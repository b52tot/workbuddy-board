"""服务管理（svc.py）的判据测试。

背景：这两个 bug 都是「静默误判」，不测就会再次出现。
  1. `tasklist` 输出是本地代码页（中文系统 GBK），用 text=True 按 UTF-8 解码
     会抛 UnicodeDecodeError，异常被吞后误判为「无 pidfile」。
  2. 探活只看 health 200，不核对 db 路径 —— 「有东西在监听」不等于
     「用的是你要的那个库」。踩过一次：status 不带 --config 时读到默认配置，
     看到 tasks=0 以为数据丢了，其实服务用的是另一个库。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import svc  # noqa: E402


class TestPidfile(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = svc.PIDFILE
        self.tmp = Path(tempfile.mkdtemp()) / ".web.pid"
        svc.PIDFILE = self.tmp

    def tearDown(self) -> None:
        svc.PIDFILE = self._orig

    def test_missing_pidfile_returns_none(self) -> None:
        self.assertIsNone(svc._read_pid())

    def test_garbage_pidfile_returns_none(self) -> None:
        self.tmp.write_text("不是数字")
        self.assertIsNone(svc._read_pid())

    def test_stale_pid_returns_none(self) -> None:
        """不存在的 pid 必须判为「没在跑」，否则会误以为已启动而不再拉起。"""
        self.tmp.write_text("999999")
        self.assertIsNone(svc._read_pid())

    def test_live_pid_detected_across_encodings(self) -> None:
        """★ 回归：中文 Windows 下 tasklist 输出是 GBK，不能用 text=True 读。

        这里直接调 _read_pid 验证「活着的 pid 能被识别」——
        修复前会因 UnicodeDecodeError 被吞而返回 None。
        """
        p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(8)"])
        try:
            self.tmp.write_text(str(p.pid))
            got = svc._read_pid()
            self.assertEqual(got, p.pid,
                             "活着的进程必须被识别（中文系统下 tasklist 是 GBK 编码）")
        finally:
            p.terminate()
            p.wait(timeout=10)


class TestConfigProbe(unittest.TestCase):
    def test_port_from_default_config(self) -> None:
        host, port = svc._port_from_config(None)
        self.assertEqual(host, "127.0.0.1")
        self.assertIsInstance(port, int)
        self.assertTrue(1 <= port <= 65535)

    def test_db_path_is_absolute(self) -> None:
        db = svc._db_from_config(None)
        self.assertTrue(os.path.isabs(db), "db 路径必须是绝对路径才好比对")

    def test_custom_config_is_honored(self) -> None:
        """★ 配置必须真的被读到 —— 否则「检查一致性」本身就是错的。"""
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text(json.dumps({
            "board": {"db_path": "./data/__probe_test__.db"},
            "web": {"host": "127.0.0.1", "port": 18899},
        }), encoding="utf-8")
        host, port = svc._port_from_config(str(p))
        self.assertEqual(port, 18899, "自定义端口必须生效")
        db = svc._db_from_config(str(p))
        self.assertIn("__probe_test__", db, "自定义 db 必须生效")

    def test_broken_config_falls_back_without_crashing(self) -> None:
        """配置坏了不能让 status 崩掉 —— 探活本身要能给出结论。"""
        p = Path(tempfile.mkdtemp()) / "bad.json"
        p.write_text("{ not json }", encoding="utf-8")
        host, port = svc._port_from_config(str(p))
        self.assertIsInstance(port, int)


class TestProbe(unittest.TestCase):
    def test_probe_returns_false_on_dead_port(self) -> None:
        self.assertFalse(svc._probe("127.0.0.1", 1))


class TestNoUnencodedTextDecode(unittest.TestCase):
    """★ 结构性判据：svc.py 里所有 text=True 的 subprocess 调用都必须显式给 encoding。

    为什么需要这条：`text=True` 会让 subprocess 按系统默认编码（中文系统 GBK）
    解不出来时抛 UnicodeDecodeError。修 `_read_pid` 那次漏了 `cmd_stop` 里的
    `taskkill`，结果 `svc.py stop` 照样往 stderr 打 traceback ——
    **一边修一边漏**，靠肉眼逐个调用点审阅必然再漏。所以改成机械扫描整份源码。

    反向臂：把任一处 encoding= 删掉，本测试必须失败。
    """

    def test_every_text_true_subprocess_specifies_encoding(self) -> None:
        src = (ROOT / "svc.py").read_text(encoding="utf-8")
        # 逐个 subprocess 调用点检查：含 text=True 的片段里必须同时有 encoding=
        offenders: list[str] = []
        for m in re.finditer(r"subprocess\.run\((.*?)\)(?=\s*(?:\n|$))",
                             src, re.S):
            call = m.group(1)
            if "text=True" in call and "encoding=" not in call:
                offenders.append(" ".join(call.split())[:90])
        self.assertEqual(
            offenders, [],
            "以下 subprocess 调用用了 text=True 但没指定 encoding，"
            "在中文 Windows 上会抛 UnicodeDecodeError：\n  - "
            + "\n  - ".join(offenders))


if __name__ == "__main__":
    unittest.main(verbosity=2)
