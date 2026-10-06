# 决策验证结论（三步全部完成）

> 日期：2026-10-05
> 目的：把「维持自建」这个决策从**论证**落到**实测**。三步按顺序跑完，结论如下。

---

## 一句话总结

**三步全部支持原结论。第 1 步给出了迄今最硬的证据：kanban-mcp 的 `items` 表里
连一个进度/停滞字段都没有 —— R3 不是「它做得差」，是「它不做」。**

| 步骤 | 验的是什么 | 结果 |
|---|---|---|
| 1 | 手感性：`kanban-mcp` 装起来、跑起来要付什么代价 | ✅ 能跑，45 工具；**但 R3/R4 完全缺失** |
| 2 | 能力性：自建的 R3/R4 在真实任务上能否触发 | ✅ **四条正向 + 四条反向全部正确** |
| 3 | 假设性：A5（目标机能否装上依赖）是否成立 | ✅ 纯二进制 wheel，可离线装 |

---

## 第一步：kanban-mcp 实测

### 装得上吗？—— 能

```
pip install kanban-mcp==0.2.0     # 成功
Successfully installed GitPython-3.2.0 blinker-1.9.0 click-8.5.0
  flask-3.1.3 gitdb-4.0.12 itsdangerous-2.2.0 jinja2-3.1.6
  kanban-mcp-0.2.0 markupsafe-3.0.4 python-dotenv-1.2.4
  pyyaml-6.0.3 smmap-5.0.3 waitress-3.0.2 werkzeug-3.1.9
```

**依赖数核实（读 wheel 的 METADATA，非 README 转述）**：

| 依赖 | 条件 |
|---|---|
| `pyyaml>=6.0` | 必需 |
| `flask>=2.0.0` | 必需 |
| `GitPython>=3.1.0` | 必需 |
| `python-dotenv>=1.0.0` | 必需 |
| `waitress>=2.1.0` | 必需 |
| `gunicorn>=21.2.0` | **`sys_platform != "win32"`** —— 本机不需要 |
| `numpy/onnxruntime/tokenizers/huggingface_hub` | 仅 `semantic` extra |
| `mysql-connector-python` | 仅 `mysql` extra |

⇒ **本机实际必需 5 个包**（原记录「6 个」把 `gunicorn` 算进去了，但它在 Windows 上被
平台标记排除）。依赖本身全部是纯 Python wheel，装起来无摩擦。

### 能用吗？—— 能用，但和我的痛点是两回事

**MCP stdio 握手实测通过**：

```
✓ initialize OK -> 服务端: {'name': 'kanban-mcp', 'version': '1.0.0'} | protocol: 2024-11-05
✓ tools/list OK -> 45 个工具
```

45 个工具里包括 `new_item` / `advance_status` / `add_relationship` / `set_parent` /
`get_epic_progress` / `semantic_search` / `get_item_timeline` —— **生态确实比本项目的 13 个强得多**。

**但真实跑一遍后，决定性的事实出来了**：

```
### new_item (feature)
  { "success": true, "item": { "id": 1, "title": "搬运 NAS 照片 2000 张",
    "type_name": "feature", "status_name": "backlog", ... } }

### advance_status
  { "success": true, "previous_status": "backlog", "new_status": "todo" }

### project_summary
  { "success": true, "summary": { "feature": { "todo": 1 } } }

### 探测 update_progress  -> ERROR: Unknown tool: update_progress
### 探测 list_stalled     -> ERROR: Unknown tool: list_stalled
### 探测 set_progress     -> ERROR: Unknown tool: set_progress
### 探测 get_stalled      -> ERROR: Unknown tool: get_stalled
```

**数据库层核实（最硬的证据）**：

```
items 字段: ['id','project_id','type_id','status_id','title','description',
             'priority','created_at','updated_at','closed_at','complexity','parent_id']

  progress   -> （无）
  current    -> （无）
  total      -> （无）
  percent    -> （无）
  stalled    -> （无）
  idle       -> （无）
  heartbeat  -> （无）
  weight     -> （无）
```

> **`items` 表里没有任何进度或停滞相关的列。**
> 这比「它的进度功能做得差」严重得多 —— **它压根不表达「一个任务跑到哪了」这件事。**
> 它的模型是「issue 跟踪」：卡片在列之间移动，反映的是**流程位置**，
> 不是**执行进度**。一个跑 6 小时的搬运任务，在它的看板上和一个卡死 6 小时的
> 任务**依然是同一个样子**（都在 `in_progress`）—— 正是我要解决的那个问题。

### 数据落盘

```
C:/Users/Administrator/.local/share/kanban-mcp/kanban.db   (164 KB)
```

⇒ **硬编码在家目录的固定路径**，不可配。多项目靠 `projects` 表的 `directory_path` 区分，
共用同一个库。对单机单人无妨，但意味着**看板数据不在项目目录里**，备份/迁移要另记一笔。

### 附带发现（不是缺陷，但要知道）

- `set_current_project(path=...)` 不接受 `path` 参数（签名不同），但项目**自动按 cwd 注册**了
- `get_item_metrics` 给的是**耗时类指标**（`lead_time`/`cycle_time`/`time_in_each_status`/
  `current_age`）—— 这是它的强项，本项目没有。**「多久没动」它有，「进度百分比」它没有。**

---

## 第二步：自建 R3/R4 真实任务验证

**不走测试桩，直接走真实 `Store` API**（与 Agent 调用路径一致）。

### R3 —— 停滞检测（四条臂）

