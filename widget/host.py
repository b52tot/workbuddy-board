#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务挂件 · 桌面宿主。

职责边界（写清楚，免得后面职责蔓延）：
  本文件只做**宿主该做的事** —— 开一个无边框置顶窗口、把数据喂给页面、
  记住窗口状态。**不碰任务业务逻辑**：建卡/移卡/改状态一律走看板自己的
  MCP 工具，挂件只读。

数据怎么进页面：
  pywebview 的 js_api 把 Python 方法暴露成 window.pywebview.api.*，
  页面调它拿数据。这样绕开了 file:// 下的跨域限制（不用起本地 HTTP 服务）。

为什么轮询放在 JS 侧而不是 Python 侧：
  JS 有现成的 setInterval，而 Python 侧要用 evaluate_js 往页面推，
  还得处理页面没加载完的情况。挂件 2 秒一次，跨语言调用开销可接受。

用法：
    python host.py            # 正常运行
    python host.py --debug    # 打开 devtools
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

try:
    import webview
except ImportError:
    sys.stderr.write("缺少 pywebview。装：pip install pywebview\n")
    raise SystemExit(1)

def _res_dir() -> str:
    """只读资源目录（host.html / widget.js / widget.css / data*.js）。

    打包后这些被 PyInstaller 解到 sys._MEIPASS（一个临时目录）；开发时就是脚本目录。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir() -> str:
    """可写目录（数据库、配置、窗口状态）。

    两条硬要求，都是踩出来的：

    1. **不能写 _MEIPASS**：那是 PyInstaller 的临时解包目录，
       进程一退就没了 —— 表现成"位置和数据库记不住"，且不报错。
    2. **打包版也不要写在 exe 同级**：
       exe 常常放在桌面（就是给人双击用的），数据写在那里会往用户桌面
       丢出 board-config.json / data/ / state.json 一堆东西。
       实测被用户当场投诉过。
       ⇒ 统一落到 `%APPDATA%\WorkBuddyBoardWidget`，这是 Windows 上
         存应用数据的标准位置，用户看得见也找得到，不污染桌面。

       开发态（未打包）仍旧用脚本目录，方便调试。
    """
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                     "WorkBuddyBoardWidget")
    os.makedirs(d, exist_ok=True)
    return d


RES = _res_dir()
DATA = _data_dir()
HERE = RES                       # 兼容下文对 HERE 的引用
STATE_PATH = os.path.join(DATA, "state.json")
CONF_PATH = os.path.join(DATA, "host_config.json")

DEFAULTS = {
    "board_url": "http://127.0.0.1:8791",
    # 看板项目所在目录。独立版挂在桌面、项目在别处时，填这里挂件才能自动拉起服务
    "board_root": "",
    # 署名（显示在底栏）。留空则不显示。
    "signature": "保大",
    "width": 320,
    "height": 470,
    "alpha": 1.0,
    # ★ 主题名与 host.html 的 CFG 保持一致（theme-amber）。
    #   原来这里是 "theme-a" —— 那是已取消的「布局」时代的名字，
    #   全靠 host.html 的 OLD_THEME 迁移表兜住才没出事。
    #   靠下游兜底活着是不行的：哪天迁移表被删，挂件就挂在一个不存在的 class 上。
    "theme": "theme-amber",
    # ★ 字号必须在这里和 save_state 白名单里都有，否则面板上调完重启就丢。
    "font": 13,
    "on_top": True,
    "x": None,
    "y": None,
}


def load_json(path: str, fallback: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        out = dict(fallback)
        out.update(d or {})
        return out
    except Exception:
        return dict(fallback)


def save_json(path: str, data: dict) -> None:
    """原子写：先写临时文件再替换，避免断电/被杀时留下半个 JSON。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


def _find_pythonw() -> str:
    """打包版里 sys.executable 指向挂件 exe，不能用来跑 svc.py。
    找一个真的 Python：优先环境变量，其次常见安装位置，最后退回 PATH 里的 pythonw。"""
    for env in ("WORKBUDDY_PYTHON", "PYTHONW"):
        v = os.environ.get(env)
        if v and os.path.isfile(v):
            return v
    cands = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"),
        r"C:\Python313\pythonw.exe",
        r"C:\Python312\pythonw.exe",
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    import shutil
    return shutil.which("pythonw") or shutil.which("python") or "pythonw"


