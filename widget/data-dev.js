// ============================================================
// 演示数据：代码开发类项目
// ------------------------------------------------------------
// 为什么单独放一个文件：
//   当前真实看板里只有「下载视频」这一类任务，看不出挂件在
//   **代码开发 / 其他项目**里长什么样。
//   这份数据是**演示用**，不是你的真实任务 —— 用来看布局够不够
//   表达「长任务 + 子步骤 + 阻塞」这些开发场景里常见的形态。
//
// 场景刻意覆盖了几种开发任务的形状：
//   · 有多步骤进展的（迁移、优化）—— 看进度条与 message 的表达
//   · 被外部依赖卡住的（等排期、等审批）—— 看"阻塞"列
//   · 无进度的研究型任务（选型调研）—— 看没有 progress 时卡片的样子
//   · 已完成但耗时很长的 —— 看 finished_at 与耗时的呈现
// ============================================================
window.WB_DATA = {
  "_source": "demo",
  "_note": "演示数据（代码开发场景），非真实任务",
  "_fetched_at": null,

  "board": {
    "title": "KnowledgeBuddy · 迭代",
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
          "id": "d1",
          "title": "Milvus 迁移：etcd + MinIO 依赖编排与数据校验脚本",
          "description": "13389 chunks → Milvus；需要 etcd/MinIO 依赖编排 + 迁移后逐条比对向量",
          "column_id": "todo", "priority": 8,
          "tags": ["milvus", "迁移", "基础设施"],
          "fields": {},
          "started_at": "2026-10-05T09:12:00+00:00", "finished_at": null, "idle_sec": 240
        },
        {
          "id": "d2",
          "title": "8B 嵌入模型选型对比（原生 4096 维，不做截断）",
          "description": "候选若干，评测维度：召回率 / 显存占用 / 推理延迟；结论要能支撑向上汇报",
          "column_id": "todo", "priority": 6,
          "tags": ["embedding", "选型", "评测"],
          "fields": {},
          "started_at": "2026-10-05T09:40:00+00:00", "finished_at": null, "idle_sec": 300
        }
      ],
      "doing": [
        {
          "id": "d3",
          "title": "RAG-Rerank 服务推理性能优化",
          "description": "目标：单请求 P95 降到可接受区间；当前瓶颈在 CPU 侧的批处理与线程绑定",
          "column_id": "doing", "priority": 10,
          "tags": ["rag", "性能", "rerank"],
          "fields": {
            "progress": {
              "current": 3, "total": 7,
              "message": "已完成 3/7：批大小调优 · 线程绑定 · tensor 复用"
            },
            "steps": [
              { "name": "批大小调优",    "state": "done" },
              { "name": "线程绑定",      "state": "done" },
              { "name": "tensor 复用",   "state": "done" },
              { "name": "KV cache 复用", "state": "running" },
              { "name": "算子融合",      "state": "pending" },
              { "name": "端到端压测",    "state": "pending" },
              { "name": "出对比报告",    "state": "pending" }
            ]
          },
          "started_at": "2026-10-05T07:20:00+00:00", "finished_at": null, "idle_sec": 150
        },
        {
          "id": "d4",
          "title": "49 题 × 8 类场景 超测集跑批",
          "description": "发布前完整验证 prompt 与检索质量，结果要出成汇报材料",
          "column_id": "doing", "priority": 9,
          "tags": ["评测", "回归"],
          "fields": {
            "progress": { "current": 31, "total": 49, "message": "31/49 · 0 异常" }
          },
          "started_at": "2026-10-05T08:05:00+00:00", "finished_at": null, "idle_sec": 5520
        },
        {
          "id": "d7",
          "title": "全量切片向量重算（分批，可续跑）",
          "description": "整库重算；单批失败不影响后续批次，失败批次单独记下重跑",
          "column_id": "doing", "priority": 9,
          "tags": ["批处理", "离线"],
          "fields": {
            "progress": { "current": 824, "total": 1410, "message": "824/1410 批" },
            "failed": { "count": 2, "message": "批次 311 超时；批次 607 OOM" }
          },
          "started_at": "2026-10-05T07:55:00+00:00", "finished_at": null, "idle_sec": 5100
        }
      ],
      "blocked": [
        {
          "id": "d5",
          "title": "910B 上 NUMA 亲和性验证",
          "description": "需要等硬件组给排期才能上机验证；当前只能先在本地跑通脚本",
          "column_id": "blocked", "priority": 7,
          "tags": ["910B", "部署", "等排期"],
          "fields": {},
          "started_at": "2026-10-05T06:10:00+00:00", "finished_at": null, "idle_sec": 9000
        }
      ],
      "done": [
        {
          "id": "d6",
          "title": "kb.db 磁盘 I/O 报错定位与规避",
          "description": "定位到并发读写争用；已在写路径上加串行化，观察期内无复发",
          "column_id": "done", "priority": 8,
          "tags": ["sqlite", "排障"],
          "fields": {},
          "started_at": "2026-10-05T02:00:00+00:00",
          "finished_at": "2026-10-05T05:30:00+00:00", "idle_sec": 12000
        }
      ]
    },
    "stats": {
      "total": 7, "finished": 1, "active": 6, "percent": 14.3,
      "agg_progress": 54.9, "stalled": 3, "stall_threshold_sec": 90.0,
      "per_column": { "todo": 2, "doing": 3, "blocked": 1, "done": 1 }
    },
    "stalled_ids": ["d4", "d5", "d7"],
    "server_seq": 128,
    "generated_at": "2026-10-05T08:54:00+00:00"
  },

  "events": {
    "d3": [
      { "seq": 128, "task_id": "d3", "kind": "advance", "from_col": null, "to_col": null,
        "actor": "agent", "detail": { "field": "progress", "value": { "current": 3, "total": 7 } },
        "created_at": "2026-10-05T08:40:00+00:00" },
      { "seq": 121, "task_id": "d3", "kind": "update", "from_col": null, "to_col": null,
        "actor": "agent", "detail": { "fields": { "progress": { "current": 2 } } },
        "created_at": "2026-10-05T08:12:00+00:00" },
      { "seq": 110, "task_id": "d3", "kind": "move", "from_col": "todo", "to_col": "doing",
        "actor": "agent", "detail": null, "created_at": "2026-10-05T07:20:00+00:00" },
      { "seq": 104, "task_id": "d3", "kind": "create", "from_col": null, "to_col": "todo",
        "actor": "agent", "detail": { "title": "RAG-Rerank 服务推理性能优化" },
        "created_at": "2026-10-05T07:02:00+00:00" }
    ],
    "d4": [
      { "seq": 127, "task_id": "d4", "kind": "advance", "from_col": null, "to_col": null,
        "actor": "agent", "detail": { "field": "progress", "value": { "current": 31, "total": 49 } },
        "created_at": "2026-10-05T08:35:00+00:00" },
      { "seq": 112, "task_id": "d4", "kind": "move", "from_col": "todo", "to_col": "doing",
        "actor": "agent", "detail": null, "created_at": "2026-10-05T08:05:00+00:00" }
    ],
    "d5": [
      { "seq": 118, "task_id": "d5", "kind": "move", "from_col": "doing", "to_col": "blocked",
        "actor": "user", "detail": null, "created_at": "2026-10-05T07:45:00+00:00" },
      { "seq": 101, "task_id": "d5", "kind": "create", "from_col": null, "to_col": "doing",
        "actor": "user", "detail": { "title": "910B 上 NUMA 亲和性验证" },
        "created_at": "2026-10-05T06:10:00+00:00" }
    ],
    "d6": [
      { "seq": 126, "task_id": "d6", "kind": "move", "from_col": "doing", "to_col": "done",
        "actor": "agent", "detail": null, "created_at": "2026-10-05T05:30:00+00:00" },
      { "seq": 99, "task_id": "d6", "kind": "create", "from_col": null, "to_col": "todo",
        "actor": "agent", "detail": { "title": "kb.db 磁盘 I/O 报错定位与规避" },
        "created_at": "2026-10-05T02:00:00+00:00" }
    ]
  }
};
