# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 任务挂件独立版（**自包含**）。

产物：单文件 exe，目标机**不需要装 Python、不需要装看板服务**，双击即用。

★ 为什么要把看板服务一起打进来：
  挂件只是"看"的界面，数据由看板服务提供。早先的版本只打包了界面，
  服务留在项目目录里 ⇒ 拷到别的电脑上，那台机器没有 svc.py、没有 Python、
  没有数据库 ⇒ 挂件永远显示「连不上」。**那不叫分发。**
  好在看板服务只用标准库（sqlite3 / http.server / json…），
  全量 300KB 左右，对体积几乎无影响。

几个容易漏的点，都在下面写清了原因：
  1. pywebview 不是纯 Python 包：它要 `Python.Runtime.dll`（pythonnet）
     和 `WebView2Loader.dll`，不收集就是运行时报"找不到 dll"。
  2. `tray` / `autostart` 是**本项目自己的模块**，而且是在函数体内
     import 的（为了"缺 pystray 也不影响主功能"），**静态分析看不到**
     —— 必须显式写进 hiddenimports，否则打包版一开托盘就崩。
  3. 资源文件（host.html / widget.js / widget.css / icon-*.png）都要打进去。
  4. **只打这 3 个 JS/CSS 资源**，不要打 data*.js —— 那些是原型的 mock 数据，
     打包版的数据全部来自看板 API，带进去只会误导。
