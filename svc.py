# -*- coding: utf-8 -*-
"""svc.py —— 把看板服务作为「脱离会话的独立进程」启动 / 停止 / 探活。

为什么需要它
------------
以「会话后台任务」方式启动的服务，会话一结束就被连带回收。
**stderr 里一行异常都没有** —— 这种退出最难排查：看起来像程序崩了，
实际是被外部收走了。本脚本用 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
创建真正独立的进程，并写一个 pidfile 以便管理。

用法:
    python svc.py start   [--config examples/config.custom-columns.json]
    python svc.py stop
    python svc.py status
    python svc.py restart [--config ...]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIDFILE = HERE / "data" / ".web.pid"
LOGFILE = HERE / "data" / "web.log"

# Windows: 完全脱离父进程控制台；POSIX: 自成会话
DETACHED = 0x00000008 | 0x00000200 if os.name == "nt" else 0


def _read_pid() -> int | None:
    try:
        pid = int(PIDFILE.read_text().strip())
    except Exception:
        return None
    # 校验这个 pid 确实还活着，避免「僵尸 pidfile」导致误判为已运行
    try:
        if os.name == "nt":
            # ★ 不要用 text=True：tasklist 输出是本地代码页（中文系统为 GBK），
            #   按 UTF-8 解码会抛 UnicodeDecodeError，异常被吞后误判为「无 pidfile」。
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                                 capture_output=True, timeout=10)
            blob = (out.stdout or b"").decode("utf-8", "replace") + \
                   (out.stdout or b"").decode("gbk", "replace")
            if str(pid) not in blob:
                return None
        else:
            os.kill(pid, 0)
    except Exception:
        return None
    return pid


def _probe(host: str = "127.0.0.1", port: int = 8792) -> bool:
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (host, port),
                                    timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _cfg_probe(cfg_path: str | None, expr: str) -> str:
    """在子进程里按指定配置求值一段表达式，避免本进程被环境污染。"""
    env = dict(os.environ)
    if cfg_path:
        env["WBB_CONFIG"] = cfg_path
    code = ("import sys;sys.path.insert(0,r'%s');"
            "from board.config import load_config;"
            "c=load_config();%s" % (HERE, expr))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         env=env, cwd=str(HERE), timeout=30)
    return (out.stdout or "").strip()


def _port_from_config(cfg_path: str | None) -> tuple[str, int]:
    """从配置里读 host/port，好让探活与实例对得上。"""
    s = _cfg_probe(cfg_path, "print(c.web.host, c.web.port)")
    parts = s.split()
    if len(parts) == 2:
        try:
            return parts[0], int(parts[1])
        except ValueError:
            pass
    return "127.0.0.1", 8792


def _db_from_config(cfg_path: str | None) -> str:
    """按配置算出「服务应该在用的库」的绝对路径。

    ★ 必须用 cfg.resolve_path，不能用 os.path.abspath：
    resolve_path 以**配置文件所在目录**为基准，abspath 以 **CWD** 为基准。
    改了 Store 的路径语义后这里若不同步，cmd_status 的「运行库 vs 期望库」
    一致性判据就会误报 —— 明明服务用的就是配置说的那个库，却被判成不一致。
    （`--config examples/xxx.json` 这类外部配置最容易撞上。）
    """
    return _cfg_probe(cfg_path, "print(c.resolve_path(c.board.db_path))")


def cmd_start(args) -> int:
    if _read_pid():
        print("已在运行（pid=%d）。如需重启请用 restart" % _read_pid())
        return 0

    host, port = _port_from_config(args.config)
    if _probe(host, port):
        print("端口 %d 已有服务在响应，但无 pidfile —— 可能是别处启动的实例。" % port)
        print("如确认要接管，请先手工停掉它。")
        return 1

    HERE.joinpath("data").mkdir(exist_ok=True)
    env = dict(os.environ)
    if args.config:
        env["WBB_CONFIG"] = args.config
    env["PYTHONPATH"] = str(HERE)
    # 关掉继承的 stdio，避免子进程持有父进程管道导致父进程无法退出
    log = open(LOGFILE, "ab", buffering=0)
    p = subprocess.Popen(
        [sys.executable, "-m", "server.web_server"],
        cwd=str(HERE), env=env,
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        creationflags=DETACHED if os.name == "nt" else 0,
        start_new_session=(os.name != "nt"))

    PIDFILE.write_text(str(p.pid))
    for _ in range(20):
        time.sleep(0.4)
        if _probe(host, port):
            print("已启动  pid=%d  http://%s:%d/" % (p.pid, host, port))
            print("日志: %s" % LOGFILE)
            return 0
    print("★ 启动后 8 秒内 /api/health 未就绪。请查日志: %s" % LOGFILE)
    return 1


def cmd_stop(args) -> int:
    pid = _read_pid()
    if not pid:
        print("未在运行（无有效 pidfile）")
        PIDFILE.unlink(missing_ok=True)
        return 0
    if os.name == "nt":
        # ★ 同 _read_pid：taskkill 的输出也是本地代码页（中文系统 GBK），
        #   用 text=True 而不指定编码会抛 UnicodeDecodeError。
        #   虽然在 stop 路径上这个异常不影响「进程是否被杀掉」，但它会把
        #   一行 traceback 打到 stderr，让人误以为停止操作失败了 ——
        #   这正是「噪音淹没真信号」。踩过一次。
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=15)
    else:
        os.kill(pid, 15)
    time.sleep(1.0)
    alive = _read_pid()
    print("已停止 pid=%d" % pid if not alive else "★ 进程 %d 仍在，请手工处理" % pid)
    PIDFILE.unlink(missing_ok=True)
    return 0


def cmd_status(args) -> int:
    pid = _read_pid()
    host, port = _port_from_config(args.config)
    ok = _probe(host, port)
    print("pidfile : %s" % (pid or "无"))
    print("健康探测: %s" % ("HTTP 200" if ok else "不可达"))
    print("地址    : http://%s:%d/" % (host, port))

    # ★ 关键：health 200 只证明「有东西在监听」，不证明「用的是你要的那个库」。
    #   之前就踩过：status 不带 --config 时读到默认配置，看到 tasks=0 以为数据丢了，
    #   其实服务用的是另一个库。所以这里显式核对 db 路径。
    rc = 0
    if ok:
        try:
            with urllib.request.urlopen("http://%s:%d/api/health" % (host, port),
                                        timeout=3) as r:
                live_db = json.loads(r.read().decode("utf-8")).get("db", "")
        except Exception as e:  # noqa: BLE001
            live_db = ""
            print("★ 读取 /api/health 失败: %s" % e)
            rc = 1
        try:
            expect = _db_from_config(args.config)
        except Exception:  # noqa: BLE001
            expect = ""
        if live_db and expect:
            same = os.path.normcase(os.path.abspath(live_db)) == \
                   os.path.normcase(os.path.abspath(expect))
            print("运行中的库: %s" % live_db)
            print("配置期望的库: %s" % expect)
            if not same:
                print("★ 两者不一致 —— 你看到的看板不是这个配置驱动的。"
                      "请检查 WBB_CONFIG / config.json。")
                rc = 1

    if pid and not ok:
        print("★ 进程在但服务不响应 —— 查日志: %s" % LOGFILE)
        return 1
    if ok and not pid:
        print("★ 服务在响应但无 pidfile —— 该实例不由本脚本管理")
        return 1
    return 0 if rc == 0 else 1


def cmd_restart(args) -> int:
    cmd_stop(args)
    time.sleep(0.5)
    return cmd_start(args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start", "stop", "status", "restart"])
    ap.add_argument("--config", default=None,
                    help="配置文件路径，如 examples/config.custom-columns.json")
    a = ap.parse_args()
    return {"start": cmd_start, "stop": cmd_stop,
            "status": cmd_status, "restart": cmd_restart}[a.action](a)


if __name__ == "__main__":
    sys.exit(main())
