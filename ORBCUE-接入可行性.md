# OrbCue 能不能引入 WorkBuddy —— 可行性分析

> ## ⛔ 已弃用（2026-10-05）
>
> **技术可行，产品不可用，因此放弃并已完全清理。**
>
> - **技术侧**：接通了，`board/orbcue.py` + 测试齐全（联调实测 7/7、`sent=8 dropped=0`）。
> - **弃用原因**：OrbCue 面板**刻意不显示任务内容**（隐私优先），
>   条目名只显示 `source`（工具名）⇒ 看板上所有任务在球上**都叫 "Workbuddy"**，
>   分不清谁是谁，「一眼看出哪个卡住」这个诉求拿不到。
> - **清理范围**：卸载程序、删开机自启、删数据目录（含 33M WebView 缓存）、
>   关 `orbcue.enabled`；**代码与测试保留**（默认关闭），README 已移除相关章节。
>
> 下方是当时完整的调研与接入记录，留作技术存档。

> 日期：2026-10-05 ｜ 问题：OrbCue 面板能不能引入 WorkBuddy
> 结论依据全部来自**直读源码与官方文档**（`raw.githubusercontent.com`），非二手转述。

---

## ✅ 已实施并验证通过（2026-10-05）

**结论：能，且已经接上了。**

| 项 | 状态 |
|---|---|
| OrbCue 安装 | ✅ v0.2.9，`%LOCALAPPDATA%\OrbCue`（用户级，无需管理员），sha256 校验通过 |
| presenter 常驻 | ✅ 开机自启已建（启动文件夹 `.lnk`，target 读回校验一致） |
| 看板侧桥接 | ✅ `board/orbcue.py` + `config.json` 的 `orbcue` 段（`enabled: true`） |
| 端到端实测 | ✅ 建卡→working / 移动→working / 移 blocked→**needs_attention** / 移 done→completed / 删卡→行消失；**sent=5 dropped=0** |
| 测试 | ✅ `tests/test_orbcue.py` 14 条，全量 **81 条**全绿 |

**看板事件 → OrbCue 的实际映射（已按此实现）**：

| 看板动作 | OrbCue 命令 | 实测状态 |
|---|---|---|
| `create_task`（非终态列） | `start <task_id>` | `working` |
| `note_progress` | `working <task_id>` | `working` |
| `move` → 普通列 | `working <task_id>` | `working` |
| `move` → 列上声明了 `orb_state: permission` | `permission <task_id>` | **`needs_attention`** |
| `move` → 终态列 | `complete <task_id>` | `completed` |
| `delete_task` | `reset --session-id <task_id>` | 行消失 |

**哪一列代表"等人处理"不写在代码里** —— 由配置的 `columns[].orb_state` 声明
（本项目既有原则：业务词汇只出现在配置里）。

### ★ 实测踩到的两个坑（都是"两边都成功、中间什么都没发生"）

**坑 1：孤儿事件 —— `sent=3` 但小球上什么都没有**

第一次把「停滞」推给小球时，本地桥接报 `sent=3 dropped=0`，
而 OrbCue 侧 `tracked=0`。**两边都是"成功"的**。

原因在契约里那条容易被略过的规则：

> `waiting_input` / `permission_requested` / `completed` / `failed` / `cancelled`
> **对未知会话 accepted 但不建记录、不发 attention**

那三个任务是**桥接开启之前**就在看板里的，OrbCue 里没有它们的会话，
于是 `waiting` 成了孤儿事件被静默丢弃。

**修法**：在「看到任务」时先把会话建出来（`_ensure_sessions`），
再谈 waiting / complete。顺序是硬要求，已钉测试（断言 `working` 必须排在
`waiting` 之前）。

**坑 2：提醒去重 —— 不做的话小球每秒响一次**

`snapshot()` 是高频读（Web 端 1s 一次）。若每次都对停滞任务发事件，
小球会每秒响一次 —— **提醒变噪音，比不提醒更糟**。
所以只在「新出现的停滞」上提醒，并在任务恢复上报后**摘除标记**
（否则第二次卡住就再也不会提醒 —— 那是漏报，更危险）。

两条判据都做过注入式反向验证：去掉去重 / 去掉摘除 ⇒ 对应用例立刻失败。

