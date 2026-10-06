# AGENTS.md —— 给在这个项目里干活的 agent

> 这份文件是**约定**，不是说明文档。里面的「铁律」是必须遵守的，
> 因为它们对应的都是"不做就会出问题、而且不报错"的事。

---

## ★ 铁律 1：动手就记账，不要等用户催

**看板只会知道"被写进去的东西"。** 用户看不到你脑子里在干什么 ——
他不看你改了哪些文件，他看的是挂件上那四列。

所以：

| 时机 | 动作 |
|---|---|
| 开始一件**实质**工作（不是查一行代码那种） | `create_task(title=..., column_id="doing")` |
| 中途有可报告的进展 | `update_progress(task_id, current=...)` |
| 卡住 / 等外部条件 | `move_task(task_id, to_column="blocked")` |
| 做完了 | `move_task(task_id, to_column="done")` |

**踩过的坑**：有一轮我埋头改了几个小时代码、一次都没写看板，用户看到的是
「进行中一直为空」，反过来质疑挂件坏了。挂件没坏 —— 是**没人往里写**。
另一面也踩过：写了一条任务之后一直不更新，1 小时后被正确地标成「卡住」，
用户又来问"怎么一直挂着"。**两头都是同一个原因：记账不连续。**

* 标题写**用户能看懂的话**（"修复挂件启动 21 秒无响应"），不要写 commit message 风格。
* 一次实质工作 = 一条任务。不要一个文件一条，也不要十条小动作合成一条。
* 不要为了"让看板好看"而编任务。看板是审计凭据，编数据比空着更坏。

---

## ★ 铁律 2：MCP 没接上，上面那条根本做不到

挂件是「看」的，MCP 是「记」的。两个是分开的东西：

* `BoardWidget.exe`（无参数）= 桌面挂件，只读看板
* `BoardWidget.exe --mcp` = **MCP server**（stdio），agent 靠它写任务

### 接入三步（脚本只能做第一步）

```bat
:: 1) 注册（只写 ~/.workbuddy/mcp.json 里的 mcpServers.board 一项，其余服务器不动）
BoardWidget.exe --install-mcp          :: 试运行，只看会写成什么
BoardWidget.exe --install-mcp --yes    :: 落盘（自动备份）
```

**2) 重启 WorkBuddy。**
MCP server 是**常驻进程**：不重启的话它还在用旧配置，新加的那条根本不生效。

**3) 打开「连接器管理」→ 右上角「自定义连接器」→ 找到 `board` → 点「信任」。**
信任之前，新会话里看不到看板工具 —— 这一步只能由用户点，脚本替不了。

**4) 验证**：新会话里让 agent 调一次 `list_tasks`，能列出任务就是接好了。

> `BoardWidget.exe --mcp-guide` 会把上面这段按当前实际状态打印出来
> （已注册 / 指向别处 / 未注册），不用自己判断。
>
> 挂件的**设置面板里也有**：状态行 + 「注册」按钮，点完直接把后续步骤摆出来。

### 为什么 `command` 要指向 exe 而不是 python

```json
{ "command": "…\\BoardWidget.exe", "args": ["--mcp"] }
```

这样别的机器只要拷一个 exe，**不需要装 Python、也不需要这份项目目录**。
如果指向 `python.exe …\server\mcp_server.py`，换台机器就断。

---

## 铁律 3：发布前必须跑这两个测试

```bat
python -m pytest tests -q      :: 数据层（102 项）
python widget\fulltest.py      :: 界面全功能（102 项）
```

`fulltest.py` 把**真实的 host.inline.html**（就是打进 exe 的那份）装进无头浏览器，
注入桩桥接，逐个按钮点、逐段文字量、拖拖动、拉缩放、查注入与导航。
**改过 widget.js / host.html / widget.css 就必须跑它。**

新增判据时**先做变异验证**（故意改坏 → 确认它变红）。不能失败的判据是装饰品。

### ★ 判据必须量「用户看得见的效果」，不能量代理指标

最惨的一次：`cards.classList.toggle('hidden')` 代码完全正确、class 也真的切换了，
但 **CSS 里根本没有 `.hidden` 这条规则** ⇒ 界面一个像素都不动 ⇒
用户反复报"收起箭头无效"，而测试只断言 `classList.contains('hidden')`，**全绿**。

靠 class 控制显隐的地方，判据一律量 `getComputedStyle()` 或 `offsetWidth/Height`。

---

## 铁律 4：打包

```bat
widget> pyinstaller boardwidget.spec --noconfirm
:: 然后更新桌面（挂件运行时占着 exe，会先自动结束进程）
widget> 更新到桌面.cmd
```

* **构建第 0 步会自动重新生成 `host.inline.html`**（`make_inline.py`），
  它是 widget.css/widget.js 的副本，手工维护必然陈旧 ——
  陈旧的表现是"所有信号都是绿的，只有行为不对"。失败会 abort 构建。
* **UPX 缺失会 abort 构建**（不能静默退化成不压缩：体积会从 13.97MB 悄悄涨到 15.89MB）。
  UPX 在 `~/.workbuddy/binaries/upx/`，可用 `WBB_UPX_DIR` 覆盖。
* 构建产物在 `widget/dist/BoardWidget.exe`，**必须手动拷到桌面**才算交付（核 md5）。

---

## 铁律 5：别改共享配置文件坑到常驻进程

`config.json` 被**多个进程**读，其中 MCP server 是常驻的、不会自己重载。

* **加新键前先确认读它的进程是什么版本。** 踩过：往 config.json 加了 `stall_seconds`，
  正在跑的旧 MCP server 立刻拒绝**所有**工具调用（旧代码把未知键当错误，这是刻意设计）。
* 想加说明又不想破坏兼容：写成 `_` 开头的键（新旧代码都忽略）。
* 改完配置要**重启 WorkBuddy** 才轮到 MCP server 读到。

---

## 其他模式（排查用）

| 命令 | 用途 |
|---|---|
| `BoardWidget.exe --mcp-guide` | 打印 MCP 接入引导 + 当前状态 |
| `BoardWidget.exe --check` | 环境自检（WebView2 等） |
| `BoardWidget.exe --windowtest` | 隐藏→显示→再隐藏 循环（托盘那条链，脚本点不到菜单） |
| `BoardWidget.exe --eval "@脚本.js"` | **在真实打包版里执行 JS**，返回值写进 `%APPDATA%\WorkBuddyBoardWidget\host.log` |
| `BoardWidget.exe --debug` | 带开发者工具开窗口 |

**打包版没有控制台** ⇒ 一切排查看 `%APPDATA%\WorkBuddyBoardWidget\host.log`。
