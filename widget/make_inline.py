#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 host.html 里的 widget.css / widget.js 内联成 host.inline.html。

为什么要内联
------------
挂件是 pywebview 直接加载本地文件。走 `<link href="widget.css">` 这种外链时，
一旦路径解析出错就是**静默 404**：CSS 没了整页裸奔、JS 没了整页不动，
而且页面自己不会报错。内联之后是零外部请求，这一类失效从根上消失。

为什么必须由构建流程来生成
--------------------------
host.inline.html 是 widget.css / widget.js 的**副本**。
手工生成过一次之后，改 widget.js 就只改了源文件、没改副本 ——
打包出来的 exe 里跑的还是旧代码，而且**所有信号都是绿的**
（文件在、构建成功、日志正常），只有行为不对。
这是"内嵌副本必然陈旧"的经典陷阱。

所以把它做成构建的**第一步**，且失败就让构建失败：
宁可报错不产出，也不要产出一个跑的旧代码的包。

用法
----
    python make_inline.py            # 就地生成 host.inline.html
    python make_inline.py --check    # 只校验是否已是最新（CI/自检用）
"""
from __future__ import annotations

import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "host.html")
OUT = os.path.join(HERE, "host.inline.html")

CSS_REF = re.compile(r'<link[^>]+href=["\']widget\.css["\'][^>]*>', re.I)
# widget.js 是**动态加载**的（document.createElement('script')），不是静态标签，
# 所以内联靠 host.html 里的显式锚点，而不是去匹配 script 标签。
JS_ANCHOR = re.compile(r"[ \t]*<!--@INLINE_WIDGET_JS@-->[ \t]*\r?\n?", re.I)


def _read(name: str) -> str:
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


def _esc_close_tag(js: str) -> str:
    """把 JS 里可能出现的 `</script>` 挡掉。

    HTML 解析器遇到 `</script>` 就认为脚本结束 —— 哪怕它出现在字符串里。
    所以必须转义成 `<\\/script>`，否则 HTML 会被从中间截断、
    后半段变成正文显示出来。这是个只在"脚本里恰好提到 script 标签"时
    才触发的坑，很隐蔽。
    """
    return re.sub(r"</(script)", r"<\\/\1", js, flags=re.I)


def render() -> str:
    html = _read(SRC)
    css = _read("widget.css")
    js = _read("widget.js")

    n_css = len(CSS_REF.findall(html))
    n_js = len(JS_ANCHOR.findall(html))
    if n_css == 0 or n_js == 0:
        # ★ 宁可构建失败，也不要静默产出一个"没内联"的包 ——
        #   那种包能构建成功、能启动，只是在运行时才缺样式/缺脚本。
        raise SystemExit(
            "锚点缺失（widget.css 外链 %d 个，@INLINE_WIDGET_JS@ 锚点 %d 个）。\n"
            "内联依赖这两处，请检查 host.html 是否被改动。" % (n_css, n_js))

    # ★ 用 lambda 回填，避免 re.sub 把内容里的 \1 / \g<..> 当反向引用解析
    html = CSS_REF.sub(lambda m: "<style>\n" + css + "\n</style>", html, count=1)
    html = JS_ANCHOR.sub(
        lambda m: "<script>\n" + _esc_close_tag(js) + "\n</script>\n",
        html, count=1)
    return html


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    check = "--check" in sys.argv
    try:
        out = render()
    except SystemExit as e:
        print("[make_inline] 失败：%s" % e)
        return 2

    old = None
    if os.path.isfile(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = f.read()

    if check:
        if old is None:
            print("[make_inline] host.inline.html 不存在 —— 需要生成")
            return 1
        same = _digest(old) == _digest(out)
        print("[make_inline] %s" % ("已是最新" if same else "**已过期，需重新生成**"))
        return 0 if same else 1

    if old is not None and _digest(old) == _digest(out):
        print("[make_inline] 已是最新，未改动（%s）" % _digest(out))
        return 0

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    print("[make_inline] 已生成 host.inline.html：%d 字节（sha256[:16]=%s）%s"
          % (len(out.encode("utf-8")), _digest(out),
             "" if old is None else "（内容有变化）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
