# workbuddy-board 技术方案

> 版本：2026-10-05 ｜ 依据：`同类项目调研与选型判定.md`（12 个开源同类项目实测核验）
> **定位：不改用第三方，在自研基础上做针对性补强。**

---

## 一、方案定位：为什么不是「选一个开源项目用」

**自建 vs 开源的真正分界，不是依赖多少，而是「解决的问题是否重合」。**

| 项目 | 它解决的问题 | 你的场景是否需要 |
|---|---|---|
| `ustoppble/overclick`（118★） | 多 Agent 并发抢任务 + 人机验收分离 | ❌ 一人单机，皆无 |
| `multidimensionalcats/kanban-mcp`（83★） | 跨项目 issue/feature/epic 跟踪 | ⚠️ 部分（缺停滞检测与配置驱动） |
| `axis-love/flow`（7★） | 角色化权限 + 多人分工 | ❌ 同样不需要 |
| **本项目** | 一人单机下的任务可见性 + 卡住能发现 | ✅ **正对** |

**结论**：三个开源项目在技术上**都能接入 WorkBuddy**（已验证 `mcp.json` 支持 stdio 与
http 两种传输），否掉它们的**不是门槛，而是需求不重合**。

**保留的反证**：若场景变为多 Agent 并行或需要给他人用，
`overclick` / `kanban-mcp` 会立刻从「过重」变成「优选」——
反转条件列在 `DECISION-开源-vs-自建.md` 的 T1–T4。

---

## 二、架构总览

保留下来的既有架构（已验证可用），本次未改动分层：

```
┌──────────────────────────────────────────────────────────┐
│  接入层（两个入口，共享同一个 Store）                        │
│   ┌────────────────────┐   ┌────────────────────────┐    │
│   │ MCP server (stdio) │   │ Web server (HTTP)      │    │
│   │ 13 个工具，写+读    │   │ 只读 + 少量写(可选)     │    │
│   └─────────┬──────────┘   └───────────┬────────────┘    │
└─────────────┼──────────────────────────┼─────────────────┘
              │                          │
              ▼                          ▼
┌──────────────────────────────────────────────────────────┐
│  数据层：board/store.py —— 「唯一写入方」                   │
│   · 所有写操作收敛于此（含 MCP 与 Web 两条路径）             │
│   · 每次写强制登记 events（只追加、seq 单调）                │
│   · 进度单调闸门 / 停滞检测 / 快照聚合 都在这层             │
└──────────────────────────┬───────────────────────────────┘
                           ▼
              SQLite（WAL 模式，一写多读）
              ├── columns  配置的物化视图
              ├── tasks    当前状态
              ├── events   追加写审计流水（唯一可信回放源）
              └── meta     元信息
```

### 三条设计铁律

1. **配置是唯一真相源，schema 只存事实。** `columns` 表是配置的物化视图，启动时同步；`tasks.fields` 是 JSON 列，业务语义全部来自 `config.json`。所以同一份 schema 能承载下载、构建、审批、评测任意业务。
2. **写路径唯一。** 任何状态变更都必须经过 `Store`，否则「任务怎么走到这一步的」就答不上来。这是审计能力的**前提**，不是可选项。
3. **报错要能自修复。** 非法流转报错时列出该列允许的目标；字段拼错时报「未在配置中定义」。静默接受拼写错误是设计缺陷。

---

## 三、能力清单

### 3.1 基线能力（已完成）

- 配置驱动：列 / 流转白名单 / 自定义字段 schema，支持 `_` 前缀注释，未知键**报错**
- 13 个 MCP 工具（stdio，手写 JSON-RPC 2.0，零依赖）
- 只读 Web 看板（原生 HTML/CSS/JS，无构建）
- 事件审计流水（`since_seq` 增量拉取）
- 真实数排序（`position` 用 REAL，插入到两者之间不必重排整列）
- 51 个测试全绿

### 3.2 本轮新增