class BoardApi:
    """暴露给页面的接口（window.pywebview.api.*）。

    约定：所有方法都**不许抛异常到 JS** —— JS 那边拿到异常会变成
    unhandled rejection，页面就白屏了。失败一律返回带 ok=False 的对象。
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._etag = None          # 上次响应的 ETag，用于条件请求
        self._bundle = None        # 上次的完整数据（304 时直接复用）
        self._store = None         # 进程内数据库访问对象（懒加载）
        self._store_lock = threading.Lock()
        # ★★ 必须是下划线私有属性 —— 这不是风格问题，是**功能性问题**。
        #
        #  pywebview 用 `webview/util.py::get_functions(window._js_api)` 构建 JS 桥接：
        #      for name in dir(obj):
        #          if name.startswith('_'): continue      # ← 私有属性被跳过
        #          ...
        #          elif isinstance(attr, object) and not callable(attr) ...:
        #              get_functions(attr, full_name, functions)   # ← 否则递归下钻
        #
        #  只要这里叫 `self.window`（公开），桥接构建器就会顺着它钻进 pywebview 的
        #  Window → `.native` → .NET WinForms 控件树，撞上 `Rectangle.Empty` 这种
        #  **自引用属性**（Empty 返回的还是 Rectangle）⇒ 无限递归爆栈：
        #      [pywebview] Error while processing
        #        window.native.AccessibilityObject.Bounds.Empty.Empty.Empty...:
        #        maximum recursion depth exceeded
        #  后果是 `window.pywebview` 永远注入不进去 ⇒ 页面 JS 一行都不执行，
        #  日志停在 `[py] shown 事件`，托盘图标在、窗口也在，就是死的。
        #  （实测就是这个症状，改了名字立刻恢复。）
        #
        #  同理 `self._tray` 持有 pystray 对象，也是公开就会被下钻。
        self._window = None
        self._tray = None          # 由 main() 注入
        self.last_error = None
        # 窗口操作（拖动/缩放/置顶）的串行队列，见 _post_window_op 的说明
        self._op_lock = threading.Lock()
        self._op_pending: dict = {}
        self._op_event = threading.Event()
        self._op_thread = None
        self._watch_thread = None      # WorkBuddy 会话镜像线程

    def _push_tray_state(self, state: str) -> None:
        """把状态推给托盘图标。托盘可能没起来（缺 pystray），所以静默处理。"""
        if self._tray:
            try:
                self._tray.set_state(state)
            except Exception:
                pass

    # ------------------------------------------------------ 数据

    def _ensure_store(self):
        """懒加载数据库访问对象（进程内直读 sqlite）。

        ★ 为什么不再走 HTTP 服务：

        原先架构是「界面 → HTTP → 看板服务子进程 → sqlite」，三个环节，
        任一环节出问题用户就看到「服务未启动 / 重试连接」。
        而看板数据**本来就是一个本地文件**，直接读是毫秒级的事。

        去掉 HTTP 之后：
          · 没有端口、没有子进程、没有启动时序
          · 打开即有数据，不存在"连接中/连不上"这种中间态
          · 少一层故障面

        线程安全：Store 内部是 RLock；这里再加一把锁防重复构造。
        """
        if self._store is not None:
            return self._store
        with self._store_lock:
            if self._store is not None:
                return self._store

            # 开发态：board/ server/ 在上一级目录，补进 sys.path
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if os.path.isdir(os.path.join(root, "board")) and root not in sys.path:
                sys.path.insert(0, root)

            from board.config import load_config
            from board.store import Store

            # 配置放在可写目录；db_path 相对该文件解析
            cfgp = os.path.join(DATA, "board-config.json")
            if os.path.isfile(cfgp):
                try:
                    _c = json.load(open(cfgp, encoding="utf-8"))
                    _d = ((_c.get("board") or {}).get("db_path") or "")
                    log("沿用已存配置的库: %s" % _d)
                except Exception:
                    pass
            if not os.path.isfile(cfgp):
                os.makedirs(os.path.join(DATA, "data"), exist_ok=True)
                dbp = "./data/board.db"
                # ★ 优先用「真实库」。
                #   独立版如果一味在 %APPDATA% 新建空库，用户看到的就是
                #   "一直显示没有任务" —— 因为真实数据在项目目录里。
                #   优先级：host_config.json 的 db_path > 现存的项目库 > 自建空库
                dbp = None

                # ① 配置显式指定（最高优先）
                want = (self.cfg or {}).get("db_path")
                if want and os.path.isfile(want):
                    dbp = os.path.abspath(want).replace("\\", "/")
                    log("数据源=配置指定: %s" % dbp)

                # ② exe 同级 / 上级目录（开发态：widget/ 的上一级就是项目根）
                if dbp is None:
                    for base in (os.path.dirname(os.path.abspath(__file__)),
                                 os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
                        cand = os.path.join(base, "data", "board.db")
                        if os.path.isfile(cand) and os.path.getsize(cand) > 8192:
                            dbp = os.path.abspath(cand).replace("\\", "/")
                            log("数据源=同级/上级项目库: %s" % dbp)
                            break

                # ③ 自动搜索 WorkBuddy 项目目录
                #    ★ 独立版常放在桌面，而看板项目在 WorkBuddy 的工作区里。
                #      写死路径不可靠（日期目录会变），所以按目录名搜。
                if dbp is None:
                    import glob
                    pats = []
                    for home in (os.path.expanduser("~"), os.environ.get("USERPROFILE", "")):
                        if not home:
                            continue
                        pats += [
                            os.path.join(home, "WorkBuddy", "*", "*", "data", "board.db"),
                            os.path.join(home, "WorkBuddy", "*", "data", "board.db"),
                            os.path.join(home, "WorkBuddy", "*", "*", "*", "data", "board.db"),
                        ]
                    found = []
                    for pat in pats:
                        found += glob.glob(pat)
                    # 只认"有内容"的库（>8KB —— 空库只有 4KB）
                    found = [f for f in found if os.path.getsize(f) > 8192]
                    if found:
                        found.sort(key=os.path.getmtime, reverse=True)   # 最近改动的优先
                        dbp = os.path.abspath(found[0]).replace("\\", "/")
                        log("数据源=自动搜索到 %d 个候选，选最新的: %s" % (len(found), dbp))
                        for f in found[:5]:
                            log("    候选: %s (%d 字节)" % (f, os.path.getsize(f)))

                if dbp is None:
                    dbp = "./data/board.db"
                    log("数据源=未找到现成库，将在 %s 新建空库" % DATA)
                    self.dbg_no_source = True
                else:
                    self.dbg_no_source = False

                with open(cfgp, "w", encoding="utf-8") as f:
                    json.dump({
                        "_comment": "由挂件自动生成。db_path 相对本文件所在目录解析。",
                        "board": {"db_path": dbp},
                        "web": {"host": "127.0.0.1", "port": 8791,
                                "open_browser": False, "allow_write": True},
                    }, f, ensure_ascii=False, indent=2)
            os.environ["WBB_CONFIG"] = cfgp

            self._store = Store(load_config())
            log("数据库已打开: %s" % getattr(self._store, "db_path", "?"))
            # ★ 启动时跑一次归档（默认行为）。
            #   触发点放在这里是刻意的：运行中定期归档是静默的，用户既不知道
            #   它跑没跑、也看不到它干了什么；放到启动时，重启一次=清一次，
            #   时机由用户掌握、结果立刻可见。
            try:
                r = self._store.archive_on_start()
                log("启动归档: %s" % r)
            except Exception:
                log_exc("archive_on_start")
            # 自动镜像 WorkBuddy 的会话（见 board/workbuddy_watch.py）
            self.start_watch()
            return self._store

    def get_board(self) -> dict:
        """读一次看板快照。**没有网络、没有子进程、没有端口**。

        失败时也必须返回一个**可用的空看板**（四列、零任务），
        而不是错误态 —— 用户要的是"打开就能用"，
        不应该因为读库异常就只看到一句报错。
        """
        try:
            store = self._ensure_store()
            snap = store.snapshot()
            # 详情用的事件流水：按需取，失败不影响主数据
            events = {}
            try:
                for lst in (snap.get("tasks_by_column") or {}).values():
                    for t in lst:
                        if t["id"] not in events:
                            events[t["id"]] = store.list_events(
                                limit=12, task_id=t["id"])
            except Exception:
                pass
            # ★ 只在「内容真的变了」时记一行 —— 用来证明挂件确实在实时跟进，
            #   而不是只在启动时读一次。频繁刷屏没意义，所以用变化量触发。
            try:
                st = snap.get("stats") or {}
                sig = (st.get("total"), st.get("stalled"),
                       tuple(sorted((c["id"], len(v)) for c, v in
                                    ((cc, (snap.get("tasks_by_column") or {}).get(cc["id"], []))
                                     for cc in snap.get("columns", []))))
                       )
                if sig != getattr(self, "_last_sig", None):
                    self._last_sig = sig
                    log("看板内容变化 → 共 %s 个任务: %s"
                        % (st.get("total"),
                           ", ".join("%s=%d" % (k, n) for k, n in sig[2])))
            except Exception:
                pass
            return {"ok": True, "board": snap, "events": events}
        except Exception as e:
            log_exc("get_board")
            return {"ok": False, "error": str(e), "fatal": True}

    # ------------------------------------------------------ 自动镜像会话
    #
    # ★ 背景：用户反复反馈「WorkBuddy 在跑任务，看板依旧显示没有任务」。
    #   根因不是挂件坏了，而是**没人往看板里写** —— 看板只会知道被写进去的东西，
    #   而"agent 会不会记得写"这件事实测靠不住（写进 AGENTS.md 也仍然靠自觉）。
    #   所以这里改成直接读 WorkBuddy 自己落盘的会话记录（只读），
    #   把"用户刚提了个请求"自动变成一条「进行中」，做完了自动转「已完成」。
    def start_watch(self) -> None:
        if getattr(self, "_watch_thread", None) is not None:
            return
        try:
            from board import workbuddy_watch as ww
        except Exception:
            log_exc("import workbuddy_watch")
            return

        def loop():
            last_note = None
            while True:
                # 间隔每次重读 —— 用户在配置里改了能立刻生效
                gap = 5.0
                try:
                    gap = float(load_json(CONF_PATH, DEFAULTS)
                                .get("board", {}).get("watch_poll_sec") or 5.0)
                except Exception:
                    pass
                try:
                    store = self._store
                    # 直接用 store 自己持有的 Config —— 它和数据库是同一份配置，
                    # 不必再去猜配置文件的路径（widget 用的是 %APPDATA%，不是项目根）
                    cfg = getattr(store, "cfg", None)
                    if store is not None and cfg is not None:
                        r = ww.poll(store, cfg)
                        # 只在"有事发生"时记日志，避免每 5 秒刷一行
                        brief = (r.get("created"), r.get("closed"),
                                 r.get("busy"), r.get("note"))
                        if brief != last_note and (r.get("created") or r.get("closed")
                                                   or not r.get("ok")):
                            log("[watch] created=%r closed=%r busy=%s note=%s"
                                % (r.get("created"), r.get("closed"),
                                   r.get("busy"), r.get("note")))
                        last_note = brief
                except Exception:
                    log_exc("workbuddy_watch loop")   # 旁路不许把挂件带崩
                time.sleep(max(1.0, gap))

        self._watch_thread = threading.Thread(target=loop, daemon=True,
                                              name="wb-watch")
        self._watch_thread.start()
        try:
            _cfg = getattr(self._store, "cfg", None)
            _root = (getattr(_cfg.board, "watch_projects_dir", "") or
                     "~/.workbuddy/projects") if _cfg else "~/.workbuddy/projects"
        except Exception:
            _root = "~/.workbuddy/projects"
        log("WorkBuddy 会话镜像已启动（只读 %s）" % os.path.expanduser(_root))

    # ------------------------------------------------------ 窗口操作队列
    #
    # ★★ 为什么所有"碰窗口"的操作都必须走这里：
    #
    #   pywebview 的 js_api 处理跑在 **Bottle 的 HTTP 线程**上，而它那几个
    #   平台函数是**直接写 WinForms 控件**的：
    #       def set_on_top(uid, on_top):
    #           BrowserView.instances.get(uid).TopMost = on_top      # ← 跨线程碰控件
    #       def resize(width, height, uid, fix_point):
    #           i.Size = ...                                          # ← 同上
    #   跨线程碰 WinForms 是不安全的：实测在设置面板里连点两次「置顶」，
    #   那个 handler **再也不返回**，而页面后续所有 IPC（连一条 log）都堵在
    #   它后面 ⇒ **整个挂件卡死**（探针逐步记录停在「置顶→取消」那一步）。
    #
    #   放到独立线程里串行执行之后：即使某次调用卡住，也只是这一个后台线程卡住，
    #   IPC 立刻返回，页面永远不冻。顺带还解决了两件事：
    #     · 拖动/拖拉会高频调用（每帧一次），串行化 + 合并以后不会堆积
    #     · 不会每次都去同步等待窗口管理器（200% 缩放下尤其慢）
    #
    #   合并策略按语义分：
    #     · move  **必须累加**（它是相对位移，丢掉一次窗口就少走一段）
    #     · resize / on_top  **只保留最新**（中间值没有意义）
    def _post_window_op(self, name: str, payload) -> None:
        with self._op_lock:
            if name == "move":
                cx, cy = self._op_pending.get("move") or (0, 0)
                self._op_pending["move"] = (cx + payload[0], cy + payload[1])
            else:
                self._op_pending[name] = payload
        self._op_event.set()

    def start_win_ops(self) -> None:
        if self._op_thread is not None:
            return

        def loop():
            while True:
                self._op_event.wait()
                with self._op_lock:
                    pending = self._op_pending
                    self._op_pending = {}
                    self._op_event.clear()
                for name, payload in pending.items():
                    try:
                        if name == "move":
                            self._apply_move(payload[0], payload[1])
                        elif name == "resize":
                            self._apply_resize(payload[0], payload[1])
                        elif name == "on_top":
                            self._apply_on_top(payload)
                    except Exception:
                        log_exc("win_op/" + name)

        self._op_thread = threading.Thread(target=loop, daemon=True,
                                           name="win-ops")
        self._op_thread.start()
        log("窗口操作线程已启动")

    def _apply_move(self, dx: int, dy: int) -> None:
        if self._window is None:
            return
        cx, cy = int(self._window.x), int(self._window.y)
        self._window.move(cx + int(dx), cy + int(dy))
        self.cfg["x"], self.cfg["y"] = cx + int(dx), cy + int(dy)

    def _apply_resize(self, w: int, h: int) -> None:
        if self._window is None:
            return
        self._window.resize(int(w), int(h))

    def _apply_on_top(self, flag: bool) -> None:
        # ★ 先走 Win32：不碰 WinForms，也就不存在跨线程问题
        if _win32_set_topmost("任务挂件", flag):
            log("set_on_top=%s via win32" % flag)
            return
        try:
            from webview.platforms import winforms as _wf
            _wf.set_on_top(self._window.uid, flag)
            log("set_on_top=%s via pywebview" % flag)
        except Exception as e:
            log("set_on_top=%s 失败: %s: %s" % (flag, type(e).__name__, e))

    # ------------------------------------------------------ MCP 注册
    def mcp_status(self) -> dict:
        """给设置面板用：当前 MCP 注册成什么样了（只读，不改任何东西）。"""
        try:
            st = mcp_status()
            st["exe"] = mcp_entry_for_this_exe()["command"]
            st["steps"] = mcp_steps_text()
            return st
        except Exception as e:
            log_exc("mcp_status")
            return {"error": str(e)}

    def install_mcp(self) -> dict:
        """设置面板上那个「注册到 WorkBuddy」按钮。

        ★ 只写 mcp.json，**不做重启、也不代替用户点信任** —— 那两步是
          WorkBuddy 侧的、必须由用户完成。所以这里把后续步骤一起带回去，
          由界面直接摆给用户看，而不是"写完了什么都不说"。
        """
        try:
            r = install_mcp_entry(apply=True)
            r["steps"] = mcp_steps_text()
            r["status"] = mcp_status()
            return r
        except Exception as e:
            log_exc("install_mcp")
            return {"ok": False, "error": str(e)}

    def set_on_top(self, flag=True) -> dict:
        """切换「始终在最前」。

        ★ 立刻返回，真正的窗口操作丢给后台线程去做。
          原因见 _post_window_op 的说明：pywebview 的平台函数跨线程写 WinForms，
          实测会把 IPC 线程堵死 ⇒ 整个挂件卡死。
        """
        flag = bool(flag)
        self.cfg["on_top"] = flag
        try:
            save_json(STATE_PATH, self.cfg)
        except Exception:
            pass
        self._post_window_op("on_top", flag)
        return {"ok": True, "on_top": flag, "queued": True}

    def hide_to_tray(self) -> dict:
        """最小化到托盘（隐藏窗口，托盘图标仍在）。

        与「收起」（mini 视图）是两回事：
          · 收起 = 窗口还在，只是变矮，仍占屏幕
          · 最小化 = 窗口隐藏，只留托盘图标，屏幕上完全让开
        用户明确要的是后者 —— 顶栏得有个入口，不能只能靠托盘。
        """
        try:
            if self._window is not None:
                self._window.hide()
            if self._tray:
                self._tray.set_visible(False)
            return {"ok": True}
        except Exception:
            log_exc("hide_to_tray")
            return {"ok": False}

    def move_window(self, dx=0, dy=0) -> dict:
        """按位移拖动窗口。

        ★ 为什么自己实现，而不是用 pywebview 自带的两种方式：
          · `easy_drag=True` —— 让**整窗**可拖，于是点击卡片被当成拖动，
            详情页永远打不开；而且不报错，只表现为"点了没反应"。
          · `draggable="..."` —— 查了源码，它的签名其实是 **bool**
            （`window.py: draggable: bool = False`），
            我一度传了选择器字符串，等于传了个真值，语义完全不是"只有
            这个元素可拖"。所以顶栏拖不动，根子在这里。

        自己实现最可控：只有顶栏触发，位移累加下发，与点击互不干扰。

        ⚠️ 只能在 webview.start() 之后调用 —— window 的位置属性
           在 start() 之前读取会死锁（实测踩过）。

        ★ 同样**立刻返回**，实际位移交给后台线程（见 _post_window_op）。
          拖动是每帧一次的调用，串行化 + 累加合并之后既不会堆积，
          也不会因为某次调用慢而把页面拖住。
        ⚠️ 真正读位置/移动在 `_apply_move` 里做，且只在那一个线程上做 ——
           这样 window 的位置属性只被一个线程碰，不存在读写竞态。
        """
        if self._window is None:
            return {"ok": False, "error": "no window"}
        try:
            self._post_window_op("move", (int(dx), int(dy)))
        except Exception:
            log_exc("move_window")
            return {"ok": False}
        return {"ok": True, "queued": True}

    def get_config(self) -> dict:
        """页面启动时读一次配置（瞬时，不做任何 IO 重活）。

        ★ 顺带把「要不要显示 MCP 引导条」算出来。
          首次启动、又还没注册时，用户看到的是一块只会显示、不会记录的看板 ——
          而"记不进去"这件事**在界面上完全看不出来**（空列和"没接上"长得一样）。
          所以要在首次启动就把这条摆出来，而不是等用户自己发现。
        """
        return {"ok": True, "poll_ms": 2000, "poll_ms_mini": 12000,
                "signature": self.cfg.get("signature", "") or "",
                "on_top": bool(self.cfg.get("on_top", True)),
                "has_source": not getattr(self, "dbg_no_source", False),
                "show_mcp_guide": self.should_show_mcp_guide(),
                "build": build_stamp()}

    def should_show_mcp_guide(self) -> bool:
        """该不该给 MCP 注册引导？

        三个条件同时成立才显示：
          ① 没有注册到**本 exe**（注册到别处也算没接上 —— 换台机器就断）
          ② 用户没有点过「以后再说」
          ③ 探测本身没出错（读不到 mcp.json 时不该反复弹，那是另一个问题）
        """
        try:
            if self.cfg.get("mcp_guide_done"):
                return False
            st = mcp_status()
            if st.get("error"):
                return False
            return not (st.get("installed") and st.get("points_to_exe"))
        except Exception:
            log_exc("should_show_mcp_guide")
            return False

    def dismiss_mcp_guide(self) -> dict:
        """「以后再说」：记下来，别在每次启动都弹。"""
        try:
            self.cfg["mcp_guide_done"] = True
            save_json(STATE_PATH, self.cfg)
            log("用户选择稍后处理 MCP 引导")
        except Exception:
            log_exc("dismiss_mcp_guide")
        return {"ok": True}

    def _get_events(self, task_id: str, limit: int = 12) -> list:
        url = "%s/api/events?limit=%d&task_id=%s" % (
            self.cfg["board_url"].rstrip("/"), limit, urllib.parse.quote(task_id))
        with urllib.request.urlopen(url, timeout=4) as r:
            return json.loads(r.read().decode("utf-8")).get("events", [])

    # ------------------------------------------------------ 窗口状态

    def get_state(self) -> dict:
        log("[ipc] get_state 被调用")
        try:
            return {"ok": True,
                    "width": self.cfg.get("width", 320),
                    "height": self.cfg.get("height", 470),
                    "alpha": self.cfg.get("alpha", 1.0),
                    "theme": self.cfg.get("theme", "theme-amber"),
                    # ★ font 必须回传：host.html 是靠 `if (st.font) CFG.font = +st.font`
                    #   恢复字号的。这里漏了，字号就"调完即忘"。
                    "font": self.cfg.get("font", 13),
                    "on_top": self.cfg.get("on_top", True)}
        except Exception:
            log_exc("get_state")
            return {"ok": False, "error": "get_state failed"}

    def save_state(self, patch: dict) -> dict:
        """页面改了什么（字号/宽度/透明度/主题/位置）就存下来，下次启动原样恢复。

        ★ 白名单漏键 = 静默丢弃。页面那边 `api().save_state({font: ...})` 照调不误、
        返回也是 {"ok": True}，只是什么也没存 —— 表现为"调完当场生效、重启就丢"，
        而且不报错。所以这里新增任何面板项，都必须同步加进这张表。
        """
        for k in ("width", "height", "alpha", "theme", "font", "x", "y", "on_top"):
            if k in patch and patch[k] is not None:
                self.cfg[k] = patch[k]
        save_json(STATE_PATH, self.cfg)
        return {"ok": True}

    def resize(self, width: int, height: int) -> dict:
        """改窗口大小。同样立刻返回，实际缩放丢给后台线程（见 _post_window_op）。"""
        try:
            w, h = int(width), int(height)
        except Exception:
            return {"ok": False, "error": "尺寸不是整数"}
        self.cfg["width"], self.cfg["height"] = w, h
        try:
            save_json(STATE_PATH, self.cfg)
        except Exception:
            pass
        self._post_window_op("resize", (w, h))
        return {"ok": True, "queued": True}

    def set_alpha(self, a: float) -> dict:
        """透明度：**由页面用 CSS 做**（只透背景、文字保持清晰），
        Python 侧只负责记下来。窗口级 transparent 会让字也变淡，
        那样半透明状态下就彻底读不了了。"""
        try:
            self.cfg["alpha"] = max(0.35, min(1.0, float(a)))
            save_json(STATE_PATH, self.cfg)
            return {"ok": True, "alpha": self.cfg["alpha"]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------ 动作

    def open_board(self) -> dict:
        """打开浏览器里的完整看板（用于添加/编辑任务）。

        挂件自身直接读库，不需要服务；
        但**网页版看板需要一个 HTTP 端点** ⇒ 这里按需启动一个服务进程。
        只在用户主动点 ↗ 时才起，且起不来也只影响网页，不影响挂件本体。
        """
        import webbrowser
        url = self.cfg["board_url"].rstrip("/")
        if not _service_alive(url, timeout=0.5):
            _spawn_serve_process()
            for _ in range(15):                 # 最多等 3 秒
                if _service_alive(url, timeout=0.4):
                    break
                time.sleep(0.2)
        webbrowser.open(url)
        return {"ok": True}

    def start_board(self) -> dict:
        """尝试拉起看板服务。

        找 svc.py 的顺序（第一个存在的就用）：
          1. 配置里的 `board_root`（**跨目录部署时用这个**）——
             独立版挂件常常放在桌面，而看板项目在别处，必须能指定；
          2. exe 上一级目录（开发态：widget/ 的上一级就是项目根）；
          3. 环境变量 WORKBUDDY_BOARD_ROOT。

        找不到就返回**能照着做的指引**，而不是一句"失败" ——
        独立版不含 svc.py 是设计决定（体积与职责都不该带），
        但用户必须知道下一步该做什么。
        """
        import subprocess
        roots = []
        if self.cfg.get("board_root"):
            roots.append(self.cfg["board_root"])
        roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if os.environ.get("WORKBUDDY_BOARD_ROOT"):
            roots.append(os.environ["WORKBUDDY_BOARD_ROOT"])

        for root in roots:
            svc = os.path.join(root, "svc.py")
            if not os.path.isfile(svc):
                continue
            try:
                pyw = sys.executable
                if getattr(sys, "frozen", False):
                    pyw = _find_pythonw()
                subprocess.Popen([pyw, svc, "start"], cwd=root,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return {"ok": True, "root": root}
            except Exception as e:
                return {"ok": False, "error": "%s（%s）" % (e, root)}

        return {"ok": False, "error":
                "没找到看板服务（svc.py）。请在 host_config.json 里指定 "
                "\"board_root\": \"看板项目所在目录\"，"
                "或直接在那台机器上运行 svc.py start。"}

    def log(self, msg) -> dict:
        """页面调这个把内部状态写到同一个日志文件。

        页面跑在 WebView2 里，它的 console 看不到、异常也传不出来；
        让它主动上报是唯一可靠的办法。
        """
        try:
            log("[js] %s" % msg)
        except Exception:
            pass
        return {"ok": True}

    def show_window(self) -> dict:
        """从托盘把挂件重新显示出来。

        ★ 这里**必须如实回报成败**，不能像原来那样 `except: pass`。
          静默失败制造的是一个**死局**：窗口一旦被 destroy()，show() 必然抛；
          异常被吞、菜单文案却照旧翻成"隐藏挂件" ⇒ 菜单里永远没有
          "显示挂件"可点，用户只能杀进程。所以现在：失败写日志，并且
          **只有真成功了才允许调用方改菜单文案**（见 main() 里的 _set_visible）。
        """
        try:
            if self._window is None:
                return {"ok": False, "error": "no window"}
            self._window.show()
            # 隐藏再显示一轮之后，最顶层属性在部分环境会丢，重新压一次
            if self.cfg.get("on_top", True):
                self.set_on_top(True)
            return {"ok": True}
        except Exception as e:
            log("show_window 失败: %s: %s" % (type(e).__name__, e))
            return {"ok": False, "error": str(e)}

    def quit(self) -> dict:
        """真正退出挂件：存状态 → 停托盘 → 销毁窗口。

        ★ 不要把它绑到界面的 ✕ 上。✕ 的语义是"收起来"（hide_to_tray），
          绑成 quit() 就会把窗口销毁、连带把唯一的恢复入口也弄没了。
          目前只有托盘菜单的「退出」会走到这里。
        """
        log("收到退出请求：保存状态并关闭")
        try:
            if self._window is not None:
                self.cfg["x"], self.cfg["y"] = int(self._window.x), int(self._window.y)
        except Exception:
            pass                        # 窗口已经没了也要把状态存下去
        try:
            save_json(STATE_PATH, self.cfg)
        except Exception:
            log_exc("quit/save_state")
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        threading.Timer(0.15, lambda: self._window.destroy()).start()
        return {"ok": True}


def clamp_to_screen(x, y, w, h):
    """把窗口位置夹回可见区域。

    ★ 必要：显示器拔掉/分辨率变了之后，上次存的坐标可能落在屏幕外 ——
    窗口起来了但看不见，表现为"挂件没启动"。这种失效极难自查。
    """
    try:
        import ctypes
        u = ctypes.windll.user32
        u.SetProcessDPIAware()
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    except Exception:
        return x, y
    if x is None or y is None:
        return None, None
    nx = min(max(int(x), vx), vx + vw - max(60, w // 2))
    ny = min(max(int(y), vy), vy + vh - 40)
    return nx, ny


LOG_PATH = None


def log(msg: str) -> None:
    """落盘日志。打包版无控制台，print 等于没写 —— 必须落盘。"""
    global LOG_PATH
    try:
        if LOG_PATH is None:
            LOG_PATH = os.path.join(DATA, "host.log")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s  %s" % (datetime.now().strftime("%H:%M:%S.%f")[:-3], msg) + os.linesep)
            f.flush()
    except Exception:
        pass


def log_exc(where: str) -> None:
    """把异常连同堆栈写进日志。

    ★ 必须有这个：上一轮日志停在「看板服务: 就绪」，
      之后的 `log("窗口已创建…")` 从未执行 —— 说明异常发生在
      `webview.create_window()` 附近，而**没被捕获 ⇒ 堆栈直接丢了**。
      取证通道光记录"走到了哪"不够，还得记录"为什么没走下去"。
    """
    import traceback
    try:
        log("!! %s 异常" % where)
        log(traceback.format_exc())
    except Exception:
        pass


_EXIT_LOGGED = False


def log_exit(reason: str) -> None:
    """记一条「进程正在结束」。

    ★ 这是整条诊断链的**合拢点**，也是唯一能区分「谁关的」与「被杀的」的判据：

      日志里**有「=== 启动 ===」却没有对应的「=== 进程结束 ===」**
      ⇒ 进程是被**强制终止**的（TerminateProcess / 原生崩溃），
        因为 atexit 在那种情况下根本不会执行。

    为什么必须补这条：这次挂件忽然消失，日志停在正常的 2 秒轮询上，
    既没有任何异常、也没有退出痕迹 —— 于是**既证明不了是崩溃、
    也证明不了是人为关闭**，只能去翻系统事件日志反推。
    取证通道只记「走到了哪」不够，还得记「是怎么结束的」。

    ★ 只记一次：quit() → destroy() → atexit 会连着触发，
      重复写会让人误以为退出了两回。
    """
    global _EXIT_LOGGED
    if _EXIT_LOGGED:
        return
    _EXIT_LOGGED = True
    log("=== 进程结束 === 原因=%s pid=%d" % (reason, os.getpid()))


def _thread_excepthook(args) -> None:
    """后台线程里的未捕获异常。

    ★ 挂件有会话镜像、窗口操作等好几个后台线程。它们抛异常时默认只打到
      stderr —— 而打包版是 windowed，stderr 直接进虚空。结果是线程死了、
      界面还在、某个功能悄悄不工作，没有任何线索。必须落盘。
    """
    import traceback
    try:
        log("!! 后台线程 %s 未捕获异常: %s: %s"
            % (getattr(args.thread, "name", "?"),
               getattr(args.exc_type, "__name__", args.exc_type),
               args.exc_value))
        log("".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)))
    except Exception:
        pass


threading.excepthook = _thread_excepthook


def _spawn_serve_process():
    """起一个独立的看板服务进程（`--serve`）。

    只有在用户要打开网页看板时才用得上 —— 挂件本体不需要服务。
    打包版 sys.executable 就是 exe 自己，所以**依然是单文件自包含**。
    """
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--serve"]
            cwd = os.path.dirname(os.path.abspath(sys.executable))
        else:
            cmd = [sys.executable, os.path.abspath(__file__), "--serve"]
            cwd = os.path.dirname(os.path.abspath(__file__))
        creation = (0x00000008 | 0x08000000) if os.name == "nt" else 0
        p = subprocess.Popen(cmd, cwd=cwd, creationflags=creation,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        log("已启动网页看板服务 pid=%s" % p.pid)
        return p
    except Exception:
        log_exc("_spawn_serve_process")
        return None


def _win32_set_topmost(title: str, flag: bool) -> bool:
    """Win32 兜底：直接改窗口 Z 序。

    什么时候用得上：pywebview 的平台函数在某些版本/后端下不可用
    （`webview.platforms.winforms.set_on_top` 是内部接口，不保证长期存在）。
    这时用 SetWindowPos 一样能达到目的，且不依赖 pywebview 内部结构。
    """
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, title)
        if not hwnd:
            return False
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
        u.SetWindowPos(hwnd, HWND_TOPMOST if flag else HWND_NOTOPMOST,
                       0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        return True
    except Exception:
        log_exc("_win32_set_topmost")
        return False


def _service_alive(url: str, timeout: float = 0.6) -> bool:
    try:
        urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout)
        return True
    except Exception:
        return False


def ensure_board_service(cfg: dict) -> tuple[bool, str]:
    """确保看板服务可用 —— **这是"能分发"的关键**。

    之前的错误做法：挂件只连 127.0.0.1:8791，而服务留在项目目录里没打包。
    ⇒ 拷到别的电脑上，那台机器没有 svc.py、没有 Python、没有数据库，
      挂件永远显示「连不上」。**那不叫分发，那叫半成品。**

    正确做法：**把看板服务打进 exe，启动时自己拉起来**。
    好在看板服务只用标准库（sqlite3 / http.server / json…），
    全部代码 300KB 左右，几乎不增加体积。

    顺序：
      1. 先看 127.0.0.1:8791 是否已经有人跑（比如用户自己开的服务）——
         有就用现成的，绝不抢占；
      2. 没有就在**后台线程**里起内置服务（同进程，不 fork，
         避免打包版拿 sys.executable 去跑 svc.py 那个坑）；
      3. 等它就绪（最多 10 秒）再开窗口。

    ★ 数据库必须落在**可写目录**：打包后 board/config.py 的 PROJECT_ROOT
      会指向 _MEIPASS（临时解压目录），相对路径的 db 会被写进临时目录、
      进程退出就没了 —— 表现成"数据莫名其妙丢了"。所以这里显式给绝对路径。
    """
    url = cfg["board_url"]
    if _service_alive(url):
        return True, "external"

    try:
        from server import web_server
    except Exception as e:
        return False, "内置服务不可用：%s" % e

    data = DATA                                   # 可写目录（exe 同级或 %APPDATA%）
    dbp = os.path.join(data, "board.db")
    cfgp = os.path.join(data, "board-config.json")
    if not os.path.isfile(dbp):
        # 数据库首次运行时由 Store 自动建表
        pass
    if not os.path.isfile(cfgp):
        try:
            with open(cfgp, "w", encoding="utf-8") as f:
                json.dump({
                    # ★ 不要写 base_dir！
                    #   load_config() 的顶层只接受 board / web 两段，
                    #   而 base_dir 是**自动推导**的：`cfg.base_dir = 配置文件所在目录`
                    #   （见 board/config.py 的 load_config）。
                    #   把这个文件放在可写目录里，db_path 的相对路径自然就落在那里 ——
                    #   这正是我们想要的，不需要也不允许手写。
                    #
                    #   ⚠️ 反过来要小心：打包后 board/config.py 的 PROJECT_ROOT
                    #   指向 _MEIPASS（临时解压目录）。若配置**不在**可写目录，
                    #   相对 db_path 会被解析到临时目录，数据一退出就没了。
                    "_comment": "由挂件自动生成。db_path 相对本文件所在目录解析。",
                    "board": {"db_path": "./data/board.db"},
                    # ★ allow_write 必须为 true。
                    #   别处默认是 false（看板当纯展示用），但**自带服务的独立版不同**：
                    #   它是空库起步，而挂件本身只读，
                    #   看板页面就是这个包里唯一的管理入口 ——
                    #   若也禁写，用户拿到手就是个永远空着的窗口，没法用。
                    #   服务只监听 127.0.0.1，风险面限于本机。
                    "web": {"host": "127.0.0.1", "port": 8791,
                            "open_browser": False, "allow_write": True},
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return False, "写配置失败：%s" % e
    os.environ["WBB_CONFIG"] = cfgp

    batch = _spawn_serve_process()
    if batch is None:
        return False, "启动内置服务失败"

    # ★ 用**独立进程**起服务，不要用同进程的后台线程。
    #
    #   实测教训：同进程线程方案下，服务虽然"就绪"（探测过一次就通过），
    #   但之后 WebView2 里的 fetch 会**永久挂起** —— 连接建立了却不响应。
    #   原因是主线程正跑着 webview.start() 的消息循环并处理 IPC，
    #   后台线程拿不到 GIL，accept 了连接也处理不了。
    #   Python 侧测试一直是通的，因为那是**另一个进程** —— 差异就在这里。
    #
    #   独立进程还有个好处：服务崩了不会带走界面，反之亦然。
    for _ in range(50):                            # 最多等 10 秒
        if _service_alive(url):
            return True, "internal"
        time.sleep(0.2)
    return False, "内置服务启动超时"


def serve_only() -> int:
    """`--serve`：只当看板服务跑，不开窗口。

    一个 exe 分饰两角：
      · 不带参数   → 挂件界面（同时负责把服务拉起来）
      · `--serve`  → 纯看板服务进程
    这样打包版**依然是单文件自包含**，又能让服务与界面进程隔离。
    """
    # ★ 开发态下 board/ server/ 在**上一级**目录（项目根），
    #   而 host.py 在 widget/ 里 —— 不把项目根加进 sys.path 就 import 不到。
    #   打包版不需要这步（PyInstaller 已把两个包收进 _MEIPASS），
    #   所以这个坑只在开发态暴露：打包版正常、源码跑就报 ModuleNotFoundError。
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(root, "server")) and root not in sys.path:
        sys.path.insert(0, root)

    data = DATA
    cfgp = os.path.join(data, "board-config.json")
    dbp = os.path.join(data, "board.db")
    if not os.path.isfile(cfgp):
        try:
            with open(cfgp, "w", encoding="utf-8") as f:
                json.dump({
                    "_comment": "由挂件自动生成。db_path 相对本文件所在目录解析。",
                    "board": {"db_path": "./data/board.db"},
                    "web": {"host": "127.0.0.1", "port": 8791,
                            "open_browser": False, "allow_write": True},
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    os.environ["WBB_CONFIG"] = cfgp
    try:
        from server import web_server
        return web_server.main()
    except Exception:
        log_exc("serve_only")
        return 1


# ================================================================ MCP 注册
#
# 为什么这整块值得单独做：挂件是「看」的，MCP 是「记」的。
# 只发一个 exe 给别的机器，对方能看见看板，但 agent 一个任务也记不进去 ——
# 而"agent 把干的活记下来"才是这套东西的意义。所以注册这一步必须**有引导**，
# 不能只是把 JSON 写进去就完事：写进去之后还有两步是脚本做不到的
# （重启 WorkBuddy、在连接器界面点「信任」），不告诉用户就等于没接上。

MCP_JSON_PATH = os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")


def mcp_entry_for_this_exe() -> dict:
    """本 exe 对应的 MCP 条目。

    ★ command 用 exe 自己 + `--mcp`，而不是 python + mcp_server.py：
      这样别的机器只要拷一个 exe，不需要装 Python、也不需要那份项目目录。
    """
    exe = os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                          else os.path.abspath(__file__))
    return {
        "type": "stdio",
        "timeout": 120000,
        "command": exe,
        "args": ["--mcp"],
        "env": {"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
    }


def mcp_status() -> dict:
    """看板 MCP 当前注册成什么样了。

    不只看"有没有 board 这一项"，还要看**它指向谁**：
    指向 Python 脚本和指向本 exe 是两种状态，用户需要分得清。
    """
    entry = mcp_entry_for_this_exe()
    st = {"path": MCP_JSON_PATH, "exists": os.path.isfile(MCP_JSON_PATH),
          "installed": False, "points_to_exe": False, "command": "",
          "is_exe_mcp": False, "error": ""}
    if not st["exists"]:
        return st
    try:
        with open(MCP_JSON_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        st["error"] = "读不了 %s：%s" % (MCP_JSON_PATH, e)
        return st
    cur = (data.get("mcpServers") or {}).get("board")
    if not isinstance(cur, dict):
        return st
    st["installed"] = True
    st["command"] = str(cur.get("command") or "")
    st["is_exe_mcp"] = list(cur.get("args") or []) == ["--mcp"]
    try:
        st["points_to_exe"] = (os.path.normcase(os.path.abspath(st["command"]))
                               == os.path.normcase(entry["command"]))
    except Exception:
        pass
    return st


def install_mcp_entry(*, apply: bool = True) -> dict:
    """把本 exe 注册为 WorkBuddy 的看板 MCP server。

    ★ 只改 `mcpServers.board` 一项 —— 这个文件里通常还躺着 kali / motrix /
      fnos / firecrawl，整体覆盖等于把用户的其他工具全删了。
    ★ apply=False 时只回结果不落盘（试运行）。
    ★ 落盘前备份：写坏了能立刻回滚。
    """
    entry = mcp_entry_for_this_exe()
    old = {}
    if os.path.isfile(MCP_JSON_PATH):
        try:
            with open(MCP_JSON_PATH, encoding="utf-8") as f:
                old = json.load(f)
        except Exception as e:
            return {"ok": False, "error": "读 %s 失败：%s" % (MCP_JSON_PATH, e)}

    servers = dict(old.get("mcpServers") or {})
    before = servers.get("board")
    servers["board"] = entry
    new = dict(old)
    new["mcpServers"] = servers

    others = sorted(k for k in servers if k != "board")
    res = {"ok": True, "applied": False, "path": MCP_JSON_PATH,
           "entry": entry, "before": before, "others": others,
           "backup": "", "error": ""}

    if not apply:
        return res

    backup = MCP_JSON_PATH + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        os.makedirs(os.path.dirname(MCP_JSON_PATH), exist_ok=True)
        if os.path.isfile(MCP_JSON_PATH):
            shutil.copy2(MCP_JSON_PATH, backup)
        with open(MCP_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)
        res["applied"] = True
        res["backup"] = backup if os.path.isfile(backup) else ""
    except Exception as e:
        res["ok"] = False
        res["error"] = str(e)
    log("[mcp] 注册%s：%s" % ("完成" if res["applied"] else "试运行",
                             json.dumps({k: res[k] for k in
                                         ("ok", "applied", "path", "backup", "error")},
                                        ensure_ascii=False)))
    return res


def mcp_steps_text() -> str:
    """给用户看的后续步骤。

    ★ 这三步是脚本**做不到**的部分，缺了任何一步用户都会以为"没接上"：
      ① 重启：MCP server 是常驻进程，不重启仍在用旧配置（旧配置里甚至没有 board 项）
      ② 信任：WorkBuddy 侧要用户确认一次，信任前新会话里看不到这些工具
      ③ 验证：给一句能自证的话，免得用户对着空工具列表猜
    """
    return "\n".join([
        "接下来还需要你做三步（脚本替不了）：",
        "",
        "  1) 重启 WorkBuddy",
        "     MCP server 是常驻进程，不重启的话它还在用旧的配置。",
        "",
        "  2) 打开「连接器管理」→ 右上角「自定义连接器」→ 找到 board → 点「信任」",
        "     信任之前，新会话里是看不到看板工具的。",
        "",
        "  3) 开一个新会话验证：让 agent 调一次 list_tasks",
        "     能列出任务就说明接好了。",
    ])


def cli_install_mcp(apply: bool) -> int:
    """`--install-mcp` 的命令行入口（给人看的输出）。"""
    st = mcp_status()
    res = install_mcp_entry(apply=apply)

    L = []
    if not res["ok"]:
        L.append("注册失败：%s" % res["error"])
        print(os.linesep.join(L))
        return 3

    L.append("目标文件：%s" % res["path"])
    L.append("")
    L.append("注册前 board 项：")
    L.append("  " + (json.dumps(res["before"], ensure_ascii=False)
                     if res["before"] else "（无）"))
    if st["installed"] and st["is_exe_mcp"] and st["points_to_exe"]:
        L.append("  ↑ 已经就是本 exe 了，这次是覆盖成同样的内容")
    L.append("")
    L.append("注册后 board 项：")
    L.append(json.dumps(res["entry"], ensure_ascii=False, indent=2))
    L.append("")
    L.append("其余 %d 个 MCP 服务器保持不动：%s"
             % (len(res["others"]), ", ".join(res["others"]) or "（无）"))

    if not apply:
        L.append("")
        L.append("【试运行】没有写入任何文件。确认无误后加 --yes 落盘：")
        L.append('   "%s" --install-mcp --yes' % res["entry"]["command"])
    else:
        L.append("")
        L.append("已写入。备份：%s" % (res["backup"] or "（原文件不存在，无需备份）"))
        L.append("")
        L.append(mcp_steps_text())

    out = os.linesep.join(L)
    print(out)
    log("[install-mcp]\n" + out)
    if apply:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, out, "看板 MCP 注册", 0x40000 | 0x40)
        except Exception:
            pass
    return 0


def build_stamp() -> dict:
    """当前运行的是哪一版 —— 给界面看的。

    ★ 为什么需要它：这轮出过一次"改了没生效"的困惑 ——
      用户复制了新 exe、界面上却看不出任何差别（新功能只在未注册的**首次启动**
      才出现，而他的环境早就注册过），于是判断"桌面未更新"。
      实测：桌面那份和 dist 字节一致、md5 都是 d06fa652 —— **文件是新的，
      但"我跑的是哪一版"这个问题在界面上根本答不出来。**
      所以要有一个能一眼对上的标记：时间 + 体积，和 dist 一比就知道。
    """
    try:
        p = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
        st = os.stat(p)
        mb = st.st_size / 1024.0 / 1024.0
        return {
            "frozen": bool(getattr(sys, "frozen", False)),
            "when": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
            "mb": "%.2f" % mb,
            "text": "%s · %.2f MB%s" % (
                datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"), mb,
                "" if getattr(sys, "frozen", False) else "（源码态）"),
        }
    except Exception:
        return {"frozen": False, "when": "?", "mb": "?", "text": "?"}


def main() -> int:
    # `--serve`：当看板服务进程跑（由挂件自己拉起，也可手工启动来排查）
    if "--serve" in sys.argv:
        return serve_only()

    # `--traytest`：只测托盘链路，不开挂件窗口。
    # 用来定位"右下角没有图标"——	逐段报告：PIL 能否解码、pystray 能否创建 Icon。
    if "--traytest" in sys.argv:
        lines = []
        try:
            from PIL import Image, ImageFile
            lines.append("PIL 导入 OK: %s" % Image.__version__)
            try:
                import PIL.PngImagePlugin as _p
                lines.append("PngImagePlugin: 可导入 %s" % _p.__file__.split("\\")[-1])
            except Exception as e:
                lines.append("PngImagePlugin: 导入失败 %s" % e)
            lines.append("已注册的解码器: %s"
                         % ",".join(sorted(Image.OPEN.keys()))[:200])
        except Exception as e:
            lines.append("PIL 导入失败: %s" % e)

        import base64
        for n in ("ok", "stall", "off"):
            fp = os.path.join(RES, "icon-%s.png" % n)
            if not os.path.isfile(fp):
                lines.append("icon-%s.png: 文件不存在" % n)
                continue
            try:
                with open(fp, "rb") as f:
                    raw = f.read()
                lines.append("icon-%s.png: %d 字节, 头=%r"
                             % (n, len(raw), raw[:8]))
                im = Image.open(fp); im.load()
                lines.append("  → 解码 OK %s %s" % (im.size, im.mode))
            except Exception as e:
                lines.append("  → 解码失败 %s: %s" % (type(e).__name__, e))

        try:
            import pystray
            lines.append("pystray 导入 OK")
        except Exception as e:
            lines.append("pystray 导入失败: %s" % e)

        out = os.linesep.join(lines)
        print(out)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, out, "托盘自检", 0x40000 | 0x40)
        except Exception:
            pass
        return 0

    # ------------------------------------------------------------ --mcp
    # `BoardWidget.exe --mcp`：把这个 exe 自己当成看板的 MCP server（stdio）。
    #
    # ★ 为什么要这么做：原来的 MCP 是这样接的 ——
    #       python.exe  …\workbuddy-board\server\mcp_server.py
    #   也就是**必须有 Python、必须有那份项目目录**。而挂件本身是自包含的，
    #   于是出现一个很别扭的状态：exe 拷到别的机器能看，但 agent 没法记任务。
    #   自带 MCP 之后，"别的机器要用"就只剩两件事：拷 exe + 在 mcp.json 里加一条。
    #
    # ★ 必须放在**任何 GUI/托盘初始化之前**：MCP 是纯 stdio 的，
    #   一旦走了 webview/托盘那条路，stdout 就会被别的输出污染，
    #   JSON-RPC 直接解析失败（而且现象是"连上了但不回话"，很难查）。
    #
    # ★ 必须先把配置指到**稳定目录**：
    #   打包后 PROJECT_ROOT 落在 %TEMP%\_MEIxxxx 里，配置里的 `./data/board.db`
    #   会被解析到那个临时解包目录 —— 进程一退数据就没了。
    #   所以这里复用挂件那套"三层探测"，把配置写到 %APPDATA% 再交给 MCP。
    if "--mcp" in sys.argv:
        try:
            cfgp = os.path.join(DATA, "board-config.json")
            if not os.path.isfile(cfgp):
                # 触发挂件的三层探测并落配置（副作用就是我们要的）
                try:
                    BoardApi({})._ensure_store()
                except Exception:
                    log_exc("--mcp 探测数据源")
            os.environ["WBB_CONFIG"] = cfgp
            log("--mcp：使用配置 %s" % cfgp)
        except Exception:
            log_exc("--mcp 准备配置")
        from server import mcp_server
        return mcp_server.main()

    # ------------------------------------------------------------ --install-mcp
    # 把这个 exe 注册成 WorkBuddy 的看板 MCP（写 ~/.workbuddy/mcp.json）。
    #
    # ★ 默认**只打印不落盘**（dry-run）。这一步改的是用户的 WorkBuddy 配置：
    #   新条目要用户在连接器界面里点「信任」才会生效，在信任之前
    #   新会话里是拿不到看板工具的 —— 也就是说贸然替换很可能"当场把能用的弄不能用了"。
    #   所以默认先给人看，确认后再加 --yes 落盘（落盘前自动备份）。
    # `--mcp-guide`：只打印「怎么把 MCP 接上」的引导，不动任何文件。
    #   给"已经把 exe 拷到别的机器、接下来该干嘛"这个场景用。
    if "--mcp-guide" in sys.argv:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        st = mcp_status()
        L = ["看板 MCP 接入引导", "=" * 34, ""]
        if st["installed"] and st["is_exe_mcp"] and st["points_to_exe"]:
            L.append("当前状态：已注册，且指向本 exe。")
            L.append("")
            L.append(mcp_steps_text())
        elif st["installed"]:
            L.append("当前状态：已注册，但**不是**指向本 exe。")
            L.append("  现在是：%s" % (st["command"] or "（未知）"))
            L.append("  ⇒ 换成本机 exe：执行 %s --install-mcp --yes"
                     % mcp_entry_for_this_exe()["command"])
        else:
            L.append("当前状态：**尚未注册**。")
            L.append("")
            L.append("先执行：")
            L.append('   "%s" --install-mcp --yes' % mcp_entry_for_this_exe()["command"])
            L.append("（先不带 --yes 可以试运行，只看会写成什么，不落盘）")
            L.append("")
            L.append(mcp_steps_text())
        out = os.linesep.join(L)
        print(out)
        log("[mcp-guide]\n" + out)
        return 0

    if "--install-mcp" in sys.argv:
        # ★ 必须先把 stdout 切成 UTF-8。
        #   windowed exe 被管道接管时，stdout 默认按系统 ANSI(GBK) 编码，
        #   中文打出去就是乱码 —— 而这份输出正是给人看的确认信息，乱码等于没有。
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return cli_install_mcp(apply="--yes" in sys.argv)

    # ------------------------------------------------------------ --stresstest
    # 逐个驱动设置面板 + 收起/展开 + 拖动 + 拖拉，**每一步记一行日志**。
    #
    # ★ 为什么必须有它：有一类 bug 在无头测试里**永远测不出来** ——
    #   "某个 js_api handler 再也不返回，把 IPC 线程堵死"。
    #   无头测试用的是桩桥接（立刻返回），所以全绿；而真机上整个挂件冻住。
    #   实测踩过：连点两次「置顶」→ pywebview 跨线程写 WinForms 的 TopMost
    #   → handler 不返回 → 页面后续所有 IPC（连一条 log）全堵住。
    #   ⇒ 这种检查只能住在真机里，而且必须**增量记账**：
    #      卡住时最后一行日志就是凶手那一步。
    # `--check`：只做环境自检，不开窗口。
    # 放在最前面，因为目标机部署时最该先确认的就是这一步。
    if "--check" in sys.argv:
        try:
            import envcheck
            return 0 if envcheck.show_report() else 1
        except Exception as e:
            print("自检失败：%s" % e)
            return 1

    # `--selftest`：验证"自包含"是否成立 —— 内置服务能不能起来、
    # 数据能不能读。**不开窗口**，这样在没有图形环境时也能测。
    # 这是"拷到别的电脑能不能用"最直接的判据。
    if "--selftest" in sys.argv:
        cfg0 = load_json(CONF_PATH, DEFAULTS)
        cfg0.update(load_json(STATE_PATH, {}))
        lines = []
        lines.append("配置：%s" % CONF_PATH)
        lines.append("数据目录：%s" % DATA)
        lines.append("资源目录：%s" % RES)
        ok, how = ensure_board_service(cfg0)
        lines.append("看板服务：%s（%s）" % ("就绪" if ok else "不可用", how))
        if ok:
            try:
                url = cfg0["board_url"].rstrip("/")
                with urllib.request.urlopen(url + "/api/board", timeout=5) as r:
                    d = json.loads(r.read().decode("utf-8"))
                st = d.get("stats") or {}
                lines.append("  /api/board OK：%d 个任务（%d 个卡住）"
                             % (st.get("total", 0), st.get("stalled", 0)))
                lines.append("  列：%s" % ", ".join(
                    c.get("title", "?") for c in (d.get("columns") or [])))
                with urllib.request.urlopen(url + "/", timeout=5) as r:
                    lines.append("  看板页面：HTTP %s（%d 字节）"
                                 % (r.status, len(r.read())))
            except Exception as e:
                lines.append("  取数失败：%s" % e)
                ok = False
        out = "\n".join(lines)
        print(out)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, out, "任务挂件 · 自检",
                0x40000 | (0x40 if ok else 0x10))
        except Exception:
            pass
        return 0 if ok else 1

    # ---------------------------------------------------------- 环境自检
    # WebView2 是本方案**唯一的硬依赖**（Python 已打进 exe、VC 运行库产物自带）。
    # 先在启动前查一次并给出能照着做的指引 —— 否则用户看到的是
    # 一句 dll 加载失败，而打包版没有控制台，连这句都看不见。
    try:
        import envcheck
        if not envcheck.check_or_explain():
            return 1
    except ImportError:
        pass

    cfg = load_json(CONF_PATH, DEFAULTS)
    cfg.update(load_json(STATE_PATH, {}))       # state 覆盖 config 的默认值

    x, y = clamp_to_screen(cfg.get("x"), cfg.get("y"), cfg["width"], cfg["height"])
    api = BoardApi(cfg)

    # ---------------------------------------------------------- 自包含启动
    # ★ 先确保看板服务可用，再开窗口。
    #   这是"拷到别的电脑就能用"的前提：那台机器上什么都没有，
    #   服务必须由挂件自己带起来。
    log("=== 启动 === pid=%d argv=%s" % (os.getpid(), sys.argv[1:]))
    log("frozen=%s  exe=%s" % (getattr(sys, "frozen", False), sys.executable))
    # ★ 退出兜底：只要 Python 是**正常结束**，这条一定会落盘。
    #   反过来，日志里"有启动、没有结束"就直接说明进程是被强杀的 ——
    #   这是本次事件唯一能一刀切开的判据。
    atexit.register(log_exit, "atexit")
    log("RES=%s" % RES)
    log("DATA=%s" % DATA)
    log("配置=%s" % CONF_PATH)
    # ★ 不再启动任何服务进程。
    #   数据由界面进程直接读本地 sqlite（BoardApi._ensure_store），
    #   所以**不需要**"连接看板服务"这一步 —— 打开就是可用状态。
    #   只有用户点「↗ 打开网页看板」时才按需起 HTTP 服务（见 open_board）。

    log("准备 create_window …")
    try:
        window = webview.create_window(
        title="任务挂件",
        # ★ 优先用内联版：widget.css / widget.js 直接嵌在 HTML 里，
        #   不经过 pywebview 的静态服务 ⇒ 不可能再卡在"加载脚本"上。
        #   这一处曾反复失效（onload/onerror 都不触发，永久停在 load-js）。
        url=os.path.join(HERE, "host.inline.html" if os.path.isfile(
            os.path.join(HERE, "host.inline.html")) else "host.html"),
        js_api=api,
        width=cfg["width"], height=cfg["height"],
        x=x, y=y,
        resizable=True,
        min_size=(300, 120),
        frameless=True,          # 无边框 —— 挂件不该有标题栏
        # ★ 不能用 easy_drag=True：它让**整窗**可拖，于是点击卡片会被当成拖动，
        #   详情页永远打不开（而且不报错，只表现为"点了没反应"）。
        #   改成只让指定元素可拖 —— 顶栏拖窗口，卡片照常可点。
        easy_drag=False,
        draggable=".drag-area",
        on_top=cfg["on_top"],    # 常驻最前
        shadow=True,
        background_color="#0D1117",
        text_select=False,
        )
    except Exception:
        log_exc("webview.create_window")
        raise
    api._window = window
    # 窗口操作线程必须在这里起：它只依赖 window 对象引用，
    # 而页面一加载就会开始调 resize/on_top（早于任何其它初始化）。
    try:
        api.start_win_ops()
    except Exception:
        log_exc("start_win_ops")
    log("create_window 返回")

    # ★★ 不要在 webview.start() 之前读 window 的任何属性。
    #
    # 实测死在这里：日志停在 `window.width/height = ...` 那一行，
    # 窗口从此再也没出现过，start() 也没执行 —— 表现成"双击没反应"。
    # 原因是 pywebview 的 Window 属性会**同步查询底层 WebView2**，
    # 而 WebView2 要到 start() 才初始化 ⇒ 提前访问 = 死锁。
    # （`getattr` 给了默认值也救不了：属性真的存在，
    #   只是取值这个动作本身会阻塞，不是 AttributeError。）
    #
    # 所以这里只挂事件、不读属性。事件挂载是安全的。

    for ev in ("loaded", "shown", "closing", "closed"):
        try:
            e = getattr(window.events, ev, None)
            if e is not None:
                e += (lambda n: (lambda *a, **k: log("[py] %s 事件" % n)))(ev)
        except Exception as ex:
            log("  挂 %s 事件失败: %s" % (ev, ex))
    log("窗口事件已挂")

    # ------------------------------------------------------------ --windowtest
    # 「关掉之后还能不能显示回来」这条链路，只有**真的把窗口隐藏再显示**才验得了：
    # 托盘菜单是人点的，脚本点不到。所以做成一个自检模式，跑完整的
    # 隐藏 → 显示 → 再隐藏 循环，每一步的结果落到 host.log。
    #
    # 为什么值得单独做一个模式：这条链出过一次很严重的死局 ——
    # 界面上的 ✕ 调的是 quit()（= window.destroy()），窗口真被销毁之后，
    # 托盘的「显示挂件」调 window.show() 必然抛异常，异常又被 `except: pass`
    # 吞掉、菜单文案照旧翻成「隐藏挂件」⇒ **再也没有任何恢复入口**。
    # 现在的约定是：✕ = 隐藏到托盘，且只有真成功了才改菜单文案。
    if "--windowtest" in sys.argv:
        def _windowtest():
            import time

            def step(label, fn):
                try:
                    log("[wt] %s → %r" % (label, fn()))
                except Exception as e:
                    log("[wt] %s 抛异常: %s: %s" % (label, type(e).__name__, e))

            time.sleep(2.5)                     # 等 webview 真的起来
            log("[wt] === 开始窗口显隐循环 ===")
            step("hide_to_tray", api.hide_to_tray)
            time.sleep(1.2)
            step("show_window", api.show_window)
            time.sleep(1.2)
            step("show_window(重复调用应仍成功)", api.show_window)
            time.sleep(1.0)
            step("hide_to_tray(第二次)", api.hide_to_tray)
            time.sleep(1.0)
            log("[wt] === 循环结束，退出 ===")
            os._exit(0)

        threading.Thread(target=_windowtest, daemon=True).start()

    # ------------------------------------------------------------ --quittest
    # 走**完整的正常退出路径**（api.quit() → destroy → webview 循环结束 → atexit），
    # 用来验证「退出时确实会留下日志」。
    #
    # ⚠️ 不能拿 --windowtest 代替：它最后是 os._exit(0)，**绕过 atexit**，
    #    那样测出来的"没有结束日志"是测试自己的性质，不是被测对象的问题 ——
    #    正好会把这个用例变成恒假的装饰品。
    if "--quittest" in sys.argv:
        def _quittest():
            time.sleep(6)                       # 等 webview 真的起来
            log("--quittest: 主动调用 api.quit()")
            try:
                r = api.quit()
                log("--quittest: api.quit() 返回 %r" % (r,))
            except Exception as e:
                log("--quittest: api.quit() 抛异常 %s: %s" % (type(e).__name__, e))

        threading.Thread(target=_quittest, daemon=True).start()
    if "--stresstest" in sys.argv:
        def _stress():
            import time as _t

            def js(code):
                return window.evaluate_js(code)

            # 等页面真的加载完再动手：evaluate_js 在页面还没就绪时会失败/卡住，
            # 那样报出来的"失败"是测试自己的问题，不是被测对象的问题。
            _t.sleep(3.5)
            # 页面里的公共小工具
            js("window.__st = {app:function(){return document.getElementById('app');}};"
               "'ok'")

            STEPS = [
                ("打开设置面板", "document.getElementById('gear').onclick(); 'ok'"),
                # ★ 面板上的「宽度」已经换成「字号」（#rw → #rf）。
                #   这里留着 #rw 会让这个模式**第 3 步就崩**（null.value 抛 TypeError）。
                #   一律先判元素在不在，不在就报 'NO-EL' —— 静默跳过是测试最大的敌人。
                ("字号滑条 连续 10 次",
                 "(function(){var rf=document.getElementById('rf');"
                 "if(!rf) return 'NO-EL';"
                 "for(var k=0;k<10;k++){rf.value=10+k*0.8;rf.oninput.call(rf);}"
                 "return rf.value;})()"),
                ("字号滑条 回到 13px",
                 "(function(){var rf=document.getElementById('rf');"
                 "if(!rf) return 'NO-EL'; rf.value=13;"
                 "rf.oninput.call(rf);return rf.value;})()"),
                ("透明度→60",
                 "(function(){var ra=document.getElementById('ra');ra.value=60;"
                 "ra.oninput.call(ra);return ra.value;})()"),
                ("★置顶→取消",
                 "(function(){var r=document.getElementById('rontop');"
                 "if(!r) return 'no-el'; r.checked=false; r.onchange.call(r); return 'ok';})()"),
                ("★置顶→勾上",
                 "(function(){var r=document.getElementById('rontop');"
                 "if(!r) return 'no-el'; r.checked=true; r.onchange.call(r); return 'ok';})()"),
                # ★ 主题按钮：名字必须与面板上的 data-t 一字不差。
                #   上一版点的是 theme-a/b/c（「布局」时代的旧名），那三个按钮早就
                #   不存在了，而 `if(b) b.onclick()` 会**静默跳过**、照样 return 'ok'
                #   ⇒ 日志全绿，实际一次都没点到。改成找不到就报 'NO-EL'。
                ("主题→午夜蓝",
                 "(function(){var b=document.querySelector('#theme button[data-t=\"theme-midnight\"]');"
                 "if(!b) return 'NO-EL'; b.onclick(); return b.dataset.t;})()"),
                ("主题→琥珀石墨",
                 "(function(){var b=document.querySelector('#theme button[data-t=\"theme-amber\"]');"
                 "if(!b) return 'NO-EL'; b.onclick(); return b.dataset.t;})()"),
                ("主题→亚克力白",
                 "(function(){var b=document.querySelector('#theme button[data-t=\"theme-ink\"]');"
                 "if(!b) return 'NO-EL'; b.onclick(); return b.dataset.t;})()"),
                ("主题→前妻整容前",
                 "(function(){var b=document.querySelector('#theme button[data-t=\"theme-ex\"]');"
                 "if(!b) return 'NO-EL'; b.onclick(); return b.dataset.t;})()"),
                ("点 MCP 状态刷新（只读）",
                 "(function(){var v=document.getElementById('vmcp');"
                 "return v? v.textContent : 'no-el';})()"),
                ("关闭设置面板", "document.getElementById('gear').onclick(); 'ok'"),
                ("点「—」收起",
                 "(function(){var b=document.querySelector('[data-act=\"mini\"]');"
                 "if(!b||!b.onclick) return 'NO-BIND'; b.onclick({stopPropagation:function(){}});"
                 "return window.CUR_STATE;})()"),
                ("点「▴」展开",
                 "(function(){var b=document.querySelector('[data-act=\"expand\"]');"
                 "if(!b||!b.onclick) return 'NO-BIND'; b.onclick({stopPropagation:function(){}});"
                 "return window.CUR_STATE;})()"),
                ("再收起→展开一轮（连续）",
                 "(function(){for(var i=0;i<3;i++){"
                 "var a=document.querySelector('[data-act=\"mini\"]');"
                 "if(a&&a.onclick) a.onclick({stopPropagation:function(){}});"
                 "var b=document.querySelector('[data-act=\"expand\"]');"
                 "if(b&&b.onclick) b.onclick({stopPropagation:function(){}});}"
                 "return window.CUR_STATE;})()"),
                ("拖动顶栏（合成事件）",
                 "(function(){var t=document.querySelector('#app .widget .bar .ttl');"
                 "if(!t) return 'no-ttl';"
                 "function md(el,ty,x,y){el.dispatchEvent(new MouseEvent(ty,{bubbles:true,"
                 "cancelable:true,view:window,screenX:x,screenY:y,clientX:x,clientY:y}));}"
                 "md(t,'mousedown',1000,500); md(document,'mousemove',1040,530);"
                 "md(document,'mouseup',1040,530); return 'ok';})()"),
                ("拖拉右下角（合成事件）",
                 "(function(){var g=document.getElementById('grip'); if(!g) return 'no-grip';"
                 "function md(el,ty,x,y){el.dispatchEvent(new MouseEvent(ty,{bubbles:true,"
                 "cancelable:true,view:window,screenX:x,screenY:y,clientX:x,clientY:y}));}"
                 "md(g,'mousedown',1000,500); md(window,'mousemove',1100,560);"
                 "md(window,'mouseup',1100,560); return 'ok';})()"),
                ("最终状态", "JSON.stringify({state:window.CUR_STATE,"
                 "w:window.CFG.width,h:window.CFG.height,"
                 "boot:!!document.getElementById('boot'),"
                 "tasks:document.querySelectorAll('#app .card').length})"),
            ]

            log("[stress] === 开始（%d 步）===" % len(STEPS))
            bad = []
            for idx, (name, code) in enumerate(STEPS, 1):
                t0 = _t.time()
                try:
                    r = js(code)
                    dt = (_t.time() - t0) * 1000
                    log("[stress] %02d/%d %s → %r（%.0f ms）"
                        % (idx, len(STEPS), name, r, dt))
                    # 判据认两种"没干成"：绑定缺失（NO-BIND）、元素不存在（NO-EL）。
                    # 大小写不敏感 —— 否则 NO-EL 这种写法会从判据缝里漏过去。
                    if isinstance(r, str) and ("NO-BIND" in r or r.lower().startswith("no-")):
                        bad.append("%s(%s)" % (name, r))
                    if dt > 8000:
                        bad.append("%s 耗时 %.0f ms" % (name, dt))
                except Exception as e:
                    log("[stress] %02d/%d %s **失败** %s: %s"
                        % (idx, len(STEPS), name, type(e).__name__, e))
                    bad.append("%s(%s)" % (name, type(e).__name__))
                    break
                _t.sleep(0.35)          # 留时间给 IPC 与重绘排队

            # 收尾：页面是否还活着、轮询是否还在跑
            _t.sleep(1.5)
            try:
                alive = js("JSON.stringify({ok:true,"
                           "fstat:document.getElementById('fstat').textContent})")
                log("[stress] 收尾存活检查：%r" % (alive,))
            except Exception as e:
                log("[stress] 收尾存活检查失败（页面可能已冻住）：%s" % e)
                bad.append("收尾存活检查")

            if bad:
                log("[stress] === FAIL（%d 项）：%s ===" % (len(bad), "; ".join(bad)))
            else:
                log("[stress] === PASS（全部 %d 步）===" % len(STEPS))
            _t.sleep(0.3)
            os._exit(0 if not bad else 1)

        threading.Thread(target=_stress, daemon=True).start()



    # ------------------------------------------------------------ --eval
    # `--eval "<js>"`：在**真实的打包版**里执行一段 JS 并把返回值写进日志。
    #
    # ★ 为什么需要它：无头浏览器跑的 host.inline.html 与真机跑的是同一份页面，
    #   但**运行环境不同**（轮询、IPC、窗口尺寸、DPI）。已经出现过
    #   "无头测试全绿、真机照样坏"的情况 —— 那时只能靠猜。
    #   有了这个入口，就可以在真机里直接把状态读出来/点下去，不用猜。
    if "--eval" in sys.argv:
        _i = sys.argv.index("--eval")
        _code = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else ""
        # 支持 `--eval @脚本文件`：命令行里塞多行 JS 的转义太容易出错，
        # 而且 cmd/PowerShell/bash 三套引号规则不一样 —— 直接用文件最稳。
        if _code.startswith("@"):
            _path = _code[1:]
            try:
                with open(_path, encoding="utf-8") as _f:
                    _code = _f.read()
                log("[eval] 已从 %s 读入 %d 字符" % (_path, len(_code)))
            except Exception as _e:
                log("[eval] 读脚本失败: %s" % _e)
                return 2

        def _eval_run():
            import time
            time.sleep(float(os.environ.get("WBB_EVAL_DELAY", "3.5")))
            try:
                r = window.evaluate_js(_code)
                log("[eval] 返回: %r" % (r,))
            except Exception as e:
                log("[eval] 异常: %s: %s" % (type(e).__name__, e))
            # 留一段时间给脚本自己用 setTimeout 做"几秒之后再量一次"的探针
            # （重绘/轮询造成的问题，只有等几秒才能看出来）
            time.sleep(float(os.environ.get("WBB_EVAL_EXIT_DELAY", "7")))
            os._exit(0)

        threading.Thread(target=_eval_run, daemon=True).start()

    # ------------------------------------------------------------ 托盘
    # 挂件窗口被挡住时是看不见的，托盘是唯一始终可达的入口。
    # 托盘起不来（缺 pystray / 图标）不该影响挂件本身，所以整段容错。
    tray = None
    try:
        from tray import Tray
    except Exception:
        Tray = None

    def _set_visible(v: bool) -> None:
        """托盘回调：显示 / 隐藏挂件。

        ★ 关键约束：**只有真的成功了才改托盘菜单文案。**
          原先这里是
              try: window.show() if v else window.hide()
              except Exception: pass
              if tray: tray.set_visible(v)
          哪怕窗口已经被销毁（show() 必抛）也照样把文案翻过去，
          结果菜单显示「隐藏挂件」而窗口根本不存在 —— 用户找不到任何恢复入口。
          现在失败会把原因落到日志，并且不再谎报状态。
        """
        if v:
            r = api.show_window()
        else:
            r = api.hide_to_tray()
        ok = bool((r or {}).get("ok"))
        if ok:
            log("托盘切换可见性 visible=%s 成功" % v)
        else:
            log("托盘切换可见性失败 visible=%s: %s" % (v, (r or {}).get("error")))
        if tray and ok:
            tray.set_visible(v)

    def _quit() -> None:
        # ★ 这一整段原先**一句日志都没有** —— 而它正是托盘「退出」的唯一入口。
        #   于是"从托盘退出后日志戛然而止"这个现象，既证明不了是崩溃、
        #   也证明不了是人为关闭。现在每一步都记账，且先落「进程结束」再 destroy。
        log("【托盘·退出】被点击，开始收尾")
        try:
            if tray:
                tray.stop()
                log("  托盘已停止")
        except Exception:
            log_exc("_quit/tray.stop")
        try:
            # 只收掉我们自己拉起来的那个服务进程；
            # 若是用户已有的外部服务（external），绝不能动它
            if getattr(ensure_board_service, "_child", None) is not None:
                ensure_board_service._child.terminate()
                log("  已终止挂件自己拉起的看板服务子进程")
        except Exception:
            log_exc("_quit/kill_child")
        try:
            api.cfg["x"], api.cfg["y"] = window.x, window.y
            save_json(STATE_PATH, api.cfg)
            log("  窗口位置已保存 x=%s y=%s" % (api.cfg["x"], api.cfg["y"]))
        except Exception:
            log_exc("_quit/save_state")
        # 先落「进程结束」，再 destroy —— 万一 destroy 自己抛异常，记录也已经在盘上了
        log_exit("托盘·退出")
        try:
            window.destroy()
            log("  window.destroy() 已返回")
        except Exception:
            log_exc("_quit/window.destroy")

    # ★ 托盘这一段的每一步都要记账。
    #   之前它静默失败（start() 返回 False 或抛异常都被吞掉），
    #   结果就是"右下角没有图标"而不知道为什么。
    log("准备启动托盘 …")
    try:
        import tray as tray_mod
        log("tray 模块导入 OK（pystray=%s）" % ("有" if tray_mod.pystray else "缺失"))
    except Exception as e:
        tray_mod = None
        log("tray 模块导入失败: %s" % e)

    if Tray:
        try:
            tray = Tray(on_toggle=_set_visible,
                        on_show=lambda: _set_visible(True),
                        on_hide=lambda: _set_visible(False),
                        on_board=lambda: api.open_board(),
                        on_quit=_quit)
            api._tray = tray
            # 图标文件也要单独确认 —— 缺图时 pystray 会直接不显示
            import os as _os
            for _n in ("ok", "stall", "off"):
                _p = _os.path.join(RES, "icon-%s.png" % _n)
                log("  图标 %s: %s" % ("icon-%s.png" % _n,
                                       "在" if _os.path.isfile(_p) else "缺失"))
            ok_tray = tray.start()
            log("托盘 start() 返回: %s | 原因: %s"
                % (ok_tray, getattr(tray, "last_error", "") or "无"))
            if not ok_tray:
                tray = None
        except Exception:
            log_exc("托盘启动")
            tray = None
    else:
        log("Tray 类不可用（pystray 缺失？）")

    # ------------------------------------------------------------ 首次运行
    # 「安装后支持开机启动」：首次跑起来就把自启打开，之后不再重复写。
    # 用户随时可以从托盘菜单关掉 —— 默认开是因为这正是装它的目的。
    try:
        import autostart
        if not cfg.get("_autostart_done"):
            ok, cmd = autostart.enable()
            cfg["_autostart_done"] = True
            save_json(STATE_PATH, cfg)
            print("开机启动：%s（%s）" % ("已开启" if ok else "开启失败", cmd))
    except Exception as e:
        print("开机启动设置跳过：%s" % e)

    # 退出时记住位置，下次原样恢复
    def on_closed():
        # 窗口被 destroy（或从外部关闭）时会走到这里。
        # 记下来的意义：把「窗口真的没了」和「进程被强杀」区分开。
        log("【窗口 closed 事件】")
        log_exit("窗口 closed")
        # ★ 这里**不能**再读 window.x / window.y：走到 closed 时窗口已经销毁，
        #   pywebview 的 get_position 返回 None，解包直接 TypeError。
        #   原先这个异常被 `except: pass` 吞掉，加了日志才现形 ——
        #   它每次正常退出都会白刷一段 traceback，把真正有用的行淹掉。
        #   位置在 quit() 里已经存过，这里不需要重复。
        #   （2026-10-07 实测：--quittest 走到这里必抛）
    window.events.closed += on_closed

    debug = "--debug" in sys.argv
    log("准备 webview.start(debug=%s) …" % debug)
    try:
        webview.start(debug=debug, gui="edgechromium")
        log("webview.start 正常返回")
        log_exit("webview 循环正常退出")
    except Exception:
        log_exc("webview.start")
        raise
    return 0


if __name__ == "__main__":
    # ★ 最外层兜底：任何未捕获异常都必须落盘。
    #   上一轮就是在这里丢掉了真实堆栈，导致只能靠猜。
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        try:
            log("!!! 未捕获异常 !!!")
            log(traceback.format_exc())
        except Exception:
            pass
        # 窗口模式下没有任何可见输出，弹个框至少让人知道出事了
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, traceback.format_exc()[-1500:], "任务挂件 · 启动失败", 0x40000 | 0x10)
        except Exception:
            pass
        raise SystemExit(1)
