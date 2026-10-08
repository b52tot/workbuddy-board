# WorkBuddy Board

> **WorkBuddy 写的 WorkBuddy 看板 —— 让英雄查英雄。不耽误打游戏了。**

<img src="docs/widget.png" width="380" alt="挂件界面：四列任务常驻桌面，停滞的会标黄">

给 MCP Agent 用的桌面任务看板。四列任务常驻桌面，agent 干到哪一步、有没有卡住，
抬眼就能看到 —— 不用切窗口，也不用点开浏览器。

零第三方依赖（只用 Python 标准库），单文件 exe，双击即用。

---

## 快速开始

1. **打开 exe** —— 下载 [`BoardWidget.exe`](https://github.com/b52tot/workbuddy-board/releases/latest/download/BoardWidget.exe)，双击运行。
2. **注册 MCP** —— 挂件右下角点 **⚙**，在 **MCP** 那一行点「注册」。
3. **重启 WorkBuddy** —— MCP 配置只在客户端启动时读一次；重启后到**连接器管理页**点「信任」。
4. **开始用** —— agent 干到哪一步、有没有卡住，实时出现在桌面上。

> 不用装 Python、不用部署服务 —— 看板后端和 MCP 端点都打包在这一个 exe 里。

---

## 特性

| 能力 | 说明 |
|---|---|
| 桌面挂件 | 无边框常驻 + 置顶，可拖动 / 缩放 / 调透明度，收起后变 62px 条形 |
| 任务卡片 | 标题、说明、标签、优先级、自定义字段 |
| 进度条 | 声明 `type: "progress"` 字段即可，卡片上直接画；进度**只增不减** |
| 停滞检测 | 非终态列 + 超过阈值无任何上报 ⇒ 卡片标黄，顶部给计数 |
| 列分组 | 栏目顺序 / 标题 / 颜色 / WIP 上限全部可配 |
| 状态流转 | 白名单约束，非法流转会报错并提示合法目标 |
| 变更流水 | 每次改动自动记事件，随时可查「谁在何时把什么从哪移到哪」 |
| 存储 | SQLite 单文件，WAL 模式 |
| MCP 工具 | 15 个，agent 可直接读写看板 |
| 网页看板 | 单端口 HTTP，1 秒轮询，深浅色主题 |

---

## 接入 WorkBuddy

挂件自带「注册 MCP」按钮（上面的第 2 步）。手工接入的话：

**第一步** 准备配置：

```bash
cp config.example.json config.json
```

**第二步** 编辑 `~/.workbuddy/mcp.json`，在 `mcpServers` 里加一项：

```json
{
  "mcpServers": {
    "board": {
      "type": "stdio",
      "command": "C:\\绝对路径\\python.exe",
      "args": ["C:\\绝对路径\\workbuddy-board\\server\\mcp_server.py"],
      "timeout": 120000
    }
  }
}
```

`command` / `args` 都用绝对路径，**不需要 `cwd`** —— 配置文件与 `db_path` 都按
**代码位置**定位，不由进程 CWD 决定，所以宿主从哪个目录拉起都一样。

> **不要往 `env` 里塞项目配置。** WorkBuddy 的 MCP 连接指纹把 `env` 的 key 集合算在内，
> 加 key 会让已授信的 server 退回待授信、下次启动重新弹授权。

**第三步** 重启客户端 → 到**连接器管理页**右上角的「自定义」入口点「信任」。

### 工具（15 个）

宿主会自动加 `mcp__board__` 前缀。

| 工具 | 用途 |
|---|---|
| `board_snapshot` | 读看板完整快照（列、任务、统计、进度、停滞） |
| `board_config` | 读当前生效的配置 |
| `create_task` / `update_task` / `delete_task` / `get_task` | 任务增删改查 |
| `list_tasks` / `list_columns` | 按列或标签筛选 / 列出栏目 |
| `move_task` | 状态流转 |
| `update_progress` | 推进进度（只增不减、传绝对值所以幂等） |
| `list_stalled` | 疑似停滞的任务（非终态列 + 超阈值无上报） |
| `list_events` | 变更流水（支持 `since_seq` 增量拉取） |
| `archive_done` / `restore_archived` | 归档出主视图 / 还原回看板 |
| `board_reset` | 清空看板（**需显式 `confirm=true`**） |

---

## 配置

复制 `config.example.json` 为 `config.json` 后修改。**所有键都可省略，省略即用默认值**
（一个配置文件都没有也能跑）。
查找顺序：`--config` 参数 → 环境变量 `WBB_CONFIG` → `./config.json`。

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

**`columns`** 每一项：

```json
{ "id": "doing", "title": "进行中", "color": "blue", "wip_limit": 5, "is_terminal": false }
```

- `id` —— 稳定标识。**改了等于换了一列**，旧任务会找不到列，定好别动
- `color` —— `gray` / `blue` / `amber` / `green` / `red` / `purple` / `teal` / `pink` / `coral`
- `wip_limit` —— 在制品上限，`null` 表示不限；超限时迁入会被拒绝
- `is_terminal` —— 终态列，用于统计完成率。**至少要有一个**

**`transitions`** —— 未列出的流转会被拒绝，报错里会列出该列允许的目标：

```json
{ "todo": ["doing", "blocked", "done"], "doing": ["todo", "blocked", "done"] }
```

**`fields`** —— 未在此定义的键不允许写入：

```json
[
  { "key": "version",  "type": "text",     "label": "版本号" },
  { "key": "risk",     "type": "enum",     "label": "风险等级", "values": ["低", "中", "高"] },
  { "key": "progress", "type": "progress", "label": "进度" }
]
```

`type` 取 `text` / `number` / `enum` / `bool` / `progress`；
`enum` 必须给 `values`，`progress` 必须给 `label`。

### `web`

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `host` | string | `127.0.0.1` | 监听地址（默认仅本机） |
| `port` | int | `8791` | 端口 |
| `open_browser` | bool | `false` | 启动时自动开浏览器 |
| `allow_write` | bool | `false` | 网页端是否允许写操作 |

> 以 `_` 开头的键视为注释、会被忽略；**其余未知键会报错** ——
> 避免「改了配置却没生效」这种最难排查的问题。

看板本身不含业务词汇。`examples/config.custom-columns.json` 演示了把它改成
发布流程看板（六个列 + 专属流转 + 五个字段），不用改一行代码。

---

## 从源码跑

需要 **Python ≥ 3.10**，无需安装依赖。

```bash
git clone https://github.com/b52tot/workbuddy-board.git
cd workbuddy-board
cp config.example.json config.json

python examples/demo_seed.py        # 可选：灌一份示例数据
python -m server.web_server         # 网页看板 → http://127.0.0.1:8791
```

让它常驻（前台进程关终端就停）：

```bash
python svc.py start | status | restart | stop
```

`status` 除了探活，还会**核对「运行中的库」与「配置期望的库」是否一致** ——
`health 200` 只证明有东西在监听，不证明用的是你要的那个库。

挂件本身（需要 `pip install pywebview`）：

```bash
python widget/host.py
```

跑测试：

```bash
python -m pytest tests -q                  # 数据层
python -m server.mcp_server --selftest     # MCP 协议自检（不碰数据库）
python widget/fulltest.py                  # 界面全功能测试（先跑 widget/make_inline.py）
```

## 用 Docker 跑 MCP server

零依赖，所以镜像里**没有 `pip install`** 这一步。主要给两类场景：跑在隔离环境里，
以及让 [Glama](https://glama.ai/) 这类目录站自动做一次「能起来、能应答」的检查。

```bash
docker build -t workbuddy-board .
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' \
  | docker run -i --rm workbuddy-board
```

容器里的 `config.json` 直接来自 `config.example.json`（不另写一份，避免两处漂移），
库落在 `/app/data/board.db`。要持久化就 `-v wbb-data:/app/data`。
网页界面不包含在这个镜像里 —— MCP server 用不到它。

---

## 项目结构

```
workbuddy-board/
├── board/                  数据层（唯一写入方，纯标准库）
│   ├── config.py           配置加载 + 校验
│   ├── models.py           数据模型与状态机校验
│   ├── store.py            ★ 所有写操作的唯一入口，自动登记事件
│   └── schema.sql          建表语句
├── server/
│   ├── mcp_server.py       MCP stdio 工具（手写 JSON-RPC，不依赖 mcp SDK）
│   └── web_server.py       HTTP 看板
├── web/                    展示层（无构建步骤）
├── widget/                 桌面挂件（pywebview）
│   ├── host.py             宿主进程：窗口 / 托盘 / 日志
│   ├── widget.js  widget.css
│   └── boardwidget.spec    PyInstaller 打包配置
├── examples/               最小配置 / 换业务示例 / 示例数据
├── tests/                  数据层 + HTTP 契约 + MCP 协议 + 挂件状态
└── svc.py                  服务管理：脱离会话启动 / 停止 / 探活
```

所有写操作都收敛到 `board/store.py`，且每次写都登记一条事件 ——
于是任何时刻都能回答「这个任务是怎么走到现在这一步的」。

---

## 常见问题

**能看到页面但一直是空的？**
确认 `db_path` 指向的库和灌数据的库是同一个。

**移动任务报「不允许从 X 流转到 Y」？**
流转规则在 `board.transitions`，报错信息会列出该列允许的目标。

**报「不是合法的配置项」？**
拼写错误。看报错里的可用键列表；注释请用 `_` 开头。

**想看某个任务是怎么一步步走到现在的？**
`GET /api/events?task_id=<id>`，或在网页上点开卡片看「变更流水」。

---

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

---

## 许可

MIT，见 [LICENSE](LICENSE)。