**① 单任务进度（补上最大缺口）**

原先的 `stats.percent` 只是「终态列任务数 / 总任务数」，是**看板完成率**，不是**单任务进度**。一个跑 6 小时的文件搬运，和一块卡死 6 小时的任务，在只有完成率的看板上长得一模一样。

实现要点：

- 配置声明 `{"key": "progress", "type": "progress", "label": "进度"}`（`label` 必填，配置期校验）
- 新增 `update_progress` 工具：传 `current`（**绝对值**，非增量）⇒ 重复调用幂等，重试不会推两遍
- `total` 可继承：本次不传则沿用任务上已有的（避免每次都重复传）
- **单调闸门**：传入比当前更小的百分比 ⇒ **显式报错并告知当前值**，不静默截断
- 每次推进单独登记 `advance` 事件 ⇒ 可按 `seq` 回放「推进到了哪个值」

**② 停滞检测**

语义边界（**必须写清，否则会误用**）：`list_stalled` 检的是「**上报中断**」，不是「**进度值没变**」。

- 长任务本来就要跑几小时，用「值变大」证明活着是错的（值不变也可能是正常传输中）
- 判据依据用 `events.seq`（只追加、单调），**不用 `tasks.updated_at`**——后者可被任何 UPDATE 顺带改写，「截断-重写-等长」也能骗过它
- 终态列任务永不判停滞（否则假告警淹没真告警）
- 阈值 `STALL_SECONDS = 90`，随快照返回，前端不硬编码

### 3.3 明确不做（边界，防止主线时间被烧掉）

| 不做 | 理由 |
|---|---|
| 依赖图 / 阻塞关系 | overclick、flow 都有，但当前场景未验证有需求；引入即增复杂度 |
| 多项目 / 多租户 | 单文件单库更符合「本地工具」定位 |
| 语义搜索 | 需 140MB ONNX 模型 + 常驻索引；对 13 个 chunk 的看板收益为 0 |
| 依赖图 / 阻塞关系（进阶版） | 见 3.2 —— 现在是同列紧邻序，不做任意 DAG |
| 换语言 / 换存储 | Go/Rust 重写、SQLite→Postgres 迁移，当前收益为 0 |

**判断标准**：不引入一项能力的理由是「**它解决的不是本机真实痛点**」。

> 零依赖是当前实现的事实（见第一节与 `DECISION-开源-vs-自建.md`），**不是准入门槛**。
> 上表四项不做的真实理由逐条列在左栏。

---

## 四、与开源项目的逐项优劣对比

### 4.1 全景对比表（2026-10-05 实测）

| 项目 | 语言 | 安装门槛 | 构建 | MCP | 列/流转可配 | 审计 | License | ★ | 最近提交 |
|---|---|---|---|---|---|---|---|---|---|
| **workbuddy-board** | Python 3.10+ | **纯标准库，0 第三方** | **无** | stdio 手写 | **✓ 全配置驱动** | ✓ | MIT | — | — |
| multidimensionalcats/kanban-mcp | Python | pipx + 6 个必需包（flask/pyyaml/GitPython/dotenv/waitress/gunicorn） | 无 | stdio | ✗ 6 种写死 | ✓ | MIT | 83 | 2026-03-17 |
| ustoppble/overclick | TypeScript | Docker + **Postgres16** + Node≥22 + pnpm | 必须 | http | 部分 | ✓ | MIT | 118 | 2026-09-24 |
| kaban-board/kaban | TypeScript | Node≥18 + Bun + monorepo | 必须 | stdio | 写死 | ? | MIT | 48 | 2026-01-31 |
| axis-love/flow | Python | fastapi/sqlalchemy/pydantic/cryptography/uvicorn | 无 | JSON-RPC `POST /mcp` | 部分 | ✓ | MIT | 7 | 2026-07-18 |
| gablabelle/mcp-kanban | TypeScript | Node≥18 + **23 npm 依赖** + Vite | 必须 | stdio | 部分 | ? | MIT | 4 | 2026-03-11 |
| 0xdzik/kanban-mcp | TypeScript | Next.js 16 + Drizzle + shadcn | 必须 | stdio | 写死 | ✓ | **无** | 4 | 2026-02-26 |
| nerkoman/agent-kanban | Python | FastAPI | 无 | MCP + OpenAPI | 部分 | ? | MIT | 4 | 2026-09-25 |
| graywrk/agent-kanban | Python | FastAPI | 无 | stdio | 写死 | ? | MIT | 4 | 2026-07-08 |
| rgracey/kanban-mcp | Go | Go 1.25 单二进制（需他人编译） | 必须 | stdio/http | 写死 | ✓ | **无** | 2 | 2026-02-24 |
| ajianaz/agentboard | Python | 纯标准库 | 无 | **无 MCP**（REST+API Key） | 部分 | ✓ | Apache-2.0 | 1 | 2026-05-09 |
| jaume-ferrarons/mcp-progress | JavaScript | Node + npm | 无 | stdio | ✗ 无看板 | ✗ | **无** | 0 | 2026-01-05 |

