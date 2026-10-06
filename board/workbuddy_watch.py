"""把 WorkBuddy **正在处理的请求**镜像到看板上。

为什么需要它
------------
看板只会知道**被写进去的东西**。而"agent 会不会记得写"这件事 —— 实测靠不住：
用户反复反馈「进行中一直为空」，每一次的根因都是**没人往看板里写**（挂件没坏）。
写进 AGENTS.md、写进 memory 都试过，仍然依赖 agent 自觉，而自觉是靠不住的。

所以这个模块换了个思路：不去指望 agent，而是**直接读 WorkBuddy 自己落盘的会话记录**，
把"用户刚提了一个请求"自动变成一条「进行中」任务，把"这一轮做完了"自动转「已完成」。

数据来源与判别依据（都在真实记录里核过，不是猜的）
--------------------------------------------------
路径：`~/.workbuddy/projects/<工作区名>/<会话id>.jsonl`，一行一个 JSON。

* **新请求** = `{"type":"message","role":"user"}` 且正文含 `<user_query>…</user_query>`。
  实测：某个 656 行的会话里只有 2 条带这个标记 —— 它只出现在**真正的用户输入**里。
  工具回执是单独的 `function_call_result` 类型（不共用 `message`），所以不会误判。
* **这一轮还在忙** = 有 `function_call` 但还没配对的 `function_call_result`（按 `callId` 配对）。
  只要还有未完成调用，就说明 agent 还在干活，此时不该把任务标成完成。
* **这一轮结束** = 既没有未完成调用，文件又已经安静超过 `idle_close_sec` 秒
  （agent 干完在等用户时，记录不再增长）。

硬约束
------
* **只读**会话记录，**绝不写** WorkBuddy 的任何文件。
* **幂等**：靠"读到第几行"的游标记在看板库的 meta 里，重启不会重复建任务。
* **永不抛异常**：这是后台旁路。坏一行 JSON、文件被删、目录不存在，
  都只是"这次没结果"，绝不能把挂件带崩。
* **首次见到文件时不补建历史**：只认最后一条请求（否则一开开关就会把整个会话
  的历史请求一次性灌进看板）。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

DEFAULT_PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "projects")

# 真正的用户输入标记。实测只在真用户消息里出现。
USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.S)

# 镜像出来的任务统一打这个标签，便于和 agent 手工上报的区分开
WATCH_TAG = "WorkBuddy"

# meta 里的游标键
K_STATE = "wbwatch_state"

MAX_TITLE = 60
MAX_DESC = 600

# state 里最多记多少个会话文件的游标。
# ★ 必须设上限：`~/.workbuddy/projects/` 下会越积越多，无上限地记就是内存泄漏。
MAX_TRACKED_FILES = 12


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _text_of(rec: dict) -> str:
    """把一条 message 的正文拼出来（content 可能是 str，也可能是分段 list）。"""
    c = rec.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for part in c:
            if isinstance(part, dict):
                t = part.get("text")
                if isinstance(t, str):
                    out.append(t)
        return "".join(out)
    return ""


def find_latest_transcript(projects_dir: str) -> str | None:
    """找出最近改动的会话记录文件。找不到返回 None。"""
    try:
        best, best_m = None, -1.0
        for name in os.listdir(projects_dir):
            sub = os.path.join(projects_dir, name)
            if not os.path.isdir(sub):
                continue
            try:
                for f in os.listdir(sub):
                    if not f.endswith(".jsonl"):
                        continue
                    fp = os.path.join(sub, f)
                    m = os.path.getmtime(fp)
                    if m > best_m:
                        best, best_m = fp, m
            except OSError:
                continue
        return best
    except OSError:
        return None


def clean_query(raw: str) -> str:
    """把 <user_query> 里的正文收拾成能当标题的样子。

    去掉图片引用、代码块围栏、markdown 记号，压掉多余空白。
    """
    s = raw.strip()
    s = re.sub(r"@image#\d+:\S+", "", s)                 # 图片引用
    s = re.sub(r"<[^>]{1,40}>", "", s)                   # 残留标签
    s = re.sub(r"```[\s\S]*?```", " ", s)                 # 代码块
    s = re.sub(r"^\s*[-*+]\s+", "", s)                    # 行首列表符
    s = re.sub(r"[#>*`]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def make_title(query: str) -> str:
    t = clean_query(query)
    if len(t) > MAX_TITLE:
        t = t[:MAX_TITLE - 1] + "…"
    return t or "（空请求）"


def _terminal_target(store) -> str | None:
    """挑一个终态列用来"收尾"。优先 done，其次任意终态列。"""
    try:
        ids = sorted(store.terminal_ids())
    except Exception:
        return None
    if not ids:
        return None
    return "done" if "done" in ids else ids[0]


def _load_state(store) -> dict:
    raw = None
    try:
        raw = store.meta_get(K_STATE)
    except Exception:
        raw = None
    if not raw:
        return {"files": {}}
    try:
        d = json.loads(raw)
    except Exception:
        return {"files": {}}
    if not isinstance(d, dict):
        return {"files": {}}
    # 兼容旧格式（单个 path/pos/open_task/calls）—— 迁移成按文件存
    if "files" not in d:
        old_path = d.get("path")
        files = {}
        if old_path:
            files[old_path] = {"pos": d.get("pos") or 0,
                               "open_task": d.get("open_task"),
                               "calls": d.get("calls") or {}}
        return {"files": files}
    if not isinstance(d.get("files"), dict):
        d["files"] = {}
    return d


def _save_state(store, st: dict) -> None:
    try:
        store.meta_set(K_STATE, json.dumps(st, ensure_ascii=False))
    except Exception:
        pass


def _close_open(store, ent: dict, why: str) -> str | None:
    """把某个文件当前挂着的那条镜像任务收尾。返回被收尾的 task_id。"""
    tid = ent.get("open_task")
    if not tid:
        return None
    ent["open_task"] = None
    target = _terminal_target(store)
    if not target:
        return None
    try:
        store.move_task(tid, target, actor="workbuddy-watch")
        return tid
    except Exception:
        # 任务可能已经被 agent 自己挪走 / 归档了 —— 不算错
        return None


def poll(store, cfg, now: float | None = None) -> dict[str, Any]:
    """跑一次巡检。返回一个描述本次结果的 dict（绝不抛异常）。

    ★★ 游标必须**按文件各存各的**，这是踩过坑之后的硬要求。
       原先只存了"一个 path + 一个 pos"。而用户可能同时开着多个会话
       （每个工作区一个 jsonl），这些文件会**交替**被写 ⇒ 每次"最新文件"
       一变，就被当成"首次见到" ⇒ 游标归零 ⇒ 把那条请求**重新建一遍**。
       实测现象：同一个标题在「进行中」和「已完成」里反复出现
       （A 建→B 建→A 又建→B 又建…），用户看到的就是"同一任务来回刷"。
       按文件存游标之后，每个会话各自推进，切来切去也不会重放。
    """
    res: dict[str, Any] = {"ok": True, "created": None, "closed": None,
                           "note": "", "busy": False}

    try:
        board = cfg.board
        if not bool(getattr(board, "watch_workbuddy", False)):
            res["note"] = "off"
            return res
        idle_close = float(getattr(board, "watch_idle_close_sec", 120) or 120)
        root = getattr(board, "watch_projects_dir", "") or DEFAULT_PROJECTS_DIR
        root = os.path.expanduser(root)
        ts = now if now is not None else _now_ts()

        path = find_latest_transcript(root)
        if not path:
            res["note"] = "no-transcript"
            return res

        st = _load_state(store)
        files = st.setdefault("files", {})
        ent = files.get(path)
        fresh = ent is None
        if fresh:
            ent = {"pos": 0, "open_task": None, "calls": {}}

        calls = ent.get("calls") or {}
        if not isinstance(calls, dict):
            calls = {}

        # 读新增的行
        queries: list[str] = []
        pos = int(ent.get("pos") or 0)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f):
                    if idx < pos:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue                      # 坏行跳过，不影响整体
                    if not isinstance(rec, dict):
                        continue
                    typ = rec.get("type")
                    if typ == "function_call":
                        cid = rec.get("callId")
                        if cid:
                            calls[str(cid)] = 1
                    elif typ == "function_call_result":
                        cid = rec.get("callId")
                        if cid:
                            calls.pop(str(cid), None)
                    elif typ == "message" and rec.get("role") == "user":
                        m = USER_QUERY_RE.search(_text_of(rec))
                        if m:
                            q = clean_query(m.group(1))
                            if q:
                                queries.append(q)
                    pos = idx + 1
        except OSError as e:
            res["note"] = "read-failed:%s" % type(e).__name__
            return res

        # ★ 首次见到这个文件时**只认最后一条**请求：
        #   否则一打开开关就会把整个会话的历史请求一次性灌进看板。
        if fresh and len(queries) > 1:
            queries = queries[-1:]

        for q in queries:
            # 同一个会话里来了新请求 ⇒ 这个文件上一条已收尾
            if ent.get("open_task"):
                res["closed"] = _close_open(store, ent, "new-request") or res["closed"]
            try:
                t = store.create_task(title=make_title(q), description=q[:MAX_DESC],
                                      column_id="doing", tags=[WATCH_TAG],
                                      actor="workbuddy-watch")
                ent["open_task"] = t.id
                res["created"] = q
            except Exception as e:
                res["note"] = "create-failed:%s" % type(e).__name__

        ent["calls"] = calls
        ent["pos"] = pos
        files[path] = ent

        # 只保留最近见过的若干个会话，避免 state 无限膨胀
        if len(files) > MAX_TRACKED_FILES:
            def seen(p):
                try:
                    return os.path.getmtime(p)
                except OSError:
                    return 0.0
            drop = sorted(files, key=seen)[:len(files) - MAX_TRACKED_FILES]
            for p in drop:
                files.pop(p, None)

        # ★ 收尾判定要**遍历所有文件**，不能只看最新的那个：
        #   一个会话安静下来了，但它可能已经不是"最新文件"了。
        #   条件是"这个文件没有未完成调用 + 文件本身安静够久"。
        #   用文件自己的 mtime，而不是"全局最新文件"的 mtime。
        busy_now = False
        for p, e in list(files.items()):
            if not e.get("open_task"):
                continue
            e_calls = e.get("calls") or {}
            if e_calls:
                busy_now = True
                continue
            try:
                quiet = ts - os.path.getmtime(p)
            except OSError:
                quiet = idle_close + 1.0          # 文件没了 ⇒ 当作早已结束
            if quiet >= idle_close:
                res["closed"] = _close_open(store, e, "idle") or res["closed"]
        # busy 反映"当前这个文件"的状态（界面只关心正在跑的那个）
        res["busy"] = bool(calls) or busy_now

        _save_state(store, st)
    except Exception as e:                                     # noqa: BLE001
        # 后台旁路的铁律：不许把挂件带崩
        res["ok"] = False
        res["note"] = "unexpected:%s: %s" % (type(e).__name__, e)

    return res