"""
from PyInstaller.utils.hooks import collect_all
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# 第 0 步：重新生成 host.inline.html
#
# ★ 为什么必须是构建的第一步，而且失败就 raise：
#   host.inline.html 是 widget.css / widget.js 的**副本**。手工生成过一次之后，
#   改 widget.js 只改了源文件、副本还是旧的 —— 打出来的 exe 跑的是旧代码。
#   最坑的是**所有信号都是绿的**：文件在、构建成功、程序能启动，只有行为不对。
#   （实测踩到：改了 5 处，副本停留在几十分钟前，差点当成"改了没生效"。）
#   所以这里强制重新生成；生成不出来（锚点被删等）就让构建直接失败，
#   宁可不出包，也不要出一个跑的旧代码的包。
# ---------------------------------------------------------------------------
_generator = os.path.join(SPECPATH, "make_inline.py")
try:
    _r = subprocess.run([sys.executable, _generator],
                        capture_output=True, text=True, encoding="utf-8")
    print("[spec] make_inline: %s" % (_r.stdout or "").strip())
    if _r.returncode != 0:
        raise RuntimeError(_r.stdout + _r.stderr)
except Exception as _e:
    raise SystemExit("!! 生成 host.inline.html 失败，构建中止: %s" % _e)

# ---------------------------------------------------------------------------
# 第 0.5 步：确认 UPX 真的在
#
# ★ `upx=True` 单独写是没有保证的 —— PyInstaller 找不到 UPX 就**静默跳过压缩**，
#   构建照样成功、程序照样能跑，只有体积悄悄变大（13.9MB → 15.9MB）。
#   这就是典型的"关掉提示、但没验证底层能力"，所以这里改成找不到就**中止构建**。
#   UPX 的位置：优先环境变量 WBB_UPX_DIR，其次内置的稳定目录。
# ---------------------------------------------------------------------------
UPX_DIR = os.environ.get("WBB_UPX_DIR") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "binaries", "upx")
_upx_exe = os.path.join(UPX_DIR, "upx.exe")
if not os.path.isfile(_upx_exe):
    raise SystemExit(
        "!! 找不到 UPX：%s\n"
        "   `upx=True` 时 PyInstaller 找不到 UPX 会**静默跳过压缩**，\n"
        "   产物会悄悄变大却不报错。所以这里直接中止。\n"
        "   解决：把 upx.exe 放到该目录，或用环境变量 WBB_UPX_DIR 指定。" % _upx_exe)

# ★★ 关键：**必须在这里重跑一次 UPX 探测**，否则上面做了也白做。
#
#   PyInstaller 的 UPX 开关存在全局 CONF 里，而它在**执行 spec 之前**
#   就已经用命令行参数（我们没传 --upx-dir）探测过一次了：
#       configure.get_config(upx_dir=None) → 去 PATH 里找 upx → 找不到
#       → CONF['upx_available'] = False
#   之后无论 `EXE(upx_dir=...)` 写什么都不管用 —— 因为
#       `self.upx = CONF['upx_available'] and kwargs.get('upx', False)`
#   读的是那个 False。（EXE 甚至不接受 upx_dir 参数，传了也不报错。）
#   实测踩到：日志里 `DEBUG: UPX is not available.`，体积纹丝不动。
#   所以这里用正确的目录**重跑探测**并覆盖 CONF，而且必须放在
#   `Analysis(...)` 之前 —— Analysis 也会读 CONF['upx_available']。
from PyInstaller import configure as _pi_configure          # noqa: E402
from PyInstaller.config import CONF as _PI_CONF             # noqa: E402

_PI_CONF.update(_pi_configure.get_config(upx_dir=UPX_DIR))
if not _PI_CONF.get("upx_available"):
    raise SystemExit("!! UPX 探测仍失败（%s），中止构建" % _upx_exe)
print("[spec] UPX = %s（探测通过）" % _upx_exe)

datas = [
    # ★ 看板前端静态资源。server/web_server.py 用
    #   `WEB_DIR = Path(__file__).resolve().parent.parent / "web"` 定位，
    #   打包后 __file__ 在 _MEIPASS，所以这里必须落到 web/ 这个相对位置。
    ("../web", "web"),
    # ★ board/schema.sql —— 建表脚本。
    #   board/store.py 用 `Path(__file__).with_name("schema.sql")` 读它，
    #   而 PyInstaller **只打 .py**，.sql 属于数据文件，不显式声明就会漏。
    #   漏了的现象很隐蔽：服务线程抛 FileNotFoundError 挂掉，
    #   主程序继续跑 ⇒ 界面显示"连不上"，看起来像网络问题。
    ("../board/schema.sql", "board"),
    ("host.html", "."),
    # 内联版：widget.css / widget.js 直接嵌在 HTML 里，零外部请求
    ("host.inline.html", "."),
    ("widget.js", "."),
    ("widget.css", "."),
    ("icon.ico", "."),
    ("icon-ok.png", "."),
    ("icon-stall.png", "."),
    ("icon-off.png", "."),
]
binaries = []
hiddenimports = [
    # 运行时按后端动态 import 的，静态分析看不到
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    # 本项目自己的模块（函数体内 import）
    "tray",
    "autostart",
    "envcheck",
    # ★ 看板服务的模块是**在函数体内 import** 的（保证缺了也不影响挂件本身），
    #   静态分析看不到 —— 必须显式列出，否则打包版一启动就 ImportError。
    "server.web_server",
    # ★ MCP server 也要打进来 —— 这样 `BoardWidget.exe --mcp` 自己就是
    #   看板的 MCP 端点，别的机器只要拷 exe + 在 mcp.json 里加一条即可，
    #   **不需要装 Python、也不需要那份项目目录**。
    #   （函数体内 import，静态分析看不到，必须显式列。）
    "server.mcp_server",
    "board.config",
    # WorkBuddy 会话镜像（在函数体内 import，静态分析看不到）
    "board.workbuddy_watch", "board.store", "board.models", "board.orbcue",
    # pystray 的后端也是动态选的
    "pystray._win32",
    # ★ PIL 的图片插件是**运行时按格式动态加载**的，静态分析看不到。
    #   缺了 PngImagePlugin 的症状极具迷惑性：
    #   Image.open() 照样返回对象（它是惰性的），只有 .load() 才报错，
    #   而托盘代码又把异常吞了 ⇒ 表现成"文件明明在，却说找不到图标"。
    # 图片插件是运行时动态加载的，静态分析看不到 ⇒ 显式列出。
    # 托盘图标是 PNG；ICO 用于 exe 图标与备选。
    "PIL.PngImagePlugin",
    "PIL.IcoImagePlugin",
    "PIL.BmpImagePlugin",
    "PIL.Image",
    "PIL.ImageFile",
    "PIL._imaging",
]

# 把 webview / pythonnet / clr_loader 的**全部**文件收进来。
# 只靠 hiddenimports 不够：它们的 dll 是"数据文件"，不是模块。
for pkg in ("webview", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print("collect_all(%s) 跳过: %s" % (pkg, e))

a = Analysis(
    ["host.py"],
    # "." 找到同目录的 tray/autostart/envcheck；".." 找到项目根的 board/ server/
    pathex=[".", ".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # ---- 体积大头，且**确认用不到**（挂件不碰数值计算/绘图/科学栈）----
        # numpy 一家在诊断里占约 32MB（numpy.libs 的 OpenBLAS 就 19.5MB），
        # 它通常是被 Pillow 之类的依赖间接拉进来的，挂件一次都不用。
        "numpy", "numpy.random", "numpy.linalg", "numpy.fft",
        "scipy", "pandas", "matplotlib",
        "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3",

        # ---- Pillow 的瘦身：**保留能用到的，排掉用不到的** ----
        # ★ 运行时实际只用到两件事：`Image.open()` 读 PNG/ICO，
        #   和 pystray 把它们塞进托盘。所以：
        #   · PngImagePlugin / IcoImagePlugin **必须留**（托盘图标就是这两个格式）
        #   · 编码器（AVIF 7.5MB、TIFF、WebP…）全部可排 —— 挂件不产生图片
        #   · ImageDraw 只在开发期的 make_icon.py 用，运行时不碰
        # ---- PIL：只排真正的 GUI / 抓屏相关 ----
        # ★★ 这里曾经排掉了一大批 `PIL.*ImagePlugin`（Bmp/Gif/Jpeg/Tiff/WebP…）
        #   来省体积，结果**把托盘图标的解码能力一起砍了** ——
        #   PNG 需要 PngImagePlugin，缺了它 Image.open() 仍会返回对象
        #   （它是惰性的），只有 .load() 才抛 UnidentifiedImageError，
        #   而调用方又把异常吞了 ⇒ 表现成"图标文件明明在，却说找不到"。
        #   教训：**不要按"看起来用不到"裁剪库的插件层** ——
        #   插件是运行时按格式动态加载的，静态分析看不出依赖。
        #   图片插件全部放行，代价约 1MB。
        "PIL.ImageQt", "PIL.ImageShow", "PIL.ImageTk", "PIL.ImageGrab",
        # 编码器（只写不读）确实用不到，但留着也就几百 KB，不再冒险
        "PIL._avif",

    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

# ★ 再过滤一遍**二进制**（.pyd/.dll）。
#   `excludes` 只保证"纯 Python 模块"不被收进来，而 Pillow 的编码器
#   （_avif 7.5MB、_webp、_imagingft…）和 numpy 的 OpenBLAS 都是 .pyd/.dll，
#   走的是 binaries 通道 —— 诊断里它们合计占了 30MB+，必须在这里拦。
#   保留清单：托盘只读 PNG/ICO，所以 _imaging（核心）+ Ico/Png 插件要留。
BIN_DROP = (
    "_avif", "_webp", "_imagingft", "_imagingcms", "_imagingmath",
    "openblas", "numpy", "scipy", "libopenblas",
    "tcl", "tk8", "tk9", "_tkinter",
)
before = len(a.binaries)
a.binaries = [b for b in a.binaries
              if not any(x in os.path.basename(b[0]).lower() for x in BIN_DROP)]
print("二进制过滤：%d → %d（去掉 %d 个）" % (before, len(a.binaries), before - len(a.binaries)))

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BoardWidget",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # ★ UPX：**无损**瘦身的正解 —— 只压缩字节，不改任何功能。
    #   PyInstaller 自带一份 upx_exclude 默认表（vcruntime/python3*.dll 等
    #   被系统加载器特殊对待的），所以不必自己列。
    #   代价：个别杀软对 UPX 壳敏感，可能首次运行告警 —— 文档里写明。
    #
    # ★★ 用 upx_dir 显式指定，并且在下面**硬校验文件存在**。
    #    踩过的坑：以前只写 `upx=True`，而 UPX 不在 PATH 上 ——
    #    PyInstaller **找不到 UPX 就静默跳过压缩**，不报错也不警告。
    #    结果：构建成功、程序正常，只有体积悄悄从 13.9MB 涨到 15.9MB。
    #    "关掉提示但不验证底层能力" 就是这么来的，所以这里改成硬失败。
    upx=True,
    upx_dir=UPX_DIR,
    runtime_tmpdir=None,
    console=False,      # GUI 程序，不要黑框
    disable_windowed_traceback=False,
    icon="icon.ico",    # exe 自身的图标
)