### 4.2 劣势：本项目明显不如开源项目的地方

> 下面 5 条**能力差距的陈述仍然成立**，但「这些劣势都要补」的隐含导向**是错的** ——
> 它来自「竞品有的我们就该有」的对比逻辑。
>
> 按真实需求（`DECISION-开源-vs-自建.md` 的 R1–R5）重判：
>
> | 劣势 | 是否真的需要补 |
> |---|---|
> | 1 生态与工具数量 | ❌ 不需要。13 个工具已覆盖 R1；工具多是「用不到的功能闲着」 |
> | 2 无真实资源遥测 | ❌ 不需要。且 overclick 的「遥测」本质也是 Agent 自报，只是报得更细 |
> | 3 无角色化权限 | ❌ 不需要。一人单机，权限是纯开销 |
> | 4 仍是轮询 | ⭕ **可以补**（唯一值得做的一条，`events.seq` 已是现成游标） |
> | 5 无证据链交接 | ❌ 不需要。`fields` + `description` 变通足够 |
>
> **保留这一节的价值**：它诚实记录了「如果场景变化，我们会缺什么」。
> 上面的判定不是「这些问题不存在」，而是「**在你当前的场景下不需要为它们付费**」。

必须诚实列出，否则方案没有承载力。

**劣势 1 —— 生态与工具数量差距大**

`multidimensionalcats/kanban-mcp` 有 40+ 工具 + parent/child 任务层级 + session hooks 自动注入上下文。本项目 13 个工具，无任务层级。

*影响*：复杂项目分解场景下，本项目需要调用方自己维护层级关系（用 `tags` 或 `fields` 变通）。
*是否补*：暂不补。任务层级在本机场景（NAS 搬运、构建批次）未出现真实需求；等真出现再加，避免为假想需求增复杂度。

**劣势 2 —— 没有真实资源遥测**

`ustoppble/overclick` 记录真实 token 数、执行时长、成本；本项目只有调用方自己上报的 `current/total`。

*影响*：如果调用方不老实上报，进度就是假的——**这是本方案最本质的弱点**：进度是「被声明的」而非「被测量的」。
*缓解*：`update_progress` 支持 `weight_bytes`，批量场景可按字节加权；但根子上仍是自报。
*是否补*：不补。真实遥测需要 hook 进 Agent 运行时，与「不侵入调用方」的定位冲突。

**劣势 3 —— 没有角色化权限**

`axis-love/flow` 有 admin/architect/implementer/reviewer 四种角色化 API Key，可限制谁能改哪一步。本项目只有 `actor` 字符串标注（记录谁改的，但不限制谁能改）。

*影响*：多 Agent 协作时无法技术性阻止越权流转。
*是否补*：不补。本机单用户场景，权限是纯开销；`actor` 字段已满足「事后可追溯」。

