"""生成主题预览页（4 套）。

★ 为什么要脚本生成，而不是手写一份 HTML 示意：
  预览里的挂件 DOM 是**真实 renderWidget 渲染出来的**（见 themedump.py），
  样式是**真实的 widget.css**，主题只覆盖 CSS 变量。
  手写一份"看起来差不多"的图，改了颜色/间距也判断不准 —— 预览必须是真身。

用法：
    python themedump.py      # 先抓一份富演示数据的 DOM
    python themepreview.py   # 再生成预览页
"""

import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(os.path.expanduser("~"), "WorkBuddy",
                   "2026-10-05-12-34-05", ".workbuddy", "tmp")
DEMO = os.path.join(TMP, "demo_dom.json")      # 富演示数据（首选）
REAL = os.path.join(TMP, "dom_dump.json")      # 真机抓的真数据（兜底）
OUT = os.path.join(TMP, "theme_preview.html")


# ---------------------------------------------------------------- 主题定义
#
# 选取标准是「**一眼能分辨**」，不是"四档深浅"：
#   午夜蓝 / 琥珀石墨 / 亚克力白  → 冷深 · 暖深 · 明浅，三重区分
#   前妻整容前                     → 绿调大图 + 玻璃卡，和前三个都不是一路
THEMES = [
    {
        "id": "t-midnight", "name": "午夜蓝", "tag": "冷 · 深色", "default": False,
        "desc": "深海军蓝底 + 亮蓝强调。最稳，贴近 Windows 11 深色 Fluent，久看不累。",
        "vars": {
            "--bg": "#0a0f1c", "--surface": "#101728", "--surface-2": "#182135",
            "--line": "#26324a", "--line-soft": "#1a2334",
            "--fg": "#e9eefc", "--fg-2": "#95a3c2", "--fg-3": "#66739a",
            "--accent": "#4f8dfd", "--ok": "#3fd07a", "--bad": "#ff6b6b",
            "--warn": "#e8b339",
            "--col-gray": "#5b6577", "--col-blue": "#4f8dfd",
            "--col-red": "#ff6b6b", "--col-green": "#3fd07a",
        },
    },
    {
        "id": "t-amber", "name": "琥珀石墨", "tag": "暖 · 深色", "default": True,
        "desc": "暖石墨棕底 + 琥珀强调。整体都调暖了（不只是强调色），工业感，久看不刺眼。",
        "vars": {
            "--bg": "#17130c", "--surface": "#221b11", "--surface-2": "#2e2416",
            "--line": "#3d3121", "--line-soft": "#2a2115",
            "--fg": "#f7f0e2", "--fg-2": "#b3a488", "--fg-3": "#857455",
            "--accent": "#f5a524", "--ok": "#4cc38a", "--bad": "#ff7a6b",
            "--warn": "#f5a524",
            "--col-gray": "#857455", "--col-blue": "#f5a524",
            "--col-red": "#ff7a6b", "--col-green": "#4cc38a",
        },
    },
    {
        "id": "t-acrylic", "name": "亚克力白", "tag": "明 · 浅色", "default": False,
        "desc": "Win11 浅色亚克力：白底 + 天蓝强调。白天、投屏、打印截图都清楚。",
        "vars": {
            "--bg": "#f4f7fb", "--surface": "#ffffff", "--surface-2": "#eef2f8",
            "--line": "#dbe2ec", "--line-soft": "#e8edf5",
            "--fg": "#16202f", "--fg-2": "#5c6779", "--fg-3": "#8b95a6",
            "--accent": "#2563eb", "--ok": "#16a34a", "--bad": "#dc2626",
            "--warn": "#d97706",
            "--col-gray": "#94a3b8", "--col-blue": "#2563eb",
            "--col-red": "#dc2626", "--col-green": "#16a34a",
        },
    },
    {
        # ★ 按用户另一条对话里的设计复刻。那边有一套自己的令牌命名（--ex-*），
        #   这里**沿用同一套命名**而不是另起一套 —— 两个会话将来合流时不会打架，
        #   也方便对照着调。（照抄实现，不是只抄外观。）
        "id": "t-exmatcha", "name": "前妻整容前", "tag": "绿调 · 大图背景", "default": False,
        "bg": True,     # 这一套要背景图（下面的 .t4-bg）
        "desc": "墨绿玻璃卡浮在大图背景上，琥珀强调。原图里那只站着吃薯片的绿恐龙整只放进背景，"
                "脸那一片（含两个大鼻孔）由面部提亮层保持清晰，其余虚化做氛围。",
        "vars": {
            # 底色：墨绿玻璃
            "--bg": "#0d1c10", "--surface": "#132619", "--surface-2": "#1b3320",
            "--line": "#2b4a2e", "--line-soft": "#1d3521",
            "--fg": "#e8f2e2", "--fg-2": "#9fb99a", "--fg-3": "#6e8768",
            # 强调：腹部黄 / 琥珀（进度条、pill 用它）
            "--accent": "#eed77b", "--warn": "#e0a53c", "--bad": "#e8735f",
            # 完成态用吉祥物身体绿
            "--ok": "#acd18b",
            "--col-gray": "#6e8768", "--col-blue": "#eed77b",
            "--col-red": "#e8735f", "--col-green": "#acd18b",
            # 服饰蓝：留给次要标记/链接（对应另一边的 --ex-chip）
            "--ex-matcha": "#acd18b", "--ex-chip": "#96bbe4",
            "--ex-ink": "#2d3013", "--ex-yolk": "#eed77b", "--ex-paper": "#ffffff",
        },
    },
]


