#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""托盘图标与右键菜单。

为什么值得做：
  挂件是个无边框窗口，被别的窗口挡住时是**看不见的**。
  托盘图标是唯一始终可达的入口 —— 而且它自己带状态色
  （正常=蓝 / 有卡住=橙 / 连不上=红），等于给"有任务卡住了"多留一条出口。

线程模型：
  pywebview 的 `webview.start()` 会**阻塞主线程**跑窗口消息循环，
  所以托盘必须跑在**自己的线程**里。pystray 的 `run_detached()` 会自己开线程。

图标状态变化是**外部驱动**的：宿主每轮询一次就调 `set_state()`，
pystray 只在状态真的变了时才换图（避免每秒重绘闪烁）。
"""
from __future__ import annotations

import os
import sys
import threading

try:
    import pystray
    from PIL import Image
except ImportError:      # 托盘是增强项，缺了不该让挂件起不来
    pystray = None
    Image = None

# 与 icon-*.png 对应；颜色语义要和界面一致（停滞=橙、失败/断开=红、正常=蓝）
STATES = ("ok", "stall", "off")
TITLES = {
    "ok": "任务挂件 · 正常",
    "stall": "任务挂件 · 有任务卡住",
    "off": "任务挂件 · 看板连不上",
}


def _res_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


class Tray:
    """托盘图标。所有回调都在托盘线程里被调用，宿主侧要注意线程安全。"""

    def __init__(self, *, on_toggle, on_show, on_hide, on_board, on_quit,
                 state: str = "ok"):
        self.on_toggle = on_toggle
        self.on_show = on_show
        self.on_hide = on_hide
        self.on_board = on_board
        self.on_quit = on_quit
        self.state = state
        self.visible = True          # 挂件窗口当前是否可见
        self._icon = None
        self._imgs: dict[str, "Image.Image"] = {}
        self.last_error = ""
        self._load_err: list[str] = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------- 内部

    def _load(self, name: str):
        """读一张状态图标。

        ★ 不再静默吞异常。之前这里 `except: return None`，
        结果是"文件明明在、却报找不到图标"，完全无法归因 ——
        实际是 PIL 缺少 PNG 解码插件（打包时被裁掉了）。
        失败原因现在写进 self._load_err，一路带到日志里。
        """
        if Image is None:
            self._load_err.append("%s: PIL 未导入" % name)
            return None
        p = os.path.join(_res_dir(), "icon-%s.png" % name)
        if not os.path.isfile(p):
            self._load_err.append("%s: 文件不存在 %s" % (name, p))
            return None
        try:
            im = Image.open(p)
            im.load()          # ★ 关键：真正解码一次。
                               #   open() 是惰性的，缺解码器时它照样返回对象，
                               #   只有 load() 才会暴露"PNG 插件缺失"。
            return im
        except Exception as e:
            self._load_err.append("%s: %s: %s" % (name, type(e).__name__, e))
            return None

    def _build_menu(self):
        import pystray as P
        return P.Menu(
            P.MenuItem(lambda _: "隐藏挂件" if self.visible else "显示挂件",
                       self._toggle, default=True),
            P.MenuItem("打开完整看板", self._board),
            P.Menu.SEPARATOR,
            P.MenuItem("开机启动", self._toggle_autostart, checked=lambda _: _autostart_on()),
            P.Menu.SEPARATOR,
            P.MenuItem("退出", self._quit),
        )

    def _toggle(self, icon=None, item=None):
        self.on_toggle(not self.visible)

    def _show(self, icon=None, item=None):
        self.on_show()

    def _hide(self, icon=None, item=None):
        self.on_hide()

    def _board(self, icon=None, item=None):
        self.on_board()

    def _quit(self, icon=None, item=None):
        self.on_quit()

    def _toggle_autostart(self, icon=None, item=None):
        try:
            import autostart
        except ImportError:
            return
        autostart.set_on(not autostart.is_on())

    # ---------------------------------------------------------- 对外

    def set_visible(self, v: bool) -> None:
        """同步"窗口当前可见吗"——菜单文案据此变化。"""
        self.visible = bool(v)
        if self._icon:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def set_state(self, state: str) -> None:
        """换状态图标。state ∈ ok / stall / off。"""
        if state not in STATES or state == self.state:
            return                       # 没变就不动，避免闪烁
        if self._icon is None:
            self.state = state
            return
        img = self._imgs.get(state)
        if img is None:
            self.state = state
            return
        with self._lock:
            self.state = state
            try:
                self._icon.icon = img
                self._icon.title = TITLES[state]
            except Exception:
                pass

    def start(self) -> bool:
        """启动托盘。失败时把原因写进 last_error，而不是静默返回 False。

        （静默失败是之前那个"右下角没图标"查不出原因的直接原因。）
        """
        self.last_error = ""
        if pystray is None:
            self.last_error = "pystray 未安装"
            return False
        for s in STATES:
            img = self._load(s)
            if img is not None:
                self._imgs[s] = img
        if not self._imgs:
            self.last_error = "没有可用图标 —— " + "；".join(self._load_err)
            return False
        img = self._imgs.get(self.state) or next(iter(self._imgs.values()))
        try:
            self._icon = pystray.Icon("workbuddy-widget", img,
                                      TITLES.get(self.state, "任务挂件"),
                                      self._build_menu())
            self._icon.run_detached()    # 自己开线程，不挡 pywebview 的主循环
            return True
        except Exception as e:
            self.last_error = "%s: %s" % (type(e).__name__, e)
            self._icon = None
            return False

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass


def _autostart_on() -> bool:
    try:
        import autostart
        return autostart.is_on()
    except Exception:
        return False