**劣势 4 —— 仍是轮询，不是推送**

本项目 Web 端 1 秒轮询（`refresh_ms: 1000`）。SSE/WebSocket 是事件驱动，延迟更低、空转更少。

*是否补*：**列入下一轮，属于「提升体验但不破前提」的少数项**——`events.seq` 已天然是增量游标，`/api/stream` 做成 SSE 不需要任何新依赖（Python 标准库能做），且保留轮询兜底（MCP 规范本身也要求客户端轮询保新鲜度）。这是唯一计划中的下一步能力新增。

**劣势 5 —— 没有证据链交接**

`overclick` 支持交接「分支 / PR / 测试脚本」，本项目 `events.detail` 只是 JSON 备注，无结构化证据挂载。

*是否补*：不补。当前用 `fields` + `description` 变通足够。

### 4.3 优势：本项目独有或明显更强的

**优势 1 —— 零依赖零构建（12 个项目中唯一「纯标准库 + 配置驱动状态机」同时成立）**

其他项目的「轻量」都要打折：kaban 要 Bun，flow 要 5 个 pip 包，mcp-kanban 要 23 个 npm 包。本项目 `requirements.txt` 为空是真空。

*可验证*：全新目录、无配置、无数据 ⇒ 测试全绿、灌数据正常、MCP 自检通过（C1 已实测）。

> 📌 **读法**：这是**当前实现的属性**，不是选型加分项。真正让本项目在对比中站住的是
> **优势 2（配置驱动状态机）**与**优势 4（停滞判据用 seq）**——那是能力差异，不是「依赖更少」。

**优势 2 —— 配置驱动状态机（真正的分水岭）**

改动 `config.json` 就能把「下载流程看板」变成「发布流程看板」，**不改一行代码**。竞品要么写死工作流（kanban-mcp 6 种、kaban、0xdzik、rgracey），要么只能部分配置。

*可验证*：`WBB_CONFIG=examples/config.custom-columns.json python -m server.web_server` 直接跑出另一套列定义。

配套的三个细节是竞品普遍没有的，**2026-10-05 实测输出如下**（隔离临时库，不污染仓库 `data/`）：

```
标题: 发布流程看板 | 列数: 6
3 个进 review(limit=3)：OK                          ← 恰好等于上限时放行，不误报
WIP 生效 -> 列 'review' 已达在制品上限 3，拒绝迁入      ← 超限即拒
流转生效 -> 不允许从 'draft' 流转到 'released'（'draft' 允许的目标: ['review', 'rejected']）
未知字段被拒 -> fields 含未定义的键: ['versoin']（已在 config.board.fields 中定义: ['owner', 'risk'...
```

注意三条报错都**自带自修复信息**（告诉你允许去哪儿、已定义哪些字段），而不是只说「非法」：

- **WIP 硬约束**：`enforce_wip: true` 时超在制品上限直接拒绝迁入
- **`_` 前缀注释约定**：JSON 里能写注释，不污染校验
- **未知键报错**：写了 `versoin` 而不是 `version` 会直接报错并列出已定义字段——「改了配置没生效」是最浪费时间的一类 bug

**优势 3 —— 唯一写入方 + 追加式审计**

所有写收敛到 `Store`，每次写登记 `events`（只追加、seq 单调、不修改不删除）。任何时刻能回答「这个任务怎么走到这一步的」。上表 12 个项目中，只有 4 个有审计，且没有一个把「写路径唯一」作为架构约束。

**优势 4 —— 停滞判据用 seq 而不用时间戳**

这是本项目的技术性细节优势。用 `updated_at` 判停滞会被三种情况骗过：时钟回拨、截断-重写-等长、无关字段的 UPDATE 顺带刷新。用只追加的 `events.seq` 则单调不可伪造。

**优势 5 —— 进度语义对齐 MCP 官方规范**