def load(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def vars_css(vs):
    return "\n".join("  %s:%s;" % (k, v) for k, v in vs.items())


# 背景大图（第 4 套专用）。
# ★ 这里是**用 CSS 画的近似图**，不是真照片 —— 目的是先把「构图」定下来：
#   顶部一条清晰带 + 两个大鼻孔落在带子里 + 其余压暗，卡片浮在上面不遮鼻孔。
#   换成真图时只要把 .t4-bg .sky 的 background 换成 url(...) 即可，别的都不用动。
MASCOT_DATA_URI = "data:image/png;base64,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"


BG_CSS = """
/* ★ 这一层**照抄**用户另一条对话里已经做好的实现
   （outputs/exwife-mode/exwife.css 里 .theme-ex.e4 .ex-bg 那一组规则），参数一个没改。
   那套是量过素材、算过几何的：
     · 鼻孔面积 215/213，是眼睛（23/20）的十倍 ⇒ 它就是这张脸的辨识锚点
     · 全身图有几何死结（保鼻孔就必切眼睛）⇒ 另裁脸特写 mascot-facebg
     · 遮罩 x 61%~96% 是实测的五官横跨范围，不是目测
   自己重推一遍只会更差，而且会把人家踩过的坑再踩一遍。 */
.t4-bg{border-radius:inherit}
.t4-bg{position:absolute;inset:0;border-radius:12px;overflow:hidden;
  background:var(--bg);
  --bgimg:url('@URI@');
  /* 几何与遮罩取他们 e1 那一档（给 mascot-alpha 全身图调出来的）：
     --bgar 200/239、bottom 锚、脸中心 (75%,30%) 都是实测值。 */
  --bgar:.8368;
  /* ★ 改用 **top 锚**：--bgy 是相对挂件高度的百分比，挂件一高（任务多）
     整张图就被推下去，头顶那条弧线掉到卡片墙上 —— 表现是"背景没做出来"。
     他们 E4 就是为同一个原因改成 top 锚的（那边注释写明：
     bottom 锚在迷你态会把整张脸推出可视区）。 */
  --bgw:180%; --bgx:-15%; --bgtop:calc(-25px * var(--scale, 1));
  --bgblur:2px; --bgop:.70; --bgsat:1.30; --bgbri:1.08;
  --facex:75%; --facey:30%; --facerx:15%; --facery:19%; --facecore:55%;
  --faceblur:.28; --faceboost:.24; --facecon:1.18}
/* i = 虚化整图（做氛围）；b = 同几何的低虚化高对比副本，只用径向遮罩把脸透出来。
   两层几何完全一致 ⇒ 天然对齐，不需要算偏移。 */
.t4-bg i{position:absolute;z-index:0;top:var(--bgtop);right:var(--bgx);bottom:auto;
  width:var(--bgw);aspect-ratio:var(--bgar);
  background-image:var(--bgimg);background-size:contain;
  background-repeat:no-repeat;background-position:right top;
  filter:blur(var(--bgblur)) saturate(var(--bgsat)) brightness(var(--bgbri));
  opacity:var(--bgop);
  -webkit-mask-image:none;mask-image:none}
.t4-bg b{position:absolute;z-index:0;top:var(--bgtop);right:var(--bgx);bottom:auto;
  width:var(--bgw);aspect-ratio:var(--bgar);
  background-image:var(--bgimg);background-size:contain;
  background-repeat:no-repeat;background-position:right top;
  filter:blur(calc(var(--bgblur) * var(--faceblur))) saturate(var(--bgsat))
         brightness(var(--bgbri)) contrast(var(--facecon));
  opacity:calc(var(--bgop) + var(--faceboost));
  -webkit-mask-image:radial-gradient(ellipse var(--facerx) var(--facery)
    at var(--facex) var(--facey), #000 var(--facecore), transparent 100%);
  mask-image:radial-gradient(ellipse var(--facerx) var(--facery)
    at var(--facex) var(--facey), #000 var(--facecore), transparent 100%)}
/* 遮罩只压顶栏那一条：压到脸上会把脸压平，只剩两个鼻孔、读不出是张脸 */
.t4-bg::after{content:'';position:absolute;inset:0;z-index:1;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--bg) 62%, transparent) 0%,
                    color-mix(in srgb, var(--bg) 20%, transparent) 12%, transparent 24%),
    linear-gradient(0deg, color-mix(in srgb, var(--bg) 58%, transparent) 0%,
                    transparent 13%)}

/* ---- 背景模式下的"让路"规则（照抄用户那条对话的实现）----
   ★ 漏了这几条，背景图会**明明在、但看不见** ——
     因为卡片/顶栏/数据源条各自是不透明的，正好把它盖住。
     这类失效没有任何报错，只表现为"图没做出来"。作用域用 .t4-on，
     避免污染另外三套纯色主题。 */
.t4-on .card{
  background:color-mix(in srgb, var(--surface) calc(var(--bg-alpha,1) * 58%), transparent);
  /* 只做掉上/右/下三条边，border-left 留给语义色（停滞/完成） */
  border-top-color:transparent;
  border-right-color:color-mix(in srgb, var(--line) 70%, transparent);
  border-bottom-color:color-mix(in srgb, var(--line) 70%, transparent);
  backdrop-filter:blur(4px);}
.t4-on .bar{
  background:color-mix(in srgb, var(--surface) calc(var(--bg-alpha,1) * 24%), transparent);
  backdrop-filter:blur(5px);}
.t4-on .srcbar{
  background:color-mix(in srgb, var(--surface-2) calc(var(--bg-alpha,1) * 55%), transparent);}
.t4-on .ghead:hover{background:color-mix(in srgb, var(--surface-2) 45%, transparent)}
/* 底部净空：不留这条，最后一张卡片正好压在脸上，大图永远只露半张 */
.t4-on .cols2{padding-bottom:calc(26px * var(--scale, 1))}
"""


def build():
    css = io.open(os.path.join(HERE, "widget.css"), encoding="utf-8").read()

    dump = load(DEMO)
    src_note = "富演示数据（真实 renderWidget 渲染）"
    if not dump:
        dump = load(REAL)
        src_note = "真机抓取的真实数据"
    if not dump:
        raise SystemExit("没有可用的 DOM 抓取结果。先跑：\n"
                         "  python themedump.py            （富演示数据）\n"
                         "  BoardWidget.exe --eval \"@dump_dom.js\"   （真数据兜底）")

    # 用真身 markup，去掉会锁死尺寸的内联量（预览要按主题给宽度、不截断）
    app_html = dump["app"]
    for drop in ("--w: 300px;", "--maxh: 447px;", "--scale: 0.971;"):
        app_html = app_html.replace(drop, "")

    # ★ 按钮重规划（Windows 惯例）：
    #   右上角只放**窗口控制**（─ 收起 / ✕ 关闭），右下角放**应用动作**（↗ 打开 / ⚙ 设置）。
    #   原先是混着的，而且 ⤓ 与 ✕ 语义重复、✕ 又摆在右下角（Windows 用户找关闭一律去右上角）。
    old_bar = ('<span class="icobtn" data-act="board" title="在浏览器打开完整看板">↗</span>'
               '<span class="icobtn" data-act="mini" title="收起">—</span>'
               '<button class="icobtn icobtn-min" data-act="tray" '
               'title="最小化到托盘（右键托盘图标可重新显示）">⤓</button>')
    new_bar = ('<span class="icobtn winbtn" data-act="mini" title="收起为迷你条">—</span>'
               '<span class="icobtn winbtn winbtn-close" data-act="close" '
               'title="关闭（隐藏到托盘；托盘右键可退出）">✕</span>')
    if old_bar in app_html:
        app_html = app_html.replace(old_bar, new_bar)
    else:
        print("!! 顶栏按钮没匹配上，预览里还是旧按钮")

    old_foot = ('<button class="fb" id="gear" title="设置">⚙</button>' + "\n"
                + '  <button class="fb" id="quit" '
                + 'title="最小化到托盘（右键托盘图标可重新显示）">✕</button>')
    new_foot = ('<button class="fb" id="open" title="在浏览器打开完整看板">↗</button>' + "\n"
                + '  <button class="fb" id="gear" title="设置">⚙</button>')
    foot_html = dump["foot"]
    if old_foot in foot_html:
        foot_html = foot_html.replace(old_foot, new_foot)
    else:
        print("!! 底栏按钮没匹配上")

    panel = """
      <div class="panel-mock">
        <div class="pl"><span class="k">字号</span>
          <input type="range" min="10" max="18" step="0.5" value="13">
          <span class="v">13 px</span></div>
        <div class="pl"><span class="k">透明度</span>
          <input type="range" min="30" max="100" value="92">
          <span class="v">92%</span></div>
        <div class="pl"><span class="k">置顶</span>
          <span class="sw on"><i></i></span><span class="v">开</span></div>
        <div class="pl"><span class="k">主题</span>
          <span class="seg"><button>午夜蓝</button><button class="on">琥珀石墨</button>
          <button>亚克力白</button><button>前妻整容前</button></span></div>
      </div>"""

    blocks = []
    for t in THEMES:
        # ★ 背景层必须是 .widget 的**子元素**：--bgw 是百分比，
        #   按包含块宽度解析；挂到挂件外面（stage）会让图偏小偏位。
        app_html_t = app_html
        if t.get("bg"):
            app_html_t = app_html.replace(
                '<div class="widget"',
                '<div class="widget t4-on"><div class="t4-bg"><i></i><b></b></div>', 1)
            if 't4-bg' not in app_html_t:
                raise SystemExit("!! 背景层没能注入到 .widget 里")
        badge = ('<span class="badge">%s</span>' % t["name"]
                 + ('<span class="dflt">默认</span>' if t.get("default") else ""))
        if t.get("bg"):
            notes = ("<li>背景是<b>大图</b>；卡片半透明浮在上面，<b>不遮顶部清晰带</b></li>"
                     "<li>两个大鼻孔压在这条带子里 —— 它是这个模式的主角</li>"
                     "<li>强调色改用<b>腹部黄 / 琥珀</b>（进度条、告警 pill）</li>")
            stage_extra = ""
        else:
            notes = ("<li>纯色底、无背景图（和「前妻整容前」最大的区别）</li>"
                     "<li>卡片密度、进度条、标签样式四套完全一致，只换配色</li>")
            stage_extra = ""

        tpl = """
<section class="theme-block" id="@ID@">
  <header class="tb-head">
    @BADGE@
    <span class="tagline">@TAG@</span>
    <span class="desc">@DESC@</span>
  </header>
  <div class="tb-body">
    <div class="stage@STAGE_EXTRA@">
      <div class="wallpaper"></div>
      <div class="widget-host" style="--w:360px;--maxh:none">
        @APP@
        @FOOT@
      </div>
    </div>
    <div class="side">
      <div class="side-t">设置面板（改版后）</div>
      <div class="widget-host" style="--w:360px;--maxh:none">@PANEL@</div>
      <div class="notes">
        <b>这一套的特点</b>
        <ul>@NOTES@</ul>
      </div>
    </div>
  </div>
</section>"""
        reps = [("@ID@", t["id"]), ("@BADGE@", badge), ("@TAG@", t["tag"]),
                ("@DESC@", t["desc"]), ("@APP@", app_html_t), ("@FOOT@", foot_html),
                ("@PANEL@", panel), ("@NOTES@", notes),
                ("@STAGE_EXTRA@", stage_extra)]
        for k, v in reps:
            tpl = tpl.replace(k, v)
        blocks.append(tpl)

    theme_css = "\n".join(
        "#%s, .preview-chrome[data-theme='%s'] {\n%s\n}"
        % (t["id"], t["id"], vars_css(t["vars"])) for t in THEMES)

    with io.open(os.path.join(HERE, "preview_shell.html"), encoding="utf-8") as f:
        page = f.read()
    for k, v in (("@CSS@", css), ("@THEMECSS@", theme_css), ("@BGCSS@", BG_CSS),
                 ("@BLOCKS@", "\n".join(blocks)), ("@SRC@", src_note),
                 # ★ 原图 base64 也必须替换。漏了它**页面不报错、只是背景图不显示** ——
                 #   正是"看着像没做"的那一类静默失效（我这一版就漏了一次）。
                 ("@URI@", MASCOT_DATA_URI)):
        page = page.replace(k, v)

    # 残留占位符自检：漏替换必须当场炸，不能静默出一张缺东西的页
    left = [x for x in ("@CSS@", "@THEMECSS@", "@BGCSS@", "@BLOCKS@", "@SRC@",
                        "@URI@", "@APP@", "@ID@") if x in page]
    if left:
        raise SystemExit("!! 还有占位符没替换：%s" % left)
    if MASCOT_DATA_URI not in page:
        raise SystemExit("!! 第 4 套的背景原图没有被内嵌进去")

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)
    print("已生成：%s（%.1f KB，数据来源：%s）" % (OUT, len(page) / 1024, src_note))


if __name__ == "__main__":
    build()
