# 桌面挂件 —— 同类项目调研清单

> 日期：2026-10-05 ｜ 目的：摸清"看板挂件"这件事在开源生态里的现状
> **立场：这份文件只摆事实 + 标出可复用件，结论与取舍留给你。**

---

## 〇、调研覆盖范围

| 渠道 | 关键词/入口 | 命中 |
|---|---|---|
| GitHub Search / Topics | kanban widget desktop / desktop-widget topic / tray topic | 多轮 |
| awesome 列表 | awesome-tauri（Productivity 段） | 多个候选 |
| 垂直生态 | DSH 插件市场、OpenClaw 生态、Claude Code / Cursor 生态 | 多个 |
| 中文社区 | V2EX、稀土掘金、CSDN、阮一峰周刊自荐区 | 4 个 |
| 聚合站 | besthub / wpdoze / howtodeploy / glama（MCP 站） | 3 篇横评 |
| 设计参考 | Dribbble、glassmorphism 设计站 | 视觉方向 |
| 直读源码/README | raw.githubusercontent.com 直读（**不看二手转述**） | 多个 |

**合计命中 31 个有效项目。** 下面按类型分组。

---

## 一、A 类：桌面看板 / 挂件（形态最接近）

| 项目 | 技术栈 | 体积 | 许可 | 关键点 | 缺口 |
|---|---|---|---|---|---|
| **Chronica**：Local Kanban Board Widget | Tauri 2 + React | **11.7 MB 安装包** | GPL-3.0 | 桌面便签式看板、多板、拖拽、自定义列/字段、always-on-top 开关、托盘、开机自启、亮/暗/纯黑主题、透明度 | **无树、无自动收起展开** |
| **desk-widgets**（ferralina） | Electron | — | 开源 | 磨砂玻璃、**拖到屏幕边缘自动贴边变半透明 + 悬停恢复**、置顶 Pin 时禁用吸附；**"阶段目标"组件能按子任务自动算进度**；每个 widget 是独立 HTML，**改完保存窗口热更新** | 偏便签/日历，非任务树 |
| **Widgetly** | Electron | — | 开源 | 20+ 组件中心，含 **Agent Usage Monitor**（token/成本/会话排行） | 组件中心的形态 |
| **schedule**（Nicercz007-cloud） | 纯 vanilla DOM，**零依赖** | — | 开源 | 毛玻璃悬浮卡片、可拖动可缩放、位置记忆；**目标设置里有"展开自由流程图工作台"** | 非看板 |
| **Kanchi** | 纯 vanilla JS | — | — | 物理漂浮卡片（教学示范性质） | 不适合生产 |
| **floweb** | — | — | — | **"超轻量悬浮挂件，把网页变成应用"**，支持置顶与透明 | 形态参考 |

### ★ Chronica 是我查到最接近"可直接用"的一个

它的数据是**纯 JSON 文件**，且**外部程序可直接读写**：

```
%UserProfile%\Documents\Chronica\Boards\{boardId}\
   board.json                        看板清单（名称 + 卡片顺序）
   columns.json                      列定义
   fields.json                       看板级自定义字段
   cards\card-{encodedCardId}.json   一张卡一个文件
   summary.md                        AI 可读的看板摘要（sidecar）
```

它还专门做了 **"AI-Optimized JSON"** 导入导出（嵌套 `columns[].cards[]` + 统计信息），
并明确写了"导入时自动识别标准格式或 AI 优化格式"。

⇒ **理论上：把看板数据写成它的 JSON 格式，挂件就直接有了**（含拖拽、托盘、自启、主题）。
⇒ **代价**：它的视觉是"便签看板"，不是你要的树；且没有"自动收起/展开"。

---

## 二、B 类：Agent 状态挂件（**交互与视觉的富矿**）

这一类比 A 类多得多，因为它解决同一个心理需求：**"我开了长任务走开，回来怎么一眼知道跑到哪了"**。