MCP 规范的 `ProgressNotification` 明确要求 `progress` **"should increase every time progress is made"**（即使 total 未知）。本项目把这句直接落成了**存储层硬闸门**（`_guard_progress_monotonic`），回退**显式报错**而非静默截断。竞品里没有看到对齐这条规范的。

**优势 6 —— 判据有承载力（工程方法层面的差异）**

本项目的每条关键能力都配了**双向判据**：正向臂（施加修复⇒必过）+ 反向臂（构造反事实⇒必败）。例如「进度回退必须报错」这条，测试里真的构造了回退输入并断言 `StoreError` 抛出。

更重要的是**测试抓到了实现自己的 bug**：C6 实现过程中，`_guard_progress_monotonic` 里我早先加了 `and old_pct is not None` 的前置条件，导致「首次上报就缺 total」这条路径被静默放行——被测试抓出来了。同一轮还发现 `idle_sec` 在快照里缺失，导致前端永远显示「已 0 分钟无进展」（元数据 `stalled_ids` 全绿，产物文案是假的）。

---

## 五、决策矩阵：什么情况下该改用开源项目

方案必须回答「它在什么情况下会失败」。以下任一条件成立时，本方案不再是最优解：

| 触发条件 | 应改用 | 原因 |
|---|---|---|
| 需要多用户/多租户，且有权限隔离要求 | `axis-love/flow` | 角色化 API Key 是本项目刻意不做的 |
| 已具备 Docker + Postgres 运维能力，且要真实资源遥测 | `ustoppble/overclick` | 实测遥测与证据链交接本项目明确不补 |
| 需要 40+ 工具与任务层级分解 | `multidimensionalcats/kanban-mcp` | 生态成熟度差距，本项目 13 工具 |
| 团队要求「买来的而不是自研的」以便追责 | 任选其一 | 这是采购问题，不是技术问题 |

**当前不触发的依据**：本机场景是单用户（用户本人）、MCP stdio 接入、无容器运行时、核心痛点是「长耗时任务看不见」——正好落在本项目的优势区。

---

## 六、实施状态与验收

### 已完成（本轮交付）

| 项 | 状态 | 验收证据 |
|---|---|---|
| 单任务进度 | ✅ | 51 测试全绿；MCP 自检 13 工具；实测卡片渲染 67.5%（486/720） |
| 进度单调闸门 | ✅ | 反向臂已成立：回退输入抛 `StoreError`，含 "当前 X%" |
| 停滞检测 | ✅ | 正向：>90s 无事件⇒标记；反向：终态任务与周期上报的长任务均被正确排除 |
| 快照带 `idle_sec` | ✅ | 新增双向判据；反向验证：注释掉挂载逻辑⇒测试立即失败（判据有承载力） |
| 前端渲染 | ✅ | 截图实测：「已 18 分钟 无进展」/「已 1 小时 无进展」，进度条 67.5% |

**实测截图证据**（`docs/screenshot-progress-stall.png`）：

![进度条与停滞标记实测](docs/screenshot-progress-stall.png)

图上可读出四件事，全部符合预期：

1. `A1` 卡片有进度条 **67.5% (486/720)**，并带 `message：上传中：第 486/720 个`
2. 同一张卡同时挂 `⚠ 疑似停滞 · 已 18 分钟 无进展` —— **「有进度」与「在上报」是两件事**，正是设计的语义边界
3. `待办/进行中/阻塞` 列头各有 `N 停滞` 计数，`已完成` 列**没有** —— 终态排除生效
4. 顶部统计区 `停滞 4` 红色高亮、`完成率 33.3%`；控制台无报错

### 6.1 本次实现中被测试/实测抓出的两个真 bug（留档）

这两条是「判据有承载力」的实例，比任何自评都有说服力：

**bug 1 —— 单调闸门被自己加了前置条件而失效**

`_guard_progress_monotonic` 里早先写了 `and old_pct is not None`，导致「首次上报就缺 `total`」这条路径被**静默放行**（拿不到基准就跳过检查）。测试 `test_missing_total_is_rejected_not_silently_skipped` 抓出来了 —— **测试抓到了实现自己的 bug**。修法：删掉该前置条件，缺 `total` 即报错。

