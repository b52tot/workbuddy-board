"""配置路径锚定测试 —— 针对「CWD 不可控」这一前提的判据。

为什么单独一个文件
------------------
MCP server 由宿主进程 spawn，**CWD 由宿主决定**。于是任何「相对 CWD」的路径
解析都会变成隐性 bug：

* 读不到 config.json ⇒ 静默退回内置默认列（看着像"我的列定义丢了"）
* db_path 解析到别处 ⇒ 数据写进另一个目录（看着像"数据丢了"）

两者都**不报错**。所以判据必须是「换 CWD 后结果不变」，而不是「能跑起来」。

判据成对给出（缺一臂不算审查）
------------------------------
A 组（in-process）：
  正向 —— 配置里的相对路径相对 base_dir 解析，与 CWD 无关
  反向 —— 用旧行为（os.path.abspath，即相对 CWD）构造反事实，必须**不同**
          若两者相同，说明这条判据分辨不出对错，是装饰品

B 组（subprocess，真实换 CWD）：
  正向 —— 从无关 CWD 启动子进程，仍读到项目根 config.json
  反向 —— 临时把项目根 config.json 挪开，同一 CWD ⇒ 必须读不到
          证明「项目根候选」真在承重，而不是碰巧被别的候选命中
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


class TestRelativePathAnchoring(unittest.TestCase):
    """A 组：相对路径必须锚在 base_dir，不锚在 CWD。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.cwd_a = self.tmp / "cwd_a"
        self.cwd_b = self.tmp / "cwd_b"
        self.base = self.tmp / "cfg_dir"
        for d in (self.cwd_a, self.cwd_b, self.base):
            d.mkdir(parents=True, exist_ok=True)
        self._old = os.getcwd()

    def tearDown(self) -> None:
        os.chdir(self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg_with(self, base_dir):
        sys.path.insert(0, str(ROOT))
        from board.config import Config
        cfg = Config()
        cfg.board.db_path = "./data/board.db"
        cfg.base_dir = str(base_dir) if base_dir else None
        return cfg

    def test_relative_db_path_is_cwd_independent(self) -> None:
        """★ 正向臂：同一份配置，在任意 CWD 下解析结果必须一致。"""
        cfg = self._cfg_with(self.base)
        os.chdir(self.cwd_a)
        got_a = cfg.resolve_path(cfg.board.db_path)
        os.chdir(self.cwd_b)
        got_b = cfg.resolve_path(cfg.board.db_path)

        want = str((self.base / "data" / "board.db").resolve())
        self.assertEqual(got_a, want,
                         "在 cwd_a 下解析结果不对：%s" % got_a)
        self.assertEqual(got_b, want,
                         "在 cwd_b 下解析结果漂了：%s" % got_b)
        self.assertEqual(got_a, got_b, "同一配置在两个 CWD 下解析出了不同位置")

    def test_counterfactual_cwd_relative_does_drift(self) -> None:
        """★ 反向臂：用旧行为（相对 CWD）构造反事实 —— 必须会漂。

        若旧行为也不漂，说明上面那条正向判据分辨不出对错（装饰品）。
        这里刻意复现修复前的写法 `os.path.abspath`，验证它确实随 CWD 变化。
        """
        rel = "./data/board.db"
        os.chdir(self.cwd_a)
        old_a = os.path.abspath(rel)
        os.chdir(self.cwd_b)
        old_b = os.path.abspath(rel)

        self.assertNotEqual(
            old_a, old_b,
            "旧写法（os.path.abspath）居然不随 CWD 漂 —— 那这条判据没有承载力，"
            "必须换一个真能分辨对错的判据")

    def test_absolute_path_passes_through(self) -> None:
        """绝对路径必须原样返回 —— 配 WBB_CONFIG 指向外部配置时靠这条。"""
        cfg = self._cfg_with(self.base)
        abs_db = str((self.tmp / "elsewhere" / "x.db").resolve())
        cfg.board.db_path = abs_db
        os.chdir(self.cwd_a)
        self.assertEqual(cfg.resolve_path(cfg.board.db_path), abs_db)

    def test_store_actually_uses_resolve_path(self) -> None:
        """★ 正向臂：**Store 真正用的 db_path** 必须锚在 base_dir。

        为什么必须单独一条（踩过）：
        上面几条测的是 `cfg.resolve_path()` 本身。但要是 Store 不调用它
        （例如改回 `os.path.abspath`），resolve_path 全绿也白搭 ——
        判据落在了**不被使用的那个函数**上，属「元数据全绿但产物是错的」。
        所以这条判据钉在**真实生效的路径**上：建两个 Store，换 CWD 前后
        `store.db_path` 必须一致，且落在 base_dir 下。
        """
        sys.path.insert(0, str(ROOT))
        from board.store import Store

        cfg = self._cfg_with(self.base)
        os.chdir(self.cwd_a)
        s1 = Store(cfg)
        try:
            path_a = s1.db_path
        finally:
            s1.close()
        os.chdir(self.cwd_b)
        s2 = Store(cfg)
        try:
            path_b = s2.db_path
        finally:
            s2.close()

        want = str((self.base / "data" / "board.db").resolve())
        self.assertEqual(path_a, want,
                         "Store 在 cwd_a 下把库建到了 %s" % path_a)
        self.assertEqual(path_b, want,
                         "Store 在 cwd_b 下把库建到了 %s" % path_b)
        self.assertTrue(
            Path(path_a).exists(),
            "库文件没真的落盘到锚定位置：%s" % path_a)


class TestProjectRootCandidate(unittest.TestCase):
    """B 组：项目根候选必须真在承重（subprocess 真实换 CWD）。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.other_cwd = self.tmp / "unrelated"
        self.other_cwd.mkdir(parents=True, exist_ok=True)
        self.cfg_file = ROOT / "config.json"
        self.backup = None
        if self.cfg_file.exists():
            self.backup = self.cfg_file.read_bytes()

    def tearDown(self) -> None:
        # 恢复项目根 config.json 到测试前状态
        if self.backup is not None:
            self.cfg_file.write_bytes(self.backup)
        elif self.cfg_file.exists():
            self.cfg_file.unlink()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _probe(self) -> dict:
        """在无关 CWD 的子进程里读配置来源。"""
        code = (
            "import json,sys;"
            "sys.path.insert(0,%r);"
            "from board.config import load_config;"
            "c=load_config();"
            "print(json.dumps({'source':c.source,'base_dir':c.base_dir}))"
            % str(ROOT)
        )
        env = dict(os.environ)
        env.pop("WBB_CONFIG", None)  # 排除 env 覆盖，专测候选顺序
        r = subprocess.run([PY, "-c", code], cwd=str(self.other_cwd),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0,
                         "子进程失败：%s" % (r.stderr or "")[:400])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_config_found_from_unrelated_cwd(self) -> None:
        """★ 正向臂：项目根有 config.json 时，无关 CWD 也必须读到它。"""
        self.cfg_file.write_text('{"board":{"title":"锚定测试"}}',
                                 encoding="utf-8")
        got = self._probe()
        self.assertIsNotNone(got["source"], "无关 CWD 下读不到任何配置")
        self.assertEqual(Path(got["source"]).resolve(), self.cfg_file.resolve(),
                         "读到的不是项目根 config.json，而是：%s" % got["source"])
        self.assertEqual(Path(got["base_dir"]).resolve(), ROOT.resolve(),
                         "base_dir 没锚在项目根：%s" % got["base_dir"])

    def test_absence_of_root_config_changes_result(self) -> None:
        """★ 反向臂：把项目根 config.json 挪开 ⇒ 同一 CWD 必须读不到。

        证明「项目根候选」是承重构件，而不是碰巧被 other_cwd/config.json
        或内置默认值命中（那些路径下根本没有 config.json）。
        """
        if self.cfg_file.exists():
            self.cfg_file.unlink()
        got = self._probe()
        self.assertIsNone(
            got["source"],
            "项目根没有 config.json 却仍读到了 %s —— "
            "说明命中的是别的候选，这条反向臂没验到东西" % got["source"])
        # 兜底：没有任何配置文件时，base_dir 也必须锚在项目根
        self.assertEqual(Path(got["base_dir"]).resolve(), ROOT.resolve(),
                         "无配置兜底时 base_dir 没锚在项目根：%s" % got["base_dir"])


if __name__ == "__main__":
    unittest.main()