| 项目 | 星 | 技术栈 | 形态 | 值得抄的点 |
|---|---|---|---|---|
| **Star Office UI** | **⭐7,162** | Phaser + Flask | 像素风办公室场景，agent = 桌面角色 | 把状态**场景化/游戏化**做到极致；多语言；每 agent 一个 avatar 带动画 |
| **Clawd on Desk** | **⭐2,892** | Electron + SVG | 像素桌宠，屏幕角落 | 状态→动作映射（thinking 冒气泡 / typing 敲键盘 / building 戴安全帽 / 完成举旗 / 空闲打瞌睡）；**权限气泡（Allow/Deny）**；支持 10+ agent 含 CodeBuddy |
| **OrbCue** | — | Rust + Tauri 2 + Svelte | **Windows 悬浮小球** | **贴边收起 + 调透明度 + 全局快捷键 + 多套主题**；hook 事件驱动（轮询→中断）；推送手机(ntfy)；**点一下跳回对应终端**；380px 面板 |
| **PILLAR** | — | Tauri 2 + React | 顶部居中 Dynamic Island | **hover 预览 / click 展开**；**隐藏于任务栏与 Alt+Tab**；全屏应用时自动隐藏；**idle 时轮询自动休眠**；**构建产物直接是 NSIS + MSI** |
| **AgentPulse** | — | Electron + React | 400px 置顶悬浮 | **旁路观察**：扫进程树 + TCP 端口找到目标 server，走 HTTP/SSE 取数，**不注入不改配置** |
| **Agent Status**（gc0106） | — | Electron | 双列状态面板 | 状态灯语义：🔴需处理 / ⛔异常 / 🟡运行中 / 🟢空闲；**文件监听 + 缓存，空闲时降低扫描频率** |
| **agent-traffic-light**（V2EX） | — | **Python + tkinter，零第三方依赖** | 三色灯卡片 | **UDP 单包通信**（`127.0.0.1:18888`）+ **每 3 秒查一次绑定 PID，进程没了自动标灰** |
| **satchel** | — | Electron | 终端窗口管理 | **停靠成任务栏条带** |
| **ClawMonitor** | — | Electron | 霓虹系统监控 | **点击穿透**；**"reserves screen space"（预留屏幕空间，不遮挡）** |
| **cursor-overlay**（Hormold） | — | Electron + Preact + Tailwind | 透明浮层 | 读 Cursor 的 SQLite + Claude 的 JSONL |
| **Talon**（V2EX） | — | Tauri + Lottie | macOS 菜单栏浮层 | Lottie 动画做状态切换 |
| claude-code-status-card / ClawMeter / tuotuo | — | Electron | 各类挂件 | token 计费 HUD、限额倒计时、宠物化 |

### 视觉实测（我抓的实机图）

| 图 | 项目 | 评价 |
|---|---|---|
| `refs/orbcue-shot1.png` | OrbCue 面板 | **目前最贴合"挂件面板"的视觉基准**：380px 宽、顶部大数字抬头（`0/3 需要你`）、分段筛选（全部/工作中/未工作）、**按项目分组**、状态胶囊（空闲/等待输入）、底部四页签 |
| `refs/staroffice-0.jpg` | Star Office UI | 像素场景化做到极致（agent 是办公室里的角色 + 状态气泡 + 门牌），但**游戏感强，不像工具** |
| `refs/tasktree.png` | Task Tree Visualization | 树+进度汇总**功能对**，但视觉是**老式表格**（浅色、红绿刺眼、无暗色主题）——**这个视觉水平不能直接用** |

---

## 三、C 类：任务看板 / 仪表盘（Web 形态，可借架构）