**bug 2 —— 停滞时长永远显示「0 分钟」（最危险的一类：元数据全绿，产物是假的）**

`stalled_ids` 算得完全正确，但 `snapshot()` 的 `tasks_by_column` 里**没有 `idle_sec` 键**，前端 `t.idle_sec || 0` 拿到 `undefined` 就显示成「已 0 分钟无进展」。

- 症状隐蔽：`stats.stalled` 计数正确、卡片也确实打上了告警样式，**看起来一切正常**
- 这是「元数据全绿 ≠ 产物正确」的典型：只要不逐字读那句文案，就发现不了
- 修法：`snapshot()` 把 `idle_sec` 挂到任务上；前端改读任务自身字段
- **固化**：新增双向判据 `test_snapshot_cards_carry_idle_sec`（正向：卡片必须带正确的秒数并与 `list_stalled` 一致）+ `test_snapshot_non_stalled_cards_have_no_idle_sec`（反向：未停滞的卡片不得带该键）
- **反向验证**：把挂载逻辑注释掉重跑 ⇒ 测试立即 `AssertionError: 'idle_sec' not found` ⇒ **判据确有承载力，不是装饰品**

顺带修掉一个噪音源：浏览器自动请求 `/favicon.ico` 得到 404，污染控制台、把真错误淹没。加内联 `data:` favicon 消掉。

测试数：33 → 49（C6）→ **51**（本次快照判据）。

### 待办（按性价比排序）

1. **C4 阶段条**：把「取凭证 → 执行 → 校验 → 完成」显式展示。目前进度条只回答「多少」，不回答「到哪一步了」。这是**本轮范围内最后一项缺口**。
2. **SSE 替代轮询**（4.2 劣势 4）：`events.seq` 已是现成游标，标准库可实现，保留轮询兜底。
3. **C7 服务常驻化**：实测踩到「会话结束进程被回收」——stderr 无异常、`/api/health` 直接不可达。这类退出最难排查，因为它像「程序崩了」实际是「被外部收走了」。

### 可机械执行的验收命令

```bash
# 全部测试（应 51 passed）
cd workbuddy-board && python -m unittest discover -s tests

# MCP 协议自检（不碰数据库，应报 13 工具）
python -m server.mcp_server --selftest

# 换业务只改配置（应直接跑出另一套列定义的看板）
WBB_CONFIG=examples/config.custom-columns.json python -m server.web_server

# 进度与停滞的端到端验证
curl -s http://127.0.0.1:8799/api/board | python -c "
import json,sys
d=json.load(sys.stdin)
print('停滞数:', d['stats']['stalled'], '/ 阈值', d['stats']['stall_threshold_sec'], '秒')
for col,ts in d['tasks_by_column'].items():
    for t in ts:
        p=t.get('progress')
        if p: print(' ', t['title'], '%s%% (%s/%s)' % (p['pct'], p['current'], p['total']))
"
```

---

## 七、证据索引

- 调研原始记录：`同类项目调研与选型判定.md`（12 竞品依赖实测表）
- 本项目实现：`board/store.py`（唯一写入方、单调闸门、`list_stalled`、`snapshot`）、`board/schema.sql`、`board/config.py`、`server/mcp_server.py`（13 工具）、`web/app.js`
- 待办与判据：`NEXT_STEPS.md` C4 / C5 / C6 / C7 / D1
- MCP 规范：`modelcontextprotocol/modelcontextprotocol` `schema/2025-06-18/schema.ts` → `ProgressNotification`
- 竞品依赖实测文件：`multidimensionalcats/kanban-mcp/pyproject.toml`、`ustoppble/overclick/docker-compose.yml` + `package.json`、`axis-love/flow/pyproject.toml`、`gablabelle/mcp-kanban/package.json`
