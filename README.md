# WorkBuddy Board

> **WorkBuddy 写的 WorkBuddy 看板 —— 让英雄查英雄。不耽误打游戏了。**

<img src="docs/widget.png" width="380" alt="挂件界面：四列任务常驻桌面，停滞的会标黄">

给 WorkBuddy（或任何支持 MCP 的 Agent）用的**通用任务看板**。

Agent 干活时最难受的不是慢，是**看不见** —— 命令交出去之后跑到哪一步、卡在哪儿、
还要多久，全靠猜。这个项目给 Agent 装一块公共看板：任务卡片、列分组、状态流转、
数据读写，全部开箱可用。

**零依赖、零构建**：只用 Python 标准库 + 原生 HTML/CSS/JS。克隆下来就能跑。

> 这是**本项目当前的实现事实**，不是约束 —— 依赖为零是因为做到这个规模时标准库够用。
> 若将来某项能力用标准库实现不划算，引入依赖是正常选择。

---

## 快速开始

1. **打开 exe** —— 下载 [`BoardWidget.exe`](https://github.com/b52tot/workbuddy-board/releases/latest/download/BoardWidget.exe)，双击运行。
2. **注册 MCP** —— 挂件右下角点 **⚙**，在 **MCP** 那一行点「注册」。
3. **重启 WorkBuddy** —— MCP 配置要重启才生效；重启后去连接器页点一下「信任」。
4. **开始用** —— agent 干到哪一步、有没有卡住，就实时出现在桌面上了。

> 不用装 Python，不用另外部署服务 —— 看板后端和 MCP 端点都打包在这一个 exe 里。

---

## 特性

| 能力 | 说明 |
|---|---|
| 任务卡片 | 标题、说明、标签、优先级、自定义字段 |
| 进度型任务 | `fields` 里声明 `type: "progress"`，卡片上直接画进度条；**进度只增不减**，回退会被显式拒绝 |
| 停滞检测 | 非终态列 + 超过 90s 无任何上报 ⇒ 卡片打「疑似停滞」标记，顶部给出计数 |
| 列分组 | 栏目由配置定义，顺序/标题/颜色/WIP 上限全部可配 |
| 状态流转 | 流转白名单约束，非法流转会报错并提示合法目标 |
| 数据读写 | SQLite 单文件，WAL 模式，一写多读 |
| 变更流水 | 每次改动自动记事件（含每次进度推进），随时可查「谁在何时把什么从哪移到哪」 |
| MCP 工具 | 15 个工具，Agent 可直接读写看板 |
| Web 看板 | 单端口 HTTP，纯标准库，1 秒轮询刷新，深浅色主题 |

---

## 长耗时任务的进度与停滞

Agent 跑批量任务（搬运、构建、评测）时最常见的问题是**看不见**：
跑到哪了、还要多久、是不是卡死了。这块由两个能力覆盖。

**进度条** —— 在配置里声明字段即可：

```json
{ "key": "progress", "type": "progress", "label": "进度" }
```

Agent 用 `update_progress` 推进：

```json
{ "task_id": "job1", "current": 486, "total": 720, "message": "上传中：第 486/720 个" }
```

三条设计约束（都有测试兜着）：

- **只增不减。** 传比当前更小的值会被**显式拒绝**，报错里告诉你当前是多少。
  这是刻意的：静默忽略会让调用方以为上报成功了。对齐 MCP 规范对
  `progress` 的要求 —— *"This should increase every time progress is made"*。
- **传绝对值，不传增量。** 因此重复上报同一值是幂等的，网络重试不会把进度推两遍。
- **`total` 可省。** 第一次给 `total` 之后，后续只传 `current` 会自动继承。

按字节加权（批量文件体积差异大时，避免小文件跑完进度条就假装快满了）：
给 `update_progress` 传 `weight_bytes`。

**停滞检测** —— `list_stalled` 列出「非终态列 + 超过阈值没有任何上报」的任务。

> ⚠️ **语义边界，务必分清**：它检的是「**上报中断**」，不是「**进度值没变**」。
> 所以长任务必须**周期性**调用 `update_progress` 上报 —— 传输中途进度值不变
> 是正常的，但「一直不上报」才可疑。不区分这两者会导致误报。

停滞依据取自 `events`（只追加、seq 单调），而不是 `tasks.updated_at` ——
后者可被任何 `UPDATE` 顺带改写，也可被「截断-重写-等长」这类改写骗过。

---

## 快速开始

```bash
# 1. 克隆
git clone <repo-url> workbuddy-board
cd workbuddy-board

# 2. 无需安装依赖（标准库实现）。可选：跑一遍测试确认环境正常
python -m unittest discover -s tests -v

# 3. 灌一份示例数据，先看看长什么样
python examples/demo_seed.py

# 4. 启动看板，浏览器打开 http://127.0.0.1:8791
python -m server.web_server
```

要求 **Python ≥ 3.10**（用到了 `X | None` 联合类型语法）。

### 让它常驻（推荐用内置的 svc.py）

`python -m server.web_server` 是前台进程，**终端一关就停**；
即使放到后台，若由 IDE / Agent 会话拉起，会话结束也可能被**连带回收**
（此时日志里**一行异常都没有** —— 这种退出最难排查）。

内置的 `svc.py` 用「完全脱离父进程」的方式启动，并写 pidfile 便于管理：

```bash
python svc.py start   --config examples/config.custom-columns.json
python svc.py status  --config examples/config.custom-columns.json
python svc.py restart --config examples/config.custom-columns.json
python svc.py stop
```

`status` 会做两件事 —— 不只是探活：

1. 探活 `/api/health`
2. **核对「运行中的库」与「配置期望的库」是否一致**

第 2 条很关键：`health 200` 只证明**有东西在监听**，不证明**用的是你要的那个库**。
踩过这个坑：`status` 没带 `--config` 时读到默认配置，看到任务数 0 以为数据丢了，
其实服务跑的是另一个库。所以两者不一致时 `status` 会明确报出来并返回非 0。

### 其他常驻方式

```bash
# Linux / macOS：nohup
nohup python -m server.web_server > board.log 2>&1 &

# systemd（开机自启 + 崩溃重拉）
# /etc/systemd/system/workbuddy-board.service
#   [Service]
#   ExecStart=/usr/bin/python3 -m server.web_server
#   WorkingDirectory=/opt/workbuddy-board
#   Restart=always
```

> 看板是**无状态服务**：随时可以重启，数据全在 SQLite 里，重启后状态一致。

## 接入 WorkBuddy

WorkBuddy 通过 MCP 以 stdio 方式拉起 `server/mcp_server.py`。

**第一步**：准备配置（仓库里只有模板，`config.json` 被 gitignore）：

```bash
cp config.example.json config.json
```

**第二步**：编辑 `~/.workbuddy/mcp.json`，在 `mcpServers` 中加一项：

```json
{
  "mcpServers": {
    "board": {
      "type": "stdio",
      "command": "C:\\绝对路径\\python.exe",
      "args": ["C:\\绝对路径\\workbuddy-board\\server\\mcp_server.py"],
      "timeout": 120000,
      "env": { "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

`command` 用绝对路径的 python，`args` 用 `mcp_server.py` 的绝对路径。
**不需要** `cwd`。

> **不要往 `env` 里塞项目配置。**
> 两件事：① WorkBuddy 的 MCP 连接指纹（trustMaterial）把 `env` 的 **key 集合**算在内，
> 加 key 会让已授信的 server 退回待授信、下次启动重新弹授权；
> ② 本项目配置**本来就按代码位置定位**，与 CWD、与 env 都无关（见下）。

**第三步**：重启 WorkBuddy 客户端，再到**连接器管理页**右上角的「自定义」入口点「信任」。

> 两个容易踩的点：
> - **`mcp.json` 只在客户端启动时读一次，没有热加载** —— 改完必须重启。
> - **「MCP 服务管理」面板不是「连接器管理页」** —— 信任入口在后者，不在前者。

### 为什么不需要 `cwd`

`config.json` 与 `db_path` 都由**代码位置**定位，不由进程 CWD 定位：

| 项 | 锚点 |
|---|---|
| `config.json` | 项目根（`board/` 的上一级）→ 其次 CWD |
| `db_path` 等相对路径 | **配置文件所在目录** |

**为什么必须这样**：MCP server 由宿主 spawn，**CWD 由宿主决定**。若按 CWD 解析，
宿主换个工作目录启动就会：读不到配置 ⇒ 静默退回内置默认列；`./data/board.db`
解析到别处 ⇒ 数据写进另一个目录。**两者都不报错**，只会让人以为「列定义丢了」
「数据丢了」。要覆盖，用 `WBB_CONFIG` 环境变量或 `--config` 参数（优先级最高）。

配置完成后，Agent 侧会出现 15 个工具（宿主会自动加 `mcp__board__` 前缀）：

| 工具 | 用途 |
|---|---|
| `board_snapshot` | 读看板完整快照（列、任务、统计、进度、停滞） |
| `board_config` | 读当前生效的配置 |
| `create_task` | 新建任务 |
| `move_task` | 状态流转 |
| `update_task` | 改标题/说明/标签/字段/优先级 |
| `update_progress` | 推进进度（只增不减，绝对值幂等） |
| `list_stalled` | 列出疑似停滞的任务（超阈值无上报） |
| `delete_task` | 删除任务 |
| `get_task` | 读单个任务 |
| `list_tasks` | 按列或标签筛选 |
| `list_columns` | 列出栏目 |
| `list_events` | 读变更流水（支持 `since_seq` 增量） |
| `archive_done` | 把「已完成」里的任务移出主视图（归档；`>0` 只归档 N 天前的、`=0` 全归档、`<0` 一个不归档） |
| `restore_archived` | 把归档的任务恢复回看板 |
| `board_reset` | 清空看板（**需显式 `confirm=true`**） |

---

## 配置

复制 `config.example.json` 为 `config.json` 后修改。**所有键都可省略，省略即用默认值**
（没有任何配置文件时项目也能跑）。

配置查找顺序：命令行参数 → 环境变量 `WBB_CONFIG` → 当前目录 `config.json`。

### `board`

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `title` | string | `WorkBuddy Board` | 看板标题 |
| `db_path` | string | `./data/board.db` | SQLite 路径，自动建目录 |
| `columns` | array | 待办/进行中/阻塞/已完成 | 栏目定义，见下 |
| `transitions` | object | 见示例 | 状态流转白名单 |
| `fields` | array | `[]` | 自定义字段 schema |
| `enforce_wip` | bool | `true` | 是否强制 WIP 上限 |
| `refresh_ms` | int | `1000` | 前端轮询间隔 |
| `hide_empty_columns` | bool | `false` | 隐藏空列 |

**`columns` 每一项：**

```json
{ "id": "doing", "title": "进行中", "color": "blue", "wip_limit": 5, "is_terminal": false }
```

- `id` — 稳定标识。**改了 id 等于换了一列**，旧任务会找不到列而报错，所以定好别动。
- `color` — 取 `gray` / `blue` / `amber` / `green` / `red` / `purple` / `teal` / `pink` / `coral`。
- `wip_limit` — 在制品上限，`null` 表示不限。超限时迁入会被拒绝。
- `is_terminal` — 终态列，用于统计完成率。**至少要有一个**，否则任务永远无法判定完成。

**`transitions`** —— 未列出的流转会被拒绝，报错信息里会列出该列允许的目标：

```json
{ "todo": ["doing", "blocked", "done"], "doing": ["todo", "blocked", "done"] }
```

想放开全部流转，就把每个列的目标写成所有列。

**`fields`** —— 未在此定义的键不允许写入，避免拼写错误被静默接受：

```json
[
  { "key": "version",  "type": "text",     "label": "版本号" },
  { "key": "risk",     "type": "enum",     "label": "风险等级", "values": ["低","中","高"] },
  { "key": "count",    "type": "number",   "label": "数量" },
  { "key": "rollback", "type": "bool",     "label": "需要回滚预案" },
  { "key": "progress", "type": "progress", "label": "进度" }
]
```

- `type` 取 `text` / `number` / `enum` / `bool` / `progress`。
- `enum` 必须给 `values` 数组。
- `progress` 必须给 `label`（否则页面上只有一根不知道代表什么的条 —— 配置期就拒绝）。
- 一个看板可以有多个 `progress` 字段，第一个作为「主进度」用于顶部聚合进度条；
  停滞判据与聚合都用主进度。

### `web`

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `host` | string | `127.0.0.1` | 监听地址（默认仅本机） |
| `port` | int | `8791` | 端口 |
| `open_browser` | bool | `false` | 启动时自动开浏览器 |
| `allow_write` | bool | `false` | 网页端是否允许写操作 |

> 配置里**以 `_` 开头的键视为注释，会被忽略**。其余未知键会报错 ——
> 这是刻意的：避免「改了配置却没生效」这种最难排查的问题。
> 示例：`"_columns_comment": "id 是稳定标识"`。

---

## 通用性：换业务只改配置

看板本身不含任何业务词汇。`examples/config.custom-columns.json` 演示了
把它改成**发布流程**看板 —— 六个列（草稿/评审中/测试中/待发布/已发布/已驳回）、
专属流转规则、五个自定义字段，**一行代码都不用动**：

```bash
WBB_CONFIG=examples/config.custom-columns.json python -m server.web_server
```

---

## 项目结构

```
workbuddy-board/
├── board/                 数据层（唯一写入方，纯标准库）
│   ├── config.py          配置加载 + 校验 + 默认值合并
│   ├── models.py          数据模型与状态机校验
│   ├── store.py           ★ 所有写操作的唯一入口，自动登记事件
│   └── schema.sql         建表语句（含索引、外键）
├── server/                服务层
│   ├── mcp_server.py      MCP stdio 工具（手写 JSON-RPC，不依赖 mcp SDK）
│   └── web_server.py      HTTP 看板（单端口，纯标准库）
├── web/                   展示层（无构建步骤）
│   ├── index.html
│   ├── app.js             渲染逻辑（只读，列定义全部来自 API）
│   └── style.css          设计 token 集中在顶部，改主题只动这里
├── svc.py                 服务管理：脱离会话启动 / 停止 / 探活 + 库路径核对
├── examples/              开箱示例
│   ├── config.minimal.json         最小配置
│   ├── config.custom-columns.json  换业务示例
│   └── demo_seed.py                灌示例数据
├── tests/                 判据
│   ├── test_store.py      数据层：流转/WIP/事件/配置校验
│   ├── test_api.py        HTTP 契约 + MCP 协议
│   └── test_svc.py        进程管理：pidfile 编码、配置核对、探活
├── NEXT_STEPS.md          下一步工作记录（带可验证完成标准）
└── 同类项目调研与选型判定.md   选型依据与关键反证
```

### 为什么强调「唯一写入方」

看板前端、MCP 工具、外部脚本都可能想改数据。若各自直接写库，
「谁改了、为什么改、什么时候改」就说不清了。本项目把所有写操作收敛到
`board/store.py`，且**每次写都登记一条事件** —— 于是任何时刻都能回答：
这个任务是怎么走到现在这一步的。

---

## 设计取舍

**手写 JSON-RPC，不用 `mcp` SDK。**
PyPI 的 `mcp>=1.0.0` 已拉到 2.x，FastMCP 在 2.x 更名为 MCPServer，
照抄旧写法必崩。stdio JSON-RPC 只有 `initialize` / `tools/list` / `tools/call`
三个方法，手写反而稳定且零依赖。

**工具名不带 `mcp__` 前缀。**
前缀由宿主注册时自动添加。工具定义里写裸名 `create_task`，
否则宿主侧会出现 `mcp__board__mcp__board__create_task` 这类重复前缀。

**看板默认只读。**
所有写操作走 MCP 工具，保证事件流完整可溯。需要网页拖拽就把
`web.allow_write` 置 `true` —— 后端仍走同一个 Store，事件照样登记。

**配置里的未知键报错。**
最危险的不是报错，是「看起来更好」的静默失效：改了配置却没生效，
用户以为改好了。所以拼写错误必须当场报出来。

---

## 开发

```bash
# 全部测试
python -m unittest discover -s tests -v

# MCP 协议自检（不碰数据库）
python -m server.mcp_server --selftest
```

自检覆盖的不变量：
- 工具名不含 `mcp__` 前缀或双下划线
- 每个工具的 `required` 项都在 `properties` 中声明
- `TOOLS` 声明与 `HANDLERS` 实现一一对应
- 配置能独立校验通过且存在终态列

---

## 常见问题

**Q：能看到页面但一直是空的？**
确认 `db_path` 指向的库和灌数据的库是同一个。用 `WBB_CONFIG` 时两处都要带。

**Q：移动任务报「不允许从 X 流转到 Y」？**
流转规则在 `board.transitions` 里。报错信息会列出该列允许的目标。

**Q：报「不是合法的配置项」？**
拼写错误。看报错里的可用键列表；注释请用 `_` 开头。

**Q：想看某个任务是怎么一步步走到现在的？**
`GET /api/events?task_id=<id>`，或在网页上点开卡片看「变更流水」。

---

## 许可

MIT，见 [LICENSE](LICENSE)。