| 项目 | 星 | 技术栈 | 值得抄的点 |
|---|---|---|---|
| **task-dashboard**（skarL007） | — | **纯标准库 Python** | ★ **ETag / 304 条件轮询**——"每 20 秒把 9.2KB 状态发给模型 ≈ 41 万 input token/小时，而这样成本为零"；**MCP 工具 + 浏览器面板同源**；8 layouts × 8 主题；严格只读；只绑 127.0.0.1 |
| **Mission Control**（crshdn） | ⭐440 | Next.js + WebSocket + SQLite | Kanban 七阶段流；**WebSocket 实时推送**而非轮询；跨机（Tailscale） |
| **LobsterBoard** | ⭐382 | 单文件 Node，无构建 | **拖拽式仪表盘 + 50 widgets 模板**；SSE 自动刷新；配置存单个 JSON |
| **Clawe** | ⭐261 | TypeScript | Trello-like 小队协作 |
| **ClawDeck** | — | — | **kanban + 完整 REST API**（外部可驱动） |
| **kontor / Agent Dashboard**（lx-wnk） | — | — | 零配置监控（扫进程 + 读 `CLAUDE_CONFIG_DIR`）；**MCP 控制面**；SSE |
| **VidClaw** | ⭐67 | JavaScript | 任务板 + usage 追踪 |

---

## 四、D 类：树状 / 层级可视化（**你点名要的那块**）

| 项目 | 技术栈 | 关键点 |
|---|---|---|
| **dsh-task-board**（etony668） | DSH 插件（Node ESM + web bundle） | ★ **父子任务树** + **任务边界四字段（goal / scope / out of scope / acceptance）** + **父任务在所有子任务完成时自动关闭，加回未完成子任务则自动重开祖先**；存储 `<project>/.dsh-taskboard/board.json`（**随项目走、可提交**）+ 全局镜像；**每次改动先快照上一版（保留 10 版）**；**原子写**；CodexFF 兼容格式；**agent 工具**：`board_get`/`board_revision`/`board_sync`（批量）/`task_create`/`task_update`/`task_delete`；MIT |
| **Task Tree Visualization**（heitorgiacomini） | D3 v7 + jsTree，纯前端 | **双视图**：Directory 表格 / Diagram（D3 SVG）；**父节点进度由子节点自动汇总**；节点饼图式进度；**双击折叠/展开子树**；JSON 导入导出；导出 PNG/SVG/MD |
| **gsd-2 Workflow Visualizer** | TUI + HTML 导出 | 进度树 `milestone → slice → task`，✅ 完成 / ⏳ 进行 / ⬜ 待办；**依赖图（ASCII DAG）**；成本/token 条形图；**时间线**；HTML 导出内联全部 CSS/JS |
| **manaflow TaskTree** | Electron / web | **agent 编号（1.2.3 层级序）**；按状态换前导图标（皇冠/PR/勾/叉/云/监视器） |

> **注意**：`dsh-task-board` 是 **DSH（DeepSeek Harness）的插件**——它出现在 DSH web UI 的
> `Chat → Trajectory → Task Board` 标签页里，**不是独立的桌面悬浮挂件**。
> 你机器上装了 DSH（桌面有 `DeepSeek Harness.lnk`），所以它是**可以立刻装上看的**。

---

## 五、E 类：交互范式（不解决数据，但决定手感）

| 项目 | 范式 |
|---|---|
| **PILLAR** | **hover 预览 → click 展开**（比"自动弹开"更克制）；隐藏于任务栏/Alt+Tab；全屏时自动隐藏 |
| **OrbCue** | 小球**贴边收起**，鼠标过去才展开 |
| **desk-widgets** | 拖到边缘自动贴边变半透明，**悬停恢复**；置顶时自动禁用吸附 |
| **ClawMonitor** | **点击穿透**（不挡下面的操作）+ **预留屏幕空间**（不遮挡内容） |
| **agent-traffic-light** | 进程没了自动标灰（**判活用 PID，不用心跳**） |

---

## 六、★ 缺口分析：有没有"直接可用"的？

**结论：没有一个是"开箱即用"的。** 具体缺在哪：

