// ============================================================
// 演示数据：「直接套住」——多种任务形态，**不用任何新字段**
// ------------------------------------------------------------
// 目的：证明挂件现有的五个要素（标题·状态·进度·时间·阻塞）
//       已经能表达这些不同种类的工作。
//
// 这里刻意**只用一个已有字段** `fields.progress`（配置里本来就定义了它），
// 不引入 failed / steps / due_at 等任何新字段。
// 如果某个任务类型在这里表达不出来，说明它属于"要加字段"那一类。
//
// 覆盖的形态（都不是下载）：
//   ① 代码开发      —— 多步骤但只用总进度
//   ② 数据迁移      —— 有明确总量
//   ③ 批量跑批      —— 大批量、长耗时
//   ④ 评测跑批      —— 有明确题数
//   ⑤ 排障（已完成）—— 看 finished_at 与耗时的表达
//   ⑥ 硬件验证（阻塞）—— 看「阻塞」列的表达
//   ⑦ 调研型（无进度）—— 看**没有 progress 时**卡片靠标题+说明撑住
// ============================================================
window.WB_DATA = {
  "_source": "demo",
  "_note": "演示数据（多种任务形态），非真实任务",
  "_fetched_at": null,

  "board": {
    "title": "各类任务一览",
    "refresh_ms": 1000,
    "columns": [
      { "id": "todo",    "title": "待办",   "color": "gray",  "wip_limit": null, "is_terminal": 0 },
      { "id": "doing",   "title": "进行中", "color": "blue",  "wip_limit": 5,    "is_terminal": 0 },
      { "id": "blocked", "title": "阻塞",   "color": "red",   "wip_limit": null, "is_terminal": 0 },
      { "id": "done",    "title": "已完成", "color": "green", "wip_limit": null, "is_terminal": 1 }
    ],
    "tasks_by_column": {
      "todo": [
        {
          "id": "c1",
          "title": "调研型：8B 嵌入模型选型（原生 4096 维，不截断）",
          "description": "候选若干，评测维度：召回率 / 显存占用 / 推理延迟。这类任务没有中间进度，靠标题+说明撑住卡片。",
          "column_id": "todo", "priority": 6,
          "tags": ["embedding", "选型"],
          "fields": {},
          "started_at": "2026-10-05T09:40:00+00:00", "finished_at": null, "idle_sec": 320
        }
      ],
      "doing": [
        {
          "id": "c2",
          "title": "代码开发：RAG-Rerank 服务推理性能优化",
          "description": "瓶颈在 CPU 侧批处理与线程绑定",
          "column_id": "doing", "priority": 10,
          "tags": ["rag", "性能"],
          "fields": { "progress": { "current": 3, "total": 7, "message": "3/7 项优化" } },
          "started_at": "2026-10-05T06:20:00+00:00", "finished_at": null, "idle_sec": 150
        },
        {
          "id": "c3",
          "title": "数据迁移：kb.db → Milvus（13389 chunks）",
          "description": "含 etcd / MinIO 依赖编排与迁移后逐条比对",
          "column_id": "doing", "priority": 8,
          "tags": ["milvus", "迁移"],
          "fields": { "progress": { "current": 8210, "total": 13389, "message": "校验通过 8210/13389" } },
          "started_at": "2026-10-05T05:00:00+00:00", "finished_at": null, "idle_sec": 90
        },
        {
          "id": "c4",
          "title": "批量跑批：全量切片向量重算",
          "description": "分批进行；这类任务的特点是「总量大、耗时以小时计」",
          "column_id": "doing", "priority": 9,
          "tags": ["批处理"],
          "fields": { "progress": { "current": 824, "total": 1410, "message": "824/1410 批" } },
          "started_at": "2026-10-05T07:55:00+00:00", "finished_at": null, "idle_sec": 5100
        },
        {
          "id": "c5",
          "title": "评测跑批：49 题 × 8 类场景 超测集",
          "description": "发布前完整验证 prompt 与检索质量",
          "column_id": "doing", "priority": 9,
          "tags": ["评测"],
          "fields": { "progress": { "current": 31, "total": 49, "message": "31/49 · 0 异常" } },
          "started_at": "2026-10-05T08:05:00+00:00", "finished_at": null, "idle_sec": 5520
        },
        {
          "id": "c6",
          "title": "视频下载：下载「马铃薯男爵」双UP全部视频",
          "description": "88 个视频，落盘 /vol2/1000/A站精华/",
          "column_id": "doing", "priority": 5,
          "tags": ["acfun", "下载"],
          "fields": { "progress": { "current": 37, "total": 88, "message": "0 失败 · 4.16 GiB" } },
          "started_at": "2026-10-05T06:53:00+00:00", "finished_at": null, "idle_sec": 60
        }
      ],
      "blocked": [
        {
          "id": "c7",
          "title": "硬件验证：910B 上 NUMA 亲和性验证",
          "description": "等硬件组排期才能上机",
          "column_id": "blocked", "priority": 7,
          "tags": ["910B", "等排期"],
          "fields": {},
          "started_at": "2026-10-05T06:10:00+00:00", "finished_at": null, "idle_sec": 9000
        }
      ],
      "done": [
        {
          "id": "c8",
          "title": "排障：kb.db 磁盘 I/O 报错定位与规避",
          "description": "定位到并发读写争用；写路径加串行化后观察期内无复发",
          "column_id": "done", "priority": 8,
          "tags": ["sqlite", "排障"],
          "fields": {},
          "started_at": "2026-10-05T02:00:00+00:00",
          "finished_at": "2026-10-05T05:30:00+00:00", "idle_sec": 12000
        }
      ]
    },
    "stats": {
      "total": 8, "finished": 1, "active": 7, "percent": 12.5,
      "agg_progress": 51.0, "stalled": 2, "stall_threshold_sec": 90.0,
      "per_column": { "todo": 1, "doing": 5, "blocked": 1, "done": 1 }
    },
    "stalled_ids": ["c4", "c5"],
    "server_seq": 200,
    "generated_at": "2026-10-05T09:05:00+00:00"
  },

  "events": {
    "c2": [
      { "seq": 199, "task_id": "c2", "kind": "advance", "from_col": null, "to_col": null,
        "actor": "agent", "detail": { "field": "progress", "value": { "current": 3, "total": 7 } },
        "created_at": "2026-10-05T08:58:00+00:00" },
      { "seq": 180, "task_id": "c2", "kind": "create", "from_col": null, "to_col": "todo",
        "actor": "agent", "detail": { "title": "RAG-Rerank 服务推理性能优化" },
        "created_at": "2026-10-05T06:20:00+00:00" }
    ],
    "c3": [
      { "seq": 198, "task_id": "c3", "kind": "advance", "from_col": null, "to_col": null,
        "actor": "agent", "detail": { "field": "progress", "value": { "current": 8210, "total": 13389 } },
        "created_at": "2026-10-05T09:03:00+00:00" }
    ],
    "c8": [
      { "seq": 170, "task_id": "c8", "kind": "move", "from_col": "doing", "to_col": "done",
        "actor": "agent", "detail": null, "created_at": "2026-10-05T05:30:00+00:00" },
      { "seq": 150, "task_id": "c8", "kind": "create", "from_col": null, "to_col": "todo",
        "actor": "agent", "detail": { "title": "kb.db 磁盘 I/O 报错定位与规避" },
        "created_at": "2026-10-05T02:00:00+00:00" }
    ]
  }
};