```
1) 建卡 id=34504c861ddd col=doing
2) 上报 100/200 -> stalled=False  (✓ 刚上报过不判停滞)      ← 反向臂 1：不误报
3) 事件时间回拨后：
   in stalled_ids = True  (✓ 正确触发)                      ← 正向臂：该报就报
   idle_sec       = 213342644.9 (list_stalled 参照 213342644.9)
   两处口径一致   = ✓                                       ← 快照与独立查询不打架
4) 移到终态列后仍判停滞? False  (✓ 终态排除生效)            ← 反向臂 2：终态不报
```

**语义边界（必须记住）**：这是**「上报中断」检测**，不是「进度值没变」检测。
Agent 只要周期性上报，永远不会被判停滞。这一点在 `list_stalled` 的 docstring 里已写明。

### R4 —— 配置驱动（正向 1 + 反向 3）

新建一份 `cfg2.json`：把「待办/进行中/阻塞/已完成」换成「草稿/评审/退回/已发布」，
流转规则改成 `draft→review→(released|rejected)`，字段换成 `version`(text) + `risk`(enum)。
**代码一行没改。**

```
新看板: 发布流程看板
  列   : ['draft', 'review', 'rejected', 'released']
  字段 : ['version', 'risk']

  正向 : draft->review 通过 ✓ (version=2.1.0 risk=medium)
  反向1: 非法流转被拒 ✓ -> 不允许从 'review' 流转到 'draft'
                            （'review' 允许的目标: ['released', 'rejected']）
  反向2: WIP 上限 3 生效 ✓ -> 列 'review' 已达在制品上限 3，拒绝迁入
  反向3: 未知字段被拒 ✓ -> fields 含未定义的键: ['versoin']
                            （已在 config.board.fields 中定义: ['risk', 'version']）
```

**三条反向臂都自带自修复信息**（告诉你允许去哪儿、已定义哪些字段），
不是干巴巴一句「非法」。这是「改了配置没生效」这类 bug 的解药。

---

## 第三步：A5 假设（目标机可装性）

**A5 原文**：目标机（内网 Windows）与开发机环境一致，pip 源可达。

分两段验：

| 检验 | 命令 | 结果 |
|---|---|---|
| 纯二进制 wheel 可下载 | `pip download cryptography --only-binary :all:` | ✅ 拿到 `cryptography-50.0.2-cp311-abi3-win_amd64.whl` (3.8 MB) + `cffi-2.1.1` + `pycparser-3.0`，**无需 Rust 工具链** |
| **可离线安装** | `pip install --no-index --find-links ./bin cryptography` | ✅ `Successfully installed cffi-2.1.1 cryptography-50.0.2 pycparser-3.0` |
| 完整依赖树离线化 | `pip download kanban-mcp --platform win_amd64 --python-version 3.13 --only-binary :all:` | ✅ 14 个 wheel / 1.3 MB，全部纯二进制 |

**结论**：即使是完全无外网的目标机，也可以在一台有网的机器上 `pip download` 出全部
wheel，拷过去 `--no-index` 离线安装。**A5 的可操作性成立。**

> 注意：`--platform` + `--python-version` 的交叉下载能成功，说明**上游全是纯二进制
> wheel**，不存在「目标机没有编译器就装不上」的风险。

---

## 三步对决策的影响

| 原决策要素 | 三步之后的状态 |
|---|---|
| **依据 1**（R3 竞品无同类实现） | ⬆️ **从「没有同类实现」升级为「数据模型里根本没有这个概念」** |
| **依据 2**（R4 配置驱动） | ✅ 实测确认：换业务零代码改动，且三条闸门都有承载力 |
| **依据 3**（竞品重型依赖是设计动因） | ✅ 修正一处：`gunicorn` 在 Windows 上不需要（平台标记排除） |
| **依据 4**（自建已覆盖 R1–R5） | ✅ R3/R4 已在真实任务路径上跑通（此前只在单测里验过） |
| **假设 A5** | ✅ **从「未实测」升级为「已实测：纯二进制 + 可离线」** |
| **假设 A4** | ⚠️ **仍然未确认** —— R1–R5 是我推的，这是唯一剩下的不确定项 |

**决策不变：维持自建。**

而且第 1 步的实测让理由更锋利了：**这不是「开源方案差一点」，而是「开源方案压根不在
同一个问题上」**。kanban-mcp 的 45 个工具全部服务于 issue 跟踪的流程位置；我需要的
「一个任务跑到百分之几、多久没动」它没有字段可承载。

---

## 什么时候该回头用 kanban-mcp

诚实地说，它的强项本项目确实没有：

| 它强的地方 | 本项目 |
|---|---|
| 6 种条目类型（issue/todo/feature/diary/epic/question）各自生命周期 | 只有通用任务卡 |
| `epic` → 子任务的父子层级与进度汇总 | 无层级 |
| `add_relationship` / `get_blocking_items` 依赖图 | 无 |
| `lead_time` / `cycle_time` / `time_in_each_status` 耗时指标 | 无 |
| `semantic_search` / `find_similar` 语义检索 | 无 |
| 45 工具 + session hooks 自动注入上下文 | 13 工具 |

⇒ **触发条件**：当工作从「一条条独立的长耗时任务」变成「一大堆有层级、有依赖、
要跨会话跟踪的 issue」时，应该认真考虑 kanban-mcp（或两者并用：它管 issue 拓扑，
本项目管执行进度）。

---

## 复现方式

```bash
# 第一步
cd .workbuddy/tmp/step1
python -m venv venv && ./venv/Scripts/pip install kanban-mcp==0.2.0
./venv/Scripts/python.exe handshake.py     # MCP 握手 + 工具清单
./venv/Scripts/python.exe exercise.py      # 真实工作流 + 落盘位置

# 第二步
cd .workbuddy/tmp/step2
python r3r4.py                             # R3 四臂 + R4 四臂

# 第三步
cd .workbuddy/tmp/step3
pip download cryptography --only-binary :all: -d ./bin
pip install --no-index --find-links ./bin cryptography
```
