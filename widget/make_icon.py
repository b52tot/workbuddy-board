#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成图标（.ico + 各状态 PNG）。

图标设计：深色圆角方块 + 三条任务横线 + 右下角状态点。
★ 状态点用颜色区分：正常=蓝、有卡住=橙、连不上=红。
  这不是装饰 —— 托盘图标是**唯一在挂件窗口被遮挡时仍然可见**的地方，
  让它带状态，就等于给"有任务卡住了"多留了一条出口。

用法：
    python make_icon.py            # 生成 icon.ico + icon-<state>.png
"""
from __future__ import annotations

import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.stderr.write("需要 Pillow：pip install pillow\n")
    raise SystemExit(1)

HERE = os.path.dirname(os.path.abspath(__file__))

# 与 widget.css 的 dark token 保持一致，避免图标和界面两个色系
BG = (13, 17, 23, 255)        # --bg  #0d1117
BAR = (230, 237, 243, 255)    # --fg  #e6edf3
DIM = (110, 118, 129, 255)    # --fg-3 #6e7681
OK = (68, 147, 248, 255)      # --accent #4493f8
WARN = (210, 153, 34, 255)    # --warn  #d29922
BAD = (248, 81, 73, 255)      # --bad   #f85149

STATES = {"ok": OK, "stall": WARN, "off": BAD}


def draw_icon(size: int, dot=OK) -> Image.Image:
    """画一张 size×size 的图标。

    用「超采样再缩小」保证圆角和圆点边缘平滑 —— 直接在小尺寸上画会有锯齿，
    而托盘图标恰好就是 16px 这个最容易糊的尺寸。
    """
    S = size * 8                     # 超采样倍数
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底
    pad = int(S * 0.06)
    r = int(S * 0.22)
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=r, fill=BG)

    # 三条任务横线：宽度递减，像一份列表
    x0 = int(S * 0.24)
    h = int(S * 0.075)
    gap = int(S * 0.14)
    y = int(S * 0.30)
    for i, wfrac in enumerate((0.50, 0.40, 0.30)):
        d.rounded_rectangle([x0, y, x0 + int(S * wfrac), y + h],
                            radius=h // 2, fill=BAR if i == 0 else DIM)
        y += gap

    # 状态点（右下角）
    dr = int(S * 0.15)
    cx, cy = int(S * 0.755), int(S * 0.755)
    d.ellipse([cx - dr, cy - dr, cx + dr, cy + dr], fill=BG)   # 挖个底，避免与线重叠
    dr2 = int(S * 0.105)
    d.ellipse([cx - dr2, cy - dr2, cx + dr2, cy + dr2], fill=dot)

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    # .ico 里塞多档尺寸：托盘用 16/20/24，任务栏和资源管理器用大的
    sizes = [16, 20, 24, 32, 48, 64, 128, 256]
    base = draw_icon(256, OK)
    ico = os.path.join(HERE, "icon.ico")
    base.save(ico, format="ICO",
              sizes=[(s, s) for s in sizes])
    print("已生成 %s（%d 字节，%d 档尺寸）" % (ico, os.path.getsize(ico), len(sizes)))

    # 各状态的 PNG：托盘运行时按状态换图
    for name, color in STATES.items():
        p = os.path.join(HERE, "icon-%s.png" % name)
        draw_icon(64, color).save(p)
        print("  %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