**工程约束（都是"错了不报错"的那类，所以都钉了测试）**：
1. **可选**：`enabled: false` 时零开销（连 `orb.exe` 都不去找）
2. **异步**：事件入队后由后台线程消费，**绝不阻塞写路径**
   （反向臂：让 `_call_orb` 卡住 2 秒，50 次 `send` 仍须 <0.5s 返回）
3. **静默**：`orb` 调用失败只进冷却（60s），不冒泡给看板
4. **队列满即丢**，不阻塞写路径
5. `close()` 投毒丸时队列满也要吞掉 `queue.Full`
   —— **这条是测试抓出来的真 bug**（看板退出路径曾会抛异常）

---

## 〇、结论

**能。而且它是"有契约、欢迎外部接入"的设计。**

依据是 OrbCue 自己的文档 `docs/event-contract.md`，第一句就是：

> **这是 OrbCue 的稳定集成边界。** 适配器只发送结构化生命周期事件，
> 不得导入状态机内部类型，也不得读取 Agent 内容。

文档末尾还有一句直接决定答案的话：

> **连接页只接上表这些工具。其他工具不要走 wrapper：用 `orb start` / `orb waiting` / `orb complete` 发事件。**

⇒ **"其他工具"是被明确接受的一等接入方式**，WorkBuddy 属于这一类。

---

## 一、接入机制（三条路，按推荐度）

| 方式 | 细节 | 判定 |
|---|---|---|
| **① `orb` CLI** | 文档原话：**"集成不需要自己实现 socket 客户端"** | ✅ **首选** |
| ② 直写命名管道 | Windows `\\.\pipe\orbcue`（可用 `ORBCUE_SOCKET` 覆盖），**一行一个 JSON** | ✅ 可行，但要自己实现客户端 |
| ③ hook 脚本 | 各 agent 的 hook 配置 | ❌ 文档明确说"其他工具**不要**走 wrapper" |

**CLI 命令集（文档原文）**：

```bash
orb start     session-123 --source claude
orb permission session-123 --source claude
orb complete  session-123 --source claude
orb acknowledge --source claude --session-id session-123
orb reset       --source claude --session-id session-123
```

**传输层的硬约束**（会影响接入设计）：
- **不监听 TCP / UDP** —— 只能走本机 IPC
- **请求最大 16 KiB** —— 先限大小再解析
- 未知 JSON 字段被忽略；**Dock 不把原始 payload 写入状态文件**

---

### ★★ 更直接：官方有一份专门写给 MCP / Skill 集成方的说明

`examples/mcp-skill-note.md`（**该目录下唯一一个文件**，标题就叫 **"MCP / Skill 接入说明"**）：

> **Skill 或 MCP 集成方不需要 import OrbCue 内部模块。** 在新会话开始时调用一次：
>
> ```
> orb start <stable-session-id> --source <tool-name>
> ```
>
> 在等待、结束或失败时对**同一个** session id 调用：
>
> ```
> orb waiting    <stable-session-id> --source <tool-name>
> orb permission <stable-session-id> --source <tool-name>
> orb complete   <stable-session-id> --source <tool-name>
> orb fail       <stable-session-id> --source <tool-name>
> ```

补充规则（原文）：
- `orb stop` / `orb completed` **等价于** `orb complete`；`orb error` **等价于** `orb fail`
- **`event_id` 可省略**（CLI 会生成），但**重试时应复用同一 `event_id`**；
  Dock 会对重复事件**去重**，不会重复提示或计数
- Dock **只在当前用户范围内**收事件（Linux Unix socket / Windows 命名管道）；**没有网络监听**
- 请求上限 **16 KiB**；**未知动作和非法 JSON 返回 rejected，不改变现有状态**

⇒ **WorkBuddy 正落在"MCP / Skill 集成方"这个官方点名支持的位置上。**
接入不是"绕过设计"，而是**设计里预留的那条路**。

### 补充边界（`docs/agents/domain.md`，值得记住的两条）

- ⭐ **"不把声音、窗口和 Agent adapter 的失败传播回事件发送方"**
  ⇒ **发事件失败不会影响 WorkBuddy**。这一点对集成方很关键：接入是**单向、无副作用**的。
