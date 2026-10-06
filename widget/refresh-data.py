#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把看板的真实数据导出成 data.js，供原型直接读取。

为什么不在原型里直接 fetch('/api/board')：
  原型要能**双击直接打开**（file:// 协议），而 file:// 下跨域请求
  127.0.0.1:8791 会被浏览器拦掉。
  用 `<script src="data.js">` 注入数据则不受同源策略限制 ——
  代价是数据是快照，刷新数据要重跑本脚本。

用法：
    python refresh-data.py            # 从 127.0.0.1:8791 拉真实数据
    python refresh-data.py --check    # 只报告能否连上，不写文件
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("BOARD_URL", "http://127.0.0.1:8791")
OUT = os.path.join(HERE, "data.js")


def get(path: str, timeout: float = 6.0):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fallback() -> dict:
    """连不上时用内置样例。

    ★ 必须有这一层：原型的用途是"改完立刻看效果"，如果连不上看板就
    白屏或者报错，那迭代会被环境问题打断。用样例数据保证**任何时候
    打开都能看到完整界面**，只是顶部会标注"样例数据"。
    """
    return {
        "_source": "sample",
        "_note": "看板服务未启动，以下为内置样例数据",
        "board": {
            "title": "WorkBuddy Board",
            "columns": [
                {"id": "todo", "title": "待办", "color": "gray", "is_terminal": 0, "wip_limit": None},
                {"id": "doing", "title": "进行中", "color": "blue", "is_terminal": 0, "wip_limit": 5},
                {"id": "blocked", "title": "阻塞", "color": "red", "is_terminal": 0, "wip_limit": None},
                {"id": "done", "title": "已完成", "color": "green", "is_terminal": 1, "wip_limit": None},
            ],
            "tasks_by_column": {
                "todo": [
                    {"id": "s1", "title": "下载「嘻咦啊看」(UID 11842903) 全部视频",
                     "description": "抓到 61 个，页面报 totalCount=70。存在 9~12 个缺口待补齐",
                     "priority": 5, "tags": ["acfun", "nas"], "fields": {},
                     "started_at": "2026-10-05T06:53:58+00:00", "finished_at": None, "idle_sec": 5520},
                    {"id": "s2", "title": "下载「猎影娘」(UID 6499746) 全部视频",
                     "description": "抓到 75 个，与页面报告一致", "priority": 5,
                     "tags": ["acfun"], "fields": {},
                     "started_at": "2026-10-05T06:53:58+00:00", "finished_at": None, "idle_sec": 120},
                ],
                "doing": [
                    {"id": "s3", "title": "下载「马铃薯男爵」双UP全部视频",
                     "description": "UID 8222737 的画廊(72) + UID 55635763 的放映厅(16)，共 88 个视频",
                     "priority": 10, "tags": ["acfun", "nas", "download"],
                     "fields": {"progress": {"current": 37, "total": 88,
                                             "message": "马铃薯男爵的画廊 37/88 · 0 失败 · 4.16 GiB"}},
                     "started_at": "2026-10-05T06:53:58+00:00", "finished_at": None, "idle_sec": 5100},
                ],
                "blocked": [], "done": [],
            },
            "stats": {"total": 3, "finished": 0, "active": 3, "percent": 0.0,
                      "agg_progress": 14.0, "stalled": 2, "stall_threshold_sec": 90.0,
                      "per_column": {"todo": 2, "doing": 1, "blocked": 0, "done": 0}},
            "stalled_ids": ["s1", "s3"],
            "server_seq": 39,
            "generated_at": "2026-10-05T08:00:00+00:00",
        },
        "events": {},
    }


def main() -> int:
    check = "--check" in sys.argv
    try:
        board = get("/api/board")
        stalled = set(board.get("stalled_ids") or [])
        events: dict[str, list] = {}
        # 详情视图需要事件流水；只给有任务的那些列取，避免无谓请求
        for lst in (board.get("tasks_by_column") or {}).values():
            for t in lst:
                try:
                    events[t["id"]] = get("/api/events?limit=12&task_id=%s" % t["id"])["events"]
                except Exception:
                    events[t["id"]] = []
        payload = {"_source": "live", "_fetched_at": datetime.now(timezone.utc).isoformat(),
                   "board": board, "events": events}
        n = board.get("stats", {}).get("total", 0)
        print("✓ 已连上看板：%d 个任务（%d 个停滞）" % (n, len(stalled)))
    except Exception as e:
        if check:
            print("✗ 连不上看板 %s：%s" % (BASE, e))
            return 1
        payload = fallback()
        print("! 连不上看板（%s），改用内置样例数据" % type(e).__name__)

    if check:
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("// 由 refresh-data.py 生成 —— 不要手工改这个文件\n")
        f.write("window.WB_DATA = ")
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    print("  写入 %s（%d 字节，source=%s）" % (OUT, os.path.getsize(OUT), payload["_source"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