| 你需要的 | 生态现状 |
|---|---|
| 读**我的看板**的数据（SQLite / HTTP API） | 所有挂件都读自己的存储格式；**没有一个能直接读别人的库** |
| **树状**展示任务 | 有现成（D3 / jsTree / TreeView），但都在 Web 或 TUI 里，**没人在桌面挂件里做树** |
| **无任务收起 / 有任务展开** | PILLAR 是"hover/click 展开"，**没有一个是按任务数自动收展的** |
| **可复制分发的安装包** | Chronica / PILLAR 有现成打包链（Tauri → NSIS/MSI） |
| 展开节点看**每一步的具体内容** | `gsd-2` 的时间线 / `dsh-task-board` 的任务边界字段最接近 |

**但每一块部件都有现成实现**，这是可复用的地方：

| 部件 | 抄谁 |
|---|---|
| 父子任务树 + 边界字段 + 自动关闭语义 | **dsh-task-board**（数据模型最完整） |
| 进度自动汇总 + 折叠展开 + 双视图 | **Task Tree Visualization** |
| 挂件形态 + 贴边收起 + 状态胶囊 | **OrbCue** |
| hover 预览 / click 展开 + 全屏隐藏 + idle 休眠 | **PILLAR** |
| **构建即得安装包** | **Chronica / PILLAR**（Tauri → NSIS + MSI） |
| 外部程序驱动挂件 | Chronica（JSON 文件）/ ClawDeck（REST）/ dsh-task-board（board.json 原子写） |
| 零依赖最轻形态 | agent-traffic-light（Python + tkinter + UDP） |
| 省流轮询 | task-dashboard（ETag / 304） |

---

## 七、我的建议（**标为建议，不是结论**）

**三条路，代价不同：**

1. **装上试**（今天就能做）：
   `dsh-task-board`（你机器有 DSH，一条命令）+ `Chronica`（11.7MB MSI）。
   目的是**摸手感**——看它们的树/看板交互能不能接受，而不是采纳。

2. **改造**：fork **Chronica**（Tauri，有打包链、有托盘自启主题），
   把它的"便签看板"换成树视图，数据源改成读你的看板。
   省掉的是：窗口/托盘/自启/打包/主题**全部现成**。

3. **自建**：Tauri 2 + 复用上面的部件设计。
   控制力最强，但窗口层、打包链、自动更新都要自己搭。

**我倾向 2**，理由是它把"最不值得自己写的部分"（安装包、自动更新、托盘、多主题）直接继承了。
但这条**依赖你认可它的视觉与交互基底** —— 所以建议先做第 1 步。

---

## 附：抓到的视觉参考图

路径：`.workbuddy/tmp/refs/`

| 文件 | 内容 |
|---|---|
| `orbcue-shot1.png` | OrbCue 面板（**视觉基准**） |
| `staroffice-0.jpg` / `staroffice-1.jpg` | Star Office UI 像素场景（⭐7162） |
| `clawd-shot0.gif` | Clawd on Desk 主视觉（⭐2892） |
| `tasktree.png` / `tasktree-diagram.png` | Task Tree 的 Directory / D3 两种视图 |
| `chronica.png` | Chronica 商店页（含截图） |

---

## 附：调研中发现的、与你现有工作的交集

- **`task-dashboard`（skarL007）的 ETag/304 思路**：可直接用到你现在的 `/api/board` 上——
  挂件 2 秒轮询一次 9KB 快照是浪费，加 ETag 后绝大多数请求返回 304。
- **`agent-traffic-light` 的判活方式**：**查 PID 而不是看日志 mtime**——
  与我在项目里记的"判活不能用日志 mtime，空闲几十秒不写一行很正常"是同一条教训，**独立两个人得出同一结论**。
- **`desk-widgets` 的"每个 widget 是独立 HTML，改完保存窗口热更新"**：
  对你"后续慢慢优化"这个诉求很友好——改样式不用重新打包。