- "**不扫进程表来猜 working/waiting**"；唯一允许的进程派生事件是对 hook 已记录的
  **单个** PID 查活性，且只用于补发 `session.closed`

---

## 二、事件契约

```json
{
  "version": 1,
  "type": "session.started",
  "event_id": "claude-session-123-start-1",
  "source": "claude",
  "session_id": "session-123",
  "occurred_at": "2026-08-16T08:00:00Z",
  "severity": "info",
  "deep_link": "https://example.invalid/session/123",
  "cwd": "/home/user/project",
  "workspace_root": "/home/user/project",
  "parent_session_id": "optional-parent-session",
  "terminal_id": "optional-terminal-identity",
  "metadata": {"workspace": "optional-bounded-value"}
}
```

**必填**：`version`、`type`、`event_id`、`source`、`session_id`、RFC3339 的 `occurred_at`。

### 生命周期类型（全部）

| type | 含义 | 面板表现 |
|---|---|---|
| `session.started` | 新会话开始并进入工作 | 创建/恢复，**静默** |
| `session.working` | 会话继续工作 | 更新状态，**静默** |
| `session.idle` | 存在但未在工作 | 标记 `o` |
| `session.waiting_input` | 等待用户文字输入 | **进待查看 + 播放一次 attention 提示** |
| `session.permission_requested` | 等待授权 | **进待查看 + 播放一次 attention 提示** |
| `session.completed` | 一轮对话自然结束 | 标记 `*` |
| `session.failed` | 失败打断 | 标记 `!` |
| `session.cancelled` | 本轮取消 | 标记 `x` |
| `session.closed` | 真正关闭 | **从打开列表和总数中移除** |

**三条必须遵守的规则**：

1. **先建会话**：`waiting_input`/`permission_requested`/`completed`/`failed`/`cancelled`
   **对未知会话 accepted，但不建记录**，也不发 attention。
   ⇒ 必须先发 `started`/`working`/`idle` 把会话建出来，否则后续事件是"孤儿"。
   （文档特意说明这是为了"用户 reset/clear 之后迟到的 stop 不会凭空复活计数"）
2. **时间窗口**：事件时间超过当前 **24 小时**，或超前超过 **5 分钟** ⇒ 返回 `stale_event`
3. **字段大小上限**：`source` 64B、`session_id` 256B、`event_id` 128B、
   `terminal_id` 128B、`deep_link` 2048B、`cwd`/`workspace_root` 各 256B、
   `metadata` 最多 32 项且 key/value 各 256B

### 查询接口（同一个 socket）

```json
{"query":"snapshot"}                                          // 取快照
{"query":"subscribe"}                                          // 订阅，状态变化推 snapshot
{"query":"acknowledge","source":"claude","session_id":"..."}   // 只清待查看标记
{"query":"reset","source":"claude","session_id":"..."}         // 显式移除会话
```

---

## 三、映射设计：看板任务 → OrbCue 会话

| 看板动作 | OrbCue 事件 | 说明 |
|---|---|---|
| `create_task` 进 `doing` | `session.started` | 建会话（**必须先发**） |
| `update_progress` 上报 | `session.working` | 静默，不打扰 |
| 任务在 `todo` 停留 | `session.idle` | 标记 `o` |
| `move → blocked` | `session.permission_requested` | **等你处理 ⇒ 进待查看 + 提示音** |
| **停滞**（超阈值无上报） | `session.waiting_input` | **等你注意 ⇒ 进待查看 + 提示音** |
| `move → done` | `session.completed` | 标记 `*` |
| 任务失败 | `session.failed` | 标记 `!` |
| `delete_task` | `session.closed` | 从列表移除 |

### ★ 一个意外的吻合

看板现有的两项能力，**正好落在 OrbCue 唯二会"打扰你"的语义上**：

- OrbCue 里**只有** `waiting_input` 和 `permission_requested` 会进"待查看"并播放提示音。
- 看板里**只有**「阻塞」和「停滞」是真正需要人介入的。

⇒ **两边的"该吵醒用户"集合是同构的**。接上去以后，
「有任务卡住了」这件最贵的事，会通过 OrbCue 的提示音 + 托盘 + （可选）手机推送推到人面前。

---

## 四、边界：它做不到什么（**这部分决定它是补充还是替代**）

