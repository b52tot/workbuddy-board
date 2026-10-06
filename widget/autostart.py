#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""开机自启的开关。

用注册表 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`，
不用启动文件夹的 .lnk：

  · **HKCU 不需要管理员权限**，而 HKLM 和"所有用户"的启动项需要；
  · 程序内可读写可校验，用户勾一下就生效，不用去翻文件夹；
  · .lnk 需要 Shell COM 才能可靠创建（之前踩过：属性看着对、双击没反应）。

命令怎么拼（这是最容易出错的地方）：
  · **打包版**：直接指 exe —— `"C:\\...\\BoardWidget.exe"`
  · **开发版**：必须指 `pythonw.exe` + 脚本路径。**不能用 python.exe** ——
    那会在每次开机时弹一个控制台黑框。
两种情况都要给路径**加引号**，因为路径里可能有空格或中文。
"""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "WorkBuddyBoardWidget"


def _command() -> str:
    """本次运行该用哪条命令开机启动。"""
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        return '"%s"' % exe
    # 开发态：pythonw + 本文件同目录的 host.py
    here = os.path.dirname(os.path.abspath(__file__))
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    return '"%s" "%s"' % (pyw, os.path.join(here, "host.py"))


def is_on() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            # 只判断"键在不在"是不够的：程序被挪过位置后，
            # 键还在但指向一个不存在的路径 —— 那等于开着却永远不会启动。
            return _same_target(val)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _same_target(val: str) -> bool:
    want = _command()
    if val.strip() == want:
        return True
    # 路径可能被换过引号风格，退一步只比首个可执行文件是否存在且同名
    a = val.strip('"').split('"')[0]
    b = want.strip('"').split('"')[0]
    return os.path.normcase(a) == os.path.normcase(b) and os.path.exists(a)


def enable() -> tuple[bool, str]:
    try:
        import winreg
        cmd = _command()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        return True, cmd
    except Exception as e:
        return False, str(e)


def disable() -> tuple[bool, str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
        return True, ""
    except FileNotFoundError:
        return True, "本来就没开"
    except Exception as e:
        return False, str(e)


def set_on(on: bool) -> tuple[bool, str]:
    return enable() if on else disable()


if __name__ == "__main__":
    # 便于手工排查：python autostart.py / python autostart.py off
    if len(sys.argv) > 1 and sys.argv[1] == "off":
        print(disable())
    elif len(sys.argv) > 1 and sys.argv[1] == "on":
        print(enable())
    else:
        print("当前:", "已开启" if is_on() else "未开启")
        print("将写入:", _command())
