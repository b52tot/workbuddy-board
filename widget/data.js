// 由 refresh-data.py 生成 —— 不要手工改这个文件
window.WB_DATA = {
 "_source": "live",
 "_fetched_at": "2026-10-05T08:50:40.625857+00:00",
 "board": {
  "title": "WorkBuddy Board",
  "refresh_ms": 1000,
  "hide_empty_columns": false,
  "columns": [
   {
    "id": "todo",
    "title": "待办",
    "position": 0,
    "color": "gray",
    "wip_limit": null,
    "is_terminal": 0
   },
   {
    "id": "doing",
    "title": "进行中",
    "position": 1,
    "color": "blue",
    "wip_limit": 5,
    "is_terminal": 0
   },
   {
    "id": "blocked",
    "title": "阻塞",
    "position": 2,
    "color": "red",
    "wip_limit": null,
    "is_terminal": 0
   },
   {
    "id": "done",
    "title": "已完成",
    "position": 3,
    "color": "green",
    "wip_limit": null,
    "is_terminal": 1
   }
  ],
  "tasks_by_column": {
   "todo": [
    {
     "id": "3115ec1d6830",
     "title": "下载「嘻咦啊看」(UID 11842903) 全部视频",
     "description": "抓到 61 个，页面报 totalCount=70 / number=63。存在 9~12 个缺口待补齐。落盘 /vol2/1000/A站精华/嘻咦啊看/",
     "column_id": "todo",
     "position": 1000.0,
     "priority": 5,
     "tags": [
      "acfun",
      "nas",
      "download",
      "数据待查"
     ],
     "fields": {},
     "created_at": "2026-10-05T06:53:58+00:00",
     "updated_at": "2026-10-05T06:53:58+00:00",
     "started_at": "2026-10-05T06:53:58+00:00",
     "finished_at": null,
     "idle_sec": 1398.6,
     "progress": null
    },
    {
     "id": "020076da421b",
     "title": "下载「猎影娘」(UID 6499746) 全部视频",
     "description": "抓到 75 个，与页面报告一致。落盘 /vol2/1000/A站精华/猎影娘/",
     "column_id": "todo",
     "position": 2000.0,
     "priority": 5,
     "tags": [
      "acfun",
      "nas",
      "download"
     ],
     "fields": {},
     "created_at": "2026-10-05T06:53:58+00:00",
     "updated_at": "2026-10-05T06:53:58+00:00",
     "started_at": "2026-10-05T06:53:58+00:00",
     "finished_at": null,
     "idle_sec": 1398.6,
     "progress": null
    }
   ],
   "doing": [
    {
     "id": "325d71a664a7",
     "title": "下载「马铃薯男爵」双UP全部视频",
     "description": "UID 8222737 马铃薯男爵的画廊(72) + UID 55635763 马铃薯男爵的放映厅(16)，共 88 个视频，落盘 /vol2/1000/A站精华/<UP名>/",
     "column_id": "doing",
     "position": 1000.0,
     "priority": 10,
     "tags": [
      "acfun",
      "nas",
      "download"
     ],
     "fields": {
      "progress": {
       "current": 37,
       "total": 88,
       "message": "马铃薯男爵的画廊 37/88 · 0 失败 · 4.16 GiB · 8 并发/16 分片"
      }
     },
     "created_at": "2026-10-05T06:53:58+00:00",
     "updated_at": "2026-10-05T07:01:12+00:00",
     "started_at": "2026-10-05T06:53:58+00:00",
     "finished_at": null,
     "idle_sec": 1398.6,
     "progress": {
      "current": 37.0,
      "total": 88.0,
      "pct": 42.0,
      "message": "马铃薯男爵的画廊 37/88 · 0 失败 · 4.16 GiB · 8 并发/16 分片"
     }
    }
   ],
   "blocked": [],
   "done": []
  },
  "stats": {
   "total": 3,
   "finished": 0,
   "active": 3,
   "percent": 0.0,
   "agg_progress": 14.0,
   "stalled": 3,
   "stall_threshold_sec": 90.0,
   "per_column": {
    "todo": 2,
    "doing": 1,
    "blocked": 0,
    "done": 0
   }
  },
  "fields_spec": [
   {
    "_comment": "progress 示例：长耗时任务用。MCP 用 update_progress 推进；进度只增不减，回退会被显式拒绝",
    "key": "progress",
    "type": "progress",
    "label": "进度"
   }
  ],
  "stalled_ids": [
   "020076da421b",
   "3115ec1d6830",
   "325d71a664a7"
  ],
  "server_seq": 39,
  "generated_at": "2026-10-05T08:50:40+00:00"
 },
 "events": {
  "3115ec1d6830": [
   {
    "seq": 38,
    "task_id": "3115ec1d6830",
    "kind": "delete",
    "from_col": "todo",
    "to_col": null,
    "actor": "full-test",
    "detail": {
     "title": "下载「嘻咦啊看」(UID 11842903) 全部视频"
    },
    "created_at": "2026-10-05T08:27:22+00:00"
   },
   {
    "seq": 10,
    "task_id": "3115ec1d6830",
    "kind": "create",
    "from_col": null,
    "to_col": "todo",
    "actor": "agent",
    "detail": {
     "title": "下载「嘻咦啊看」(UID 11842903) 全部视频"
    },
    "created_at": "2026-10-05T06:53:58+00:00"
   }
  ],
  "020076da421b": [
   {
    "seq": 39,
    "task_id": "020076da421b",
    "kind": "delete",
    "from_col": "todo",
    "to_col": null,
    "actor": "full-test",
    "detail": {
     "title": "下载「猎影娘」(UID 6499746) 全部视频"
    },
    "created_at": "2026-10-05T08:27:22+00:00"
   },
   {
    "seq": 12,
    "task_id": "020076da421b",
    "kind": "create",
    "from_col": null,
    "to_col": "todo",
    "actor": "agent",
    "detail": {
     "title": "下载「猎影娘」(UID 6499746) 全部视频"
    },
    "created_at": "2026-10-05T06:53:58+00:00"
   }
  ],
  "325d71a664a7": [
   {
    "seq": 37,
    "task_id": "325d71a664a7",
    "kind": "delete",
    "from_col": "doing",
    "to_col": null,
    "actor": "full-test",
    "detail": {
     "title": "下载「马铃薯男爵」双UP全部视频"
    },
    "created_at": "2026-10-05T08:27:22+00:00"
   },
   {
    "seq": 17,
    "task_id": "325d71a664a7",
    "kind": "advance",
    "from_col": null,
    "to_col": null,
    "actor": "agent",
    "detail": {
     "field": "progress",
     "value": {
      "current": 37,
      "total": 88,
      "message": "马铃薯男爵的画廊 37/88 · 0 失败 · 4.16 GiB · 8 并发/16 分片"
     }
    },
    "created_at": "2026-10-05T07:01:12+00:00"
   },
   {
    "seq": 16,
    "task_id": "325d71a664a7",
    "kind": "update",
    "from_col": null,
    "to_col": null,
    "actor": "agent",
    "detail": {
     "fields": {
      "progress": {
       "current": 37,
       "total": 88,
       "message": "马铃薯男爵的画廊 37/88 · 0 失败 · 4.16 GiB · 8 并发/16 分片"
      }
     }
    },
    "created_at": "2026-10-05T07:01:12+00:00"
   },
   {
    "seq": 15,
    "task_id": "325d71a664a7",
    "kind": "advance",
    "from_col": null,
    "to_col": null,
    "actor": "agent",
    "detail": {
     "field": "progress",
     "value": {
      "current": 31,
      "total": 88,
      "message": "正在进行：马铃薯男爵的画廊 31/88，0 失败 · 8 并发 / 16 分片"
     }
    },
    "created_at": "2026-10-05T07:00:21+00:00"
   },
   {
    "seq": 14,
    "task_id": "325d71a664a7",
    "kind": "update",
    "from_col": null,
    "to_col": null,
    "actor": "agent",
    "detail": {
     "fields": {
      "progress": {
       "current": 31,
       "total": 88,
       "message": "正在进行：马铃薯男爵的画廊 31/88，0 失败 · 8 并发 / 16 分片"
      }
     }
    },
    "created_at": "2026-10-05T07:00:21+00:00"
   },
   {
    "seq": 11,
    "task_id": "325d71a664a7",
    "kind": "create",
    "from_col": null,
    "to_col": "doing",
    "actor": "agent",
    "detail": {
     "title": "下载「马铃薯男爵」双UP全部视频"
    },
    "created_at": "2026-10-05T06:53:58+00:00"
   }
  ]
 }
};