| 你要的 | OrbCue 能不能 |
|---|---|
| **树状结构 + 展开节点看步骤** | ❌ **不能**。面板是**按项目分组的会话列表**，没有层级树 |
| 显示任务内容/详情 | ❌ **不能，而且是刻意不做**。文档反复强调"不转发题目、标题、提示词和工具参数"、"即使 payload 含 `transcript_path`，也不会打开、保存或转发该路径" |
| 按任务数自动收起/展开 | ❌ 不是。形态是**悬浮小球**，靠 hover / 点击展开面板 |
| 任务进度百分比 | ❌ 契约里没有进度字段（只有会话状态） |
| 多 agent 状态汇总 + 提醒 | ✅ **强项** |
| 贴边收起 / 透明度 / 多主题 / 全局快捷键 | ✅ 有 |
| 手机推送 | ✅ ntfy（默认关） |

⇒ **OrbCue 是"状态灯 + 提醒器"，不是"任务树 + 详情面板"。**
它能替掉的是「有任务卡住要提醒我」这一半；替不掉的是「展开看每一步」那一半。

---

## 五、附带发现：两种"判活"思路的对比

OrbCue 有一份 ADR：`docs/adr/0002-explicit-events-without-heartbeats.md`
——**"显式事件，不用心跳"**。

它的判活方式（文档原文）：

> Liveness 仅 hook 路径写入：`agent_os`、`agent_pid`、`agent_starttime` 三项齐全才合并
> …… daemon **每 15s 问「是否仍是原进程」，死亡则发 `session.closed`**。
> **不扫进程表，不因 HWND 消失删会话。**

| | 判活依据 | 优点 | 代价 |
|---|---|---|---|
| **看板（现有）** | 最后一次事件距今多久（静默时长） | 不需要有进程 | **任务本身慢会被误判停滞** |
| **OrbCue** | **进程存活三元组**（os + pid + starttime） | 准 —— 进程真死了才判死 | 要求被监视的东西**有进程可查** |

⇒ 对"Agent 跑的任务"，OrbCue 的方式更准；对"没有独立进程的长任务"（比如一次批量搬运），
看板的方式才是唯一可行。**两者互补，不冲突。**

---

## 六、安装与实测记录（已完成）

**它最新版是 v0.2.9，今天（2026-10-05）刚发布。**

| 项 | 值 |
|---|---|
| 安装包 | `OrbCue_0.2.9_x64-setup.exe` |
| 体积 | **3.6 MB** |
| sha256 | `fab13008d290c6a74f87ea740f75e08cc25155061f25b150d3ee7be2194edb61` |
| 免安装版 | v0.2.5 有 portable zip（**最新版没有**） |
| 平台 | **只支持 Windows 10/11 x64**（暂未正式支持 macOS / Linux 桌面） |
| 实例 | 同一用户下**只运行一个** OrbCue 实例 |
| 开机自启 | **默认关**，需在面板「设置」里开 |
| 全局快捷键 | `Ctrl+Shift+Space`（**默认开**） |

**实际执行的验证步骤**（含一条反例，全部通过）：

1. 装（3.6 MB，会注册托盘 + 可选开机自启）
2. 确认 `orb` CLI 可用（`orb --help`）
3. **用 CLI 手发一条 `orb start test-1 --source workbuddy`**
   ⇒ 小球上必须出现一个会话
4. 发 `orb complete test-1 --source workbuddy` ⇒ 应标记完成
5. 发 `orb reset --source workbuddy --session-id test-1` ⇒ 应移除
6. **反例**：先发 `orb complete ghost-1 --source workbuddy`（不先 start）
   ⇒ 按契约应是 **accepted 但不建记录**（小球上不出现）—— 验证"先建会话"这条规则真的在生效

> 下载按既有规矩走 **Motrix**（不写 curl 循环），GitHub 走国内镜像。

---

## 七、一句话给决策

**OrbCue 能接，且接入成本很低（发几条 CLI 或写命名管道）。**
但它能接的是"**提醒层**"，接不了"**展示层**"。
如果要"任务树 + 展开看步骤"，那部分仍得自己做 —— OrbCue 只能当**并列的提醒器**，
不能当那个挂件的底子。
