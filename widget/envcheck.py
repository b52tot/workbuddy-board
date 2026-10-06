#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""运行环境自检。

exe 拷到别的机器上能不能跑，**只有一个硬依赖：WebView2 运行时**
（pywebview 的 EdgeChromium 后端要用它渲染界面）。

其它依赖都已经解决掉：
  · Python 本身     —— 打进 exe
  · VC++ 运行库     —— 产物自带 VCRUNTIME140.dll 与 api-ms-win-* 全套
                       （实测确认，目标机不需要单独装）
  · .NET            —— **不需要**（这个方案从头到尾没用 .NET）

所以这里的职责很窄：**查 WebView2，没有就给出能照着做的指引**，
而不是让程序在启动时抛一句看不懂的 dll 错误。
"""
from __future__ import annotations

import os
import sys

# WebView2 Evergreen 运行时的固定 GUID
WV2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WV2_DOWNLOAD = "https://developer.microsoft.com/microsoft-edge/webview2/"

# 三个可能的登记位置：64 位机器上通常落在 WOW6432Node
_WV2_KEYS = (
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\%s" % WV2_GUID,
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients\%s" % WV2_GUID,
)


def webview2_version() -> str | None:
    """返回 WebView2 运行时版本号；没装返回 None。"""
    try:
        import winreg
    except ImportError:
        return None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in _WV2_KEYS:
            try:
                with winreg.OpenKey(root, sub) as k:
                    v, _ = winreg.QueryValueEx(k, "pv")
                    if v and v not in ("", "0.0.0.0"):
                        return str(v)
            except Exception:
                continue
    # 有些环境注册表没写，但文件在 —— 退一步看安装目录
    for p in (
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\EdgeWebView\Application"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\EdgeWebView\Application"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\EdgeWebView\Application"),
    ):
        if os.path.isdir(p):
            subs = [d for d in os.listdir(p) if d[:1].isdigit()]
            if subs:
                return max(subs)
    return None


def windows_ok() -> tuple[bool, str]:
    """WebView2 要求 Win7+，但实际 Win10 才稳。这里只做粗判，给提示用。"""
    if sys.platform != "win32":
        return False, "当前不是 Windows"
    try:
        v = sys.getwindowsversion()
        if v.major < 10:
            return False, "Windows %d.%d 版本偏低" % (v.major, v.minor)
        return True, "Windows %d.%d build %d" % (v.major, v.minor, v.build)
    except Exception:
        return True, "无法判断 Windows 版本"


def report() -> dict:
    """给程序启动时调用；也给"自检"入口用。"""
    v = webview2_version()
    ok, osdesc = windows_ok()
    return {
        "os": osdesc,
        "os_ok": ok,
        "webview2": v,
        "webview2_ok": bool(v),
        "download": WV2_DOWNLOAD,
    }


def show_report() -> bool:
    """`--check` 用：**总是**弹一个框把检测结果说清楚。

    为什么不是 print：打包版是 windowed（没有控制台），
    print 出去用户根本看不见 —— 那等于什么都没做。
    """
    r = report()
    if r["webview2_ok"]:
        msg = ("环境检查通过，可以运行。\n\n"
               "  系统     : %s\n"
               "  WebView2 : %s\n\n"
               "（Python 已打进程序，VC 运行库程序自带，都不需要额外安装）"
               % (r["os"], r["webview2"]))
        title = "任务挂件 · 环境检查通过"
        flags = 0x40000 | 0x40          # 置顶 + 信息图标
    else:
        msg = ("缺少 WebView2 运行时，挂件无法显示界面。\n\n"
               "  系统     : %s\n"
               "  WebView2 : 未检测到\n\n"
               "解决办法（二选一）：\n"
               "  1. 安装 Edge 浏览器（会自动带上 WebView2）\n"
               "  2. 直接下载 Evergreen 运行时：\n"
               "     %s\n\n"
               "装完重新运行即可。" % (r["os"], r["download"]))
        title = "任务挂件 · 缺少组件"
        flags = 0x40000 | 0x10          # 置顶 + 错误图标
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, title, flags)
    except Exception:
        sys.stderr.write(msg + "\n")
    return r["webview2_ok"]


def check_or_explain(parent=None) -> bool:
    """检查环境。缺 WebView2 时弹一个说人话的对话框并返回 False。

    为什么不直接在控制台打印：打包版是 **windowed**（没有控制台），
    print 出去用户根本看不见 —— 那等于静默失败。
    """
    r = report()
    if r["webview2_ok"]:
        return True
    return show_report()


if __name__ == "__main__":
    r = report()
    print("操作系统 :", r["os"], "" if r["os_ok"] else "（偏低）")
    print("WebView2 :", r["webview2"] or "未安装")
    print("结论     :", "可以运行" if r["webview2_ok"] else
          "缺 WebView2 —— 装 Edge 或从 %s 下载" % r["download"])
    raise SystemExit(0 if r["webview2_ok"] else 1)
