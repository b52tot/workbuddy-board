#!/usr/bin/env python3
"""WorkBuddy Board — MCP stdio server。

把看板能力接进 WorkBuddy / 任何支持 MCP 的 Agent。

设计约束（刻意为之，别改）
--------------------------
* **协议层手写 JSON-RPC，不依赖 `mcp` SDK**：PyPI 的 `mcp>=1.0.0` 已拉到 2.x，
  FastMCP 在 2.x 更名为 MCPServer，照抄旧写法必崩。stdio JSON-RPC 只有
  initialize / tools/list / tools/call 三个方法，手写反而稳定、零依赖。
* **工具名不带 `mcp__` 前缀**：前缀由宿主（WorkBuddy）注册时自动添加。
  这里只写裸名 `create_task` / `move_task` / `board_snapshot` …，
  否则宿主侧会出现 `mcp__board__mcp__board__create_task` 这类重复前缀。
* **配置只从 config.json 读，绝不写进宿主 mcp.json 的 env 段**：WorkBuddy 的
  MCP 连接指纹把 env 的 **key 集合**算在内，往 env 里加 key 会让已授信的
  server 退回待授信、下次启动重新弹授权。要改端口/路径就改 config.json。
* **写操作一律经由 Store**：看板数据的唯一写入方。这样每次变更都自动登记
  events，任何时刻都能回答「这个任务是怎么走到现在这一步的」。
* **失败一律显式报错**，绝不返回假的成功。静默失败比报错危险。

用法：由 WorkBuddy 以 stdio 方式拉起，不需要手工运行。
自检：python -m server.mcp_server --selftest   （只走协议层，不碰数据库）
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

# 允许以「脚本」或「模块」两种方式运行（便于自检与被宿主拉起）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board.config import ConfigError, load_config  # noqa: E402
from board.models import StateError, read_progress  # noqa: E402
from board.store import STALL_SECONDS, Store, StoreError  # noqa: E402

SERVER_NAME = "workbuddy-board"
SERVER_VERSION = "1.0.0"

store: Store | None = None  # 进程内单例，懒加载


# ---------------------------------------------------------------- 基础设施

def get_store() -> Store:
    global store
    if store is None:
        store = Store(load_config())
    return store


def send(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def send_result(rid: Any, result: Any) -> None:
    send({"jsonrpc": "2.0", "id": rid, "result": result})


def send_error(rid: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def ok(payload: Any, text: str | None = None) -> dict[str, Any]:
    body = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": (text + "\n" if text else "") + body}]}


def fail(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


# ---------------------------------------------------------------- handlers

def h_board_snapshot(a: dict[str, Any]) -> dict[str, Any]:
    snap = get_store().snapshot()
    if a.get("compact"):
        # 任务很多时（KPI 型看板可能上千个）只回 id/标题，显著减小返回体积
        snap["tasks_by_column"] = {
            cid: [{"id": t["id"], "title": t["title"], "column_id": t["column_id"]}
                  for t in lst]
            for cid, lst in (snap.get("tasks_by_column") or {}).items()
        }
        snap["compact"] = True
    return ok(snap)


def h_board_config(a: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    return ok({
        "source": cfg.source,
        "title": cfg.board.title,
        "columns": cfg.board.columns,
        "transitions": cfg.board.transitions,
        "fields": cfg.board.fields,
        "enforce_wip": cfg.board.enforce_wip,
        "web": {"host": cfg.web.host, "port": cfg.web.port},
        "db_path": cfg.board.db_path,
    })


def h_create_task(a: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    t = get_store().create_task(
        title=a.get("title", ""),
        column_id=a.get("column_id"),
        description=a.get("description"),
        tags=a.get("tags"),
        fields=a.get("fields"),
        priority=int(a.get("priority") or 0),
        task_id=a.get("task_id"),
        actor=a.get("actor") or "agent",
    )
    col = cfg.column_by_id(t.column_id) or {}
    return ok(t.to_dict(), text="已创建任务 %s，位于「%s」" % (t.id, col.get("title", t.column_id)))


def h_move_task(a: dict[str, Any]) -> dict[str, Any]:
    tid = a.get("task_id")
    if not tid:
        return fail("缺少参数 task_id")
    to_col = a.get("to_column") or a.get("column_id")
    if not to_col:
        return fail("缺少参数 to_column")
    t = get_store().move_task(tid, to_col,
                              before_task_id=a.get("before_task_id"),
                              actor=a.get("actor") or "agent")
    return ok(t.to_dict(), text="任务 %s 已移动到 %s" % (t.id, t.column_id))


def h_update_task(a: dict[str, Any]) -> dict[str, Any]:
    tid = a.get("task_id")
    if not tid:
        return fail("缺少参数 task_id")
    t = get_store().update_task(
        tid,
        title=a.get("title"),
        description=a.get("description"),
        tags=a.get("tags"),
        fields=a.get("fields"),
        priority=(int(a["priority"]) if a.get("priority") is not None else None),
        actor=a.get("actor") or "agent",
    )
    return ok(t.to_dict(), text="已更新任务 %s" % t.id)


def h_delete_task(a: dict[str, Any]) -> dict[str, Any]:
    tid = a.get("task_id")
    if not tid:
        return fail("缺少参数 task_id")
    get_store().delete_task(tid, actor=a.get("actor") or "agent")
    return ok({"deleted": tid}, text="已删除任务 %s" % tid)


def h_get_task(a: dict[str, Any]) -> dict[str, Any]:
    tid = a.get("task_id")
    if not tid:
        return fail("缺少参数 task_id")
    return ok(get_store().get_task(tid).to_dict())


def h_list_tasks(a: dict[str, Any]) -> dict[str, Any]:
    """列出任务。默认不含已归档的（完成已久、已挪出主视图）。"""
    ts = get_store().list_tasks(column_id=a.get("column_id"), tag=a.get("tag"),
                                include_archived=bool(a.get("include_archived")))
    return ok({"count": len(ts), "tasks": [t.to_dict() for t in ts]})


def h_archive_done(a: dict[str, Any]) -> dict[str, Any]:
    """把完成已久的终态任务归档出主视图（**只打标记，不删数据**）。

    平时不需要手动调：看板快照会自动巡检并归档（见 board.store.archive_done）。
    这个工具是给"想立刻清一下"或"想换一个保留天数"的场景用的。
    """
    s = get_store()
    r = s.archive_done(older_than_days=a.get("older_than_days"), force=True)
    n = r.get("archived") or 0
    return ok(r, text=("已归档 %d 个任务（保留期 %s 天）"
                       % (n, r.get("older_than_days"))) if n
              else "没有需要归档的任务")


def h_restore_archived(a: dict[str, Any]) -> dict[str, Any]:
    """把归档的任务还原回主视图。给 task_id 就还原那一个，不给就全部还原。

    ★ 有这个入口，"归档"才不是"删除"的委婉说法 —— 用户随时能拿回来。
    """
    s = get_store()
    r = s.restore_archived(a.get("task_id"))
    return ok(r, text="已还原 %d 个任务" % (r.get("restored") or 0))


def h_list_columns(a: dict[str, Any]) -> dict[str, Any]:
    return ok({"columns": get_store().list_columns()})


def h_update_progress(a: dict[str, Any]) -> dict[str, Any]:
    """推进任务的进度字段。

    设计要点
    --------
    * **只增不减由 Store 兜底**，这里不做二次判断 —— 所有写入路径共用同一道闸门，
      才不会出现「某个入口能绕过」。
    * 进度值以 events 形式留痕：推进本身也是「这个任务怎么走到现在的」的一部分。
    * `current` 直接给绝对值（而非增量），因为增量语义在重试/重复投递下会累加错，
      而绝对值是幂等的 —— 网络重试不会把进度推两遍。
    """
    tid = a.get("task_id")
    if not tid:
        return fail("缺少参数 task_id")
    key = a.get("field") or a.get("key")
    cur = a.get("current")
    if cur is None:
        return fail("缺少参数 current（已完成的量，绝对值）")

    s = get_store()
    spec = None
    if key:
        for f in s.cfg.board.fields:
            if f["key"] == key and f.get("type") == "progress":
                spec = f
                break
        if spec is None:
            avail = [f["key"] for f in s.cfg.board.fields if f.get("type") == "progress"]
            return fail("字段 %r 不是 progress 类型（可用的进度字段: %s）"
                        % (key, ", ".join(avail) or "无 —— 请先在 config.board.fields "
                           "里声明 {\"type\": \"progress\"}"))
    else:
        cands = [f for f in s.cfg.board.fields if f.get("type") == "progress"]
        if not cands:
            return fail("配置里没有任何 progress 字段。请先在 config.board.fields 中"
                        "声明，例如 {\"key\": \"progress\", \"type\": \"progress\", "
                        "\"label\": \"进度\"}")
        spec = cands[0]
        key = spec["key"]

    payload: dict[str, Any] = {"current": cur}
    if a.get("total") is not None:
        payload["total"] = a["total"]
    if a.get("weight_bytes") is not None:
        payload["weight_bytes"] = a["weight_bytes"]
    if a.get("message") is not None:
        payload["message"] = a["message"]
    if a.get("done") is not None:
        payload["done"] = bool(a["done"])

    t = s.update_task(tid, fields={key: payload}, actor=a.get("actor") or "agent")
    got = (t.fields or {}).get(key)
    # 显示文本从**任务上实际存下来的值**算，不用入参 —— 入参可能只给了 current，
    # 直接拼会打出「None% (300/None)」这种自己都说服不了的输出。
    shown = read_progress(spec, got)
    # 进度推进要单独记一条 advance 事件：update 事件只说明「改过」，
    # 而复盘时需要看到「推进到了哪个值」
    s.note_progress(tid, key=key, value=got if isinstance(got, dict) else {}, actor=a.get("actor") or "agent")

    if shown is None:
        return ok({"task_id": tid, "field": key, "progress": None,
                   "stalled": tid in {x["id"] for x in s.list_stalled()}},
                  text="任务 %s 的「%s」已更新，但当前缺 total，无法计算百分比"
                       % (tid, spec.get("label", key)))
    return ok({"task_id": tid, "field": key, "progress": shown,
               "stalled": tid in {x["id"] for x in s.list_stalled()}},
              text="任务 %s 的「%s」已推进到 %s%%（%s/%s）%s"
                   % (tid, spec.get("label", key), shown["pct"],
                      shown["current"], shown["total"],
                      "（已完成）" if shown.get("done") else ""))


def h_list_stalled(a: dict[str, Any]) -> dict[str, Any]:
    """列出疑似停滞的任务。

    语义边界要讲清楚，否则调用方会误判：这里检的是「**上报中断**」——
    非终态列 + 超过阈值没有任何写入。因此调用方必须**周期性**地调用
    update_progress 上报，否则长任务会被误标。
    """
    th = a.get("threshold_sec")
    st = get_store().list_stalled(threshold_sec=float(th) if th is not None
                                  else STALL_SECONDS, now=a.get("now"))
    return ok({"count": len(st), "threshold_sec": float(th) if th is not None
               else STALL_SECONDS, "stalled": st})


def h_list_events(a: dict[str, Any]) -> dict[str, Any]:
    evs = get_store().list_events(limit=int(a.get("limit") or 100),
                                  task_id=a.get("task_id"),
                                  since_seq=int(a.get("since_seq") or 0))
    return ok({"count": len(evs), "events": evs})


def h_board_reset(a: dict[str, Any]) -> dict[str, Any]:
    """清空所有任务。**故意要求显式 confirm=true** —— 防止误调用清掉看板。"""
    if a.get("confirm") is not True:
        return fail("这是破坏性操作。确认要清空全部任务请传 confirm=true")
    s = get_store()
    ts = s.list_tasks()
    for t in ts:
        s.delete_task(t.id, actor=a.get("actor") or "agent")
    return ok({"deleted": len(ts)}, text="已清空 %d 个任务" % len(ts))


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "board_snapshot": h_board_snapshot,
    "board_config": h_board_config,
    "create_task": h_create_task,
    "move_task": h_move_task,
    "update_task": h_update_task,
    "delete_task": h_delete_task,
    "get_task": h_get_task,
    "list_tasks": h_list_tasks,
    "list_columns": h_list_columns,
    "update_progress": h_update_progress,
    "list_stalled": h_list_stalled,
    "list_events": h_list_events,
    "archive_done": h_archive_done,
    "restore_archived": h_restore_archived,
    "board_reset": h_board_reset,
}


# ---------------------------------------------------------------- schemas

def tool(name: str, desc: str, props: dict[str, Any] | None = None,
         required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": desc,
        "inputSchema": {"type": "object",
                        "properties": props or {},
                        "required": required or []},
    }


TOOLS = [
    tool("board_snapshot",
         "读取看板完整快照：栏目定义、每列下的任务、统计（总数/已完成/百分比）。"
         "想看当前进展、或要渲染看板时用它。compact=true 时只返回任务 id 与标题，"
         "任务很多时可显著减小返回体积。",
         {"compact": {"type": "boolean",
                      "description": "只返回 id/title/column_id，默认 false"}}),
    tool("board_config",
         "读取当前生效的看板配置：栏目、合法流转、自定义字段、数据库路径、Web 端口。"
         "在决定「能移到哪一列」「有哪些自定义字段」之前先看它。"),
    tool("create_task",
         "在看板上新建一个任务。column_id 省略时放到第一列；"
         "fields 只接受在配置 board.fields 中定义过的键，传未定义的键会报错。",
         {"title": {"type": "string", "description": "任务标题（必填，≤500 字）"},
          "column_id": {"type": "string", "description": "目标列 id，省略则用第一列"},
          "description": {"type": "string", "description": "详细说明"},
          "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
          "fields": {"type": "object", "description": "自定义字段（须在配置中已定义）"},
          "priority": {"type": "integer", "description": "优先级，越大越优先"},
          "task_id": {"type": "string", "description": "自定义 id；省略则自动生成"},
          "actor": {"type": "string", "description": "操作者标识，默认 agent"}},
         ["title"]),
    tool("move_task",
         "把任务移到另一列（状态流转）。流转合法性由配置 board.transitions 决定，"
         "非法流转会报错并列出该列允许的目标。before_task_id 可指定插到某任务之前。",
         {"task_id": {"type": "string", "description": "任务 id（必填）"},
          "to_column": {"type": "string", "description": "目标列 id（必填）"},
          "before_task_id": {"type": "string", "description": "插到这个任务之前，省略则追加到列尾"},
          "actor": {"type": "string", "description": "操作者标识，默认 agent"}},
         ["task_id", "to_column"]),
    tool("update_task",
         "更新任务的标题/说明/标签/自定义字段/优先级。只改传入的部分。",
         {"task_id": {"type": "string", "description": "任务 id（必填）"},
          "title": {"type": "string"},
          "description": {"type": "string"},
          "tags": {"type": "array", "items": {"type": "string"}},
          "fields": {"type": "object", "description": "与已有自定义字段合并"},
          "priority": {"type": "integer"},
          "actor": {"type": "string"}},
         ["task_id"]),
    tool("delete_task", "删除一个任务。会同时登记一条 delete 事件（任务数据不可恢复）。",
         {"task_id": {"type": "string", "description": "任务 id（必填）"},
          "actor": {"type": "string"}}, ["task_id"]),
    tool("get_task", "读取单个任务的完整字段。",
         {"task_id": {"type": "string", "description": "任务 id（必填）"}}, ["task_id"]),
    tool("list_tasks",
         "按列或标签筛选任务。想看「进行中都有什么」时用 column_id 过滤。"
         "默认**不返回已归档**的任务（完成已久、已挪出主视图的）；要看历史就传 "
         "include_archived=true。归档不是删除，数据一直都在。",
         {"column_id": {"type": "string", "description": "只看某一列"},
          "tag": {"type": "string", "description": "只看带某标签的任务"},
          "include_archived": {"type": "boolean",
                               "description": "连已归档的一起返回，默认 false"}}),
    tool("list_columns", "列出所有栏目（列）及其顺序、颜色、在制品上限。"),
    tool("update_progress",
         "推进某个任务的进度字段（长耗时任务用）。**进度只增不减**：传入比当前更小的"
         "值会被显式拒绝并告诉你当前值是多少（对齐 MCP 规范 progress 语义）。"
         "current 传绝对值而非增量，因此重复调用是幂等的、重试不会推两遍。"
         "建议长任务周期性上报 —— 否则会被 list_stalled 判定为停滞。",
         {"task_id": {"type": "string", "description": "任务 id（必填）"},
          "current": {"type": "number",
                      "description": "已完成的量（绝对值）。必填"},
          "field": {"type": "string",
                    "description": "要推进的 progress 字段 key；省略则用第一个"},
          "total": {"type": "number",
                    "description": "总量；省略则沿用任务上已有的 total"},
          "weight_bytes": {"type": "number",
                           "description": "本项的权重（字节）。批量搬运场景下用它实现"
                                          "「按字节加权」——大的文件走得慢时进度条不会假装很快"},
          "message": {"type": "string",
                      "description": "当前在干什么，人类可读，如「上传中：第 300 个」"},
          "done": {"type": "boolean", "description": "显式标记该项已完成"},
          "actor": {"type": "string", "description": "操作者标识，默认 agent"}},
         ["task_id", "current"]),
    tool("list_stalled",
         "列出疑似停滞的任务：仍在非终态列、且超过阈值秒数没有任何写入。"
         "**语义是「上报中断」而非「进度值没变」** —— 所以长任务必须周期性调用 "
         "update_progress 上报，否则会被误标。返回里带 idle_sec（静默了多久）。",
         {"threshold_sec": {"type": "number",
                            "description": "静默多少秒算停滞，默认 90"},
          "now": {"type": "string",
                  "description": "参照时间（ISO8601），便于测试与回放；省略用当前时间"}}),
    tool("list_events",
         "读取变更流水（谁在什么时候把什么从哪列移到哪列）。"
         "since_seq 支持增量拉取；给定 task_id 可看单个任务的完整历史。",
         {"limit": {"type": "integer", "description": "最多返回条数，默认 100"},
          "task_id": {"type": "string", "description": "只看该任务的历史"},
          "since_seq": {"type": "integer", "description": "只返回 seq 大于此值的事件"}}),
    tool("archive_done",
         "把「完成已久」的已完成任务归档出主视图。归档 = 打标记，**一条都不删**，"
         "随时可用 restore_archived 拿回来。平时无需手动调：看板快照会自动巡检，"
         "按配置 board.archive_done_after_days（默认 3 天）归档。"
         "这里传 older_than_days 可以临时改用另一个保留期。",
         {"older_than_days": {"type": "number",
                              "description": "完成超过多少天算「已久」，省略则用配置值"}}),
    tool("restore_archived",
         "把归档的任务还原回主视图。给 task_id 还原那一个；不给则**全部还原**。",
         {"task_id": {"type": "string", "description": "要还原的任务 id，省略则全部还原"}}),
    tool("board_reset",
         "清空看板上的全部任务。**破坏性操作**，必须显式传 confirm=true 才会执行。",
         {"confirm": {"type": "boolean", "description": "必须为 true，否则拒绝执行"},
          "actor": {"type": "string"}}),
]


# ---------------------------------------------------------------- protocol

def handle(msg: Any) -> None:
    if not isinstance(msg, dict):
        return
    method = msg.get("method")
    rid = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        pv = (msg.get("params") or {}).get("protocolVersion") or "2024-11-05"
        send_result(rid, {
            "protocolVersion": pv,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return
    if method == "ping":
        send_result(rid, {})
        return
    if method == "tools/list":
        send_result(rid, {"tools": TOOLS})
        return
    if method == "resources/list":
        send_result(rid, {"resources": []})
        return
    if method == "prompts/list":
        send_result(rid, {"prompts": []})
        return
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            send_result(rid, fail("未知工具：%r。可用：%s"
                                  % (name, ", ".join(sorted(HANDLERS)))))
            return
        try:
            send_result(rid, fn(args))
        except (StoreError, StateError, ValueError, ConfigError) as exc:
            # 这些是「可预期的用户错误」：只回一句话，不砸 traceback
            send_result(rid, fail("%s" % exc))
        except Exception as exc:  # noqa: BLE001 — 意外异常要如实回给调用方
            send_result(rid, fail("%s\n%s" % (exc, traceback.format_exc(limit=3))))
        return

    if not is_notification:
        send_error(rid, -32601, "Method not found: %s" % method)


def selftest() -> int:
    """离线自检：只走协议层与 schema，不碰数据库、不写任何文件。"""
    problems: list[str] = []

    names = [t["name"] for t in TOOLS]
    if len(names) != len(set(names)):
        problems.append("工具名有重复")
    # ★ 关键不变量：工具名不得自带 mcp__ 前缀（前缀由宿主添加）
    for n in names:
        if n.startswith("mcp__") or "__" in n:
            problems.append("工具名 %r 不应含 mcp__ 前缀或双下划线" % n)
        if not n.replace("_", "").isalnum():
            problems.append("工具名 %r 含非法字符" % n)
    for t in TOOLS:
        if not t.get("description"):
            problems.append("工具 %r 缺 description" % t["name"])
        sch = t.get("inputSchema") or {}
        if sch.get("type") != "object":
            problems.append("工具 %r 的 inputSchema.type 必须是 object" % t["name"])
        for r in sch.get("required", []):
            if r not in (sch.get("properties") or {}):
                problems.append("工具 %r 的 required 项 %r 未在 properties 中声明"
                                % (t["name"], r))

    # 每个 handler 都要在 TOOLS 里有定义，反之亦然
    miss_h = set(names) - set(HANDLERS)
    miss_t = set(HANDLERS) - set(names)
    if miss_h:
        problems.append("TOOLS 中缺少 handler: %s" % sorted(miss_h))
    if miss_t:
        problems.append("HANDLERS 中缺少工具声明: %s" % sorted(miss_t))

    # 配置也要能独立校验通过（纯内存，不建库）
    try:
        cfg = load_config()
        cfg.validate()
        if not cfg.terminal_ids():
            problems.append("配置缺少终态列")
    except Exception as exc:  # noqa: BLE001
        problems.append("配置校验失败: %s" % exc)

    if problems:
        print("SELFTEST FAILED")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("SELFTEST OK  (%d tools: %s)" % (len(names), ", ".join(names)))
    return 0


def main() -> int:
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    except Exception:  # noqa: BLE001
        pass

    if "--selftest" in sys.argv:
        return selftest()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            continue
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("workbuddy-board fatal: %s\n" % exc)
            sys.stderr.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
