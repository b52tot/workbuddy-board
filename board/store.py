"""存储层 —— 看板数据的**唯一写入方**。

为什么强调「唯一」
------------------
看板前端、MCP 工具、外部脚本都可能想改数据。若各自直接写库，
就会出现「谁改了、为什么改、什么时候改」说不清的情况。
本模块把所有写操作收敛到一处，且**每次写都登记一条 events**，
于是任何时刻都能回答：这个任务是怎么走到现在这一步的。

并发约定
--------
* `PRAGMA journal_mode=WAL` + `busy_timeout`：一写多读，读写互不阻塞。
* 每个方法内部短事务，不做跨调用长事务（避免长时间持锁）。
* 位置(position)用 REAL：插入到两个任务之间时取中值，不必整列重排。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import Config
from .models import (Task, new_id, now_iso, progress_of, progress_fields,
                     read_progress, validate_move, validate_task_payload)


def _parse_iso(s: str) -> datetime:
    """解析 ISO8601，统一带上 UTC 时区。

    ★ 必须统一时区：库里存的时间串有的带 Z、有的不带（旧数据），
      不带时区的那种直接参与减法会抛 TypeError，或者更糟 ——
      被当成"本地时间"从而静默差 8 小时。这类错算在"归档/停滞"这类
      **按时间比较**的逻辑里是致命的，而且不报错。
    """
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# 位置步长：新任务追加到列尾时用 position = max + STEP
POS_STEP = 1000.0

# 停滞判定阈值（秒）的**兜底值**。真正生效的值取自 cfg.board.stall_seconds，
# 这个常量只在拿不到配置时用（比如被单独 import 的老调用方）。
#
# ★ 从 90 秒提到 900 秒：90 秒是"心跳"不是"卡住"。agent 跑一次下载、调一次
#   模型，几分钟不写看板是常态 —— 用 90 秒判会让几乎每个进行中的任务变"卡住"，
#   报警疲劳之后真正的停滞就再也看不见了。
STALL_SECONDS = 900.0


class StoreError(Exception):
    pass


class Store:
    """看板数据访问对象。线程安全（内部一把可重入锁 + 独立连接）。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.RLock()
        # ★ 用 cfg.resolve_path 而不是 os.path.abspath：后者相对 CWD 解析，
        #   而 MCP server 的 CWD 由宿主决定。用 abspath 会让配置里的
        #   ./data/board.db 在宿主换目录启动时写到别处 —— 服务正常、工具正常、
        #   看板却空的。这类失效不报错，只让人以为"数据丢了"。
        self.db_path = cfg.resolve_path(cfg.board.db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                     timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self.sync_columns()

    # ------------------------------------------------------------ 基础设施
    def _init_schema(self) -> None:
        with self._lock:
            # ★ 顺序是硬要求：**先补列，再跑 schema.sql**。
            #
            #   schema.sql 里有一条 `CREATE INDEX ... ON tasks(archived_at)`，
            #   而 executescript 是**整段执行**的：在老库上（tasks 表已存在、
            #   但没有 archived_at 列）建索引会直接抛
            #       sqlite3.OperationalError: no such column: archived_at
            #   于是后面的补列步骤根本轮不到执行 —— 服务起不来。
            #   实测踩到：所有测试都用全新库，所以这条路径一路绿灯，
            #   直到在真实的老库上启动才炸。**老库迁移必须单独测。**
            self._migrate()
            self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._conn.commit()

    def _migrate(self) -> None:
        """给**已存在**的库补新列。

        ★ 为什么必须有这一步：schema.sql 里用的是 `CREATE TABLE IF NOT EXISTS`，
        表已经存在时它**什么都不做** —— 新加的列在老库上永远不会出现。
        用户手上的库都是老库，必须显式迁移。
        """
        try:
            cols = {r["name"] for r in
                    self._conn.execute("PRAGMA table_info(tasks)").fetchall()}
        except Exception:
            return
        if not cols:
            return                      # 表还不存在 = 全新库，交给 schema.sql
        if "archived_at" not in cols:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN archived_at TEXT")

    # ------------------------------------------------------------ 归档
    def _meta_get(self, key: str) -> str | None:
        r = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    # 给旁路模块（如 workbuddy_watch）用的公共版本：
    # 它们不该去碰下划线私有名，否则一重构就断。
    def meta_get(self, key: str) -> str | None:
        return self._meta_get(key)

    def meta_set(self, key: str, value: str) -> None:
        with self._lock:
            self._meta_set(key, value)
            self._conn.commit()

    def _meta_set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def archive_done(self, *, older_than_days: float | None = None,
                     now: str | None = None, force: bool = False) -> dict[str, Any]:
        """把终态列里「已完成」的任务归档出主视图。**只打标记，一条都不删。**

        为什么是归档而不是删除：看板同时是**审计凭据**（events 是只追加的流水）。
        删掉任务而留着它的事件，等于留下一堆指向空气的引用；两者一起删，则等于
        把"当时干了什么"抹掉。归档则在两边都保住完整性的前提下让主视图保持干净。

        保留期语义（`older_than_days`，默认取配置）：
            > 0  只归档「完成超过 N 天」的
            = 0  **归档当时已完成的全部**（默认 —— 重启即清空已完成列）
            < 0  关闭

        `now` 可注入，便于测试构造"三天前完成的任务"而不必真的等三天。
        `force=True` 绕过节流（手动触发时用）。
        """
        with self._lock:
            days = (self.cfg.board.archive_done_after_days
                    if older_than_days is None else older_than_days)

            if days is not None and float(days) < 0:
                return {"ok": True, "skipped": "disabled", "archived": 0}

            ref = _parse_iso(now) if now else datetime.now(timezone.utc)
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)

            # 节流：只在"运行期定期归档"打开时才需要 ——
            # 快照每 2 秒被调一次，不节流就是每 2 秒写一次库。
            if not force:
                if not bool(self.cfg.board.archive_during_run):
                    return {"ok": True, "skipped": "run_archive_off", "archived": 0}
                gap = float(self.cfg.board.archive_check_minutes or 0) * 60
                last = self._meta_get("archived_check_at")
                if last and gap > 0:
                    try:
                        if (ref - _parse_iso(last)).total_seconds() < gap:
                            return {"ok": True, "skipped": "throttled", "archived": 0}
                    except Exception:
                        pass                      # 时间戳坏了就当没检查过

            term = sorted(self.terminal_ids())
            if not term:
                return {"ok": True, "archived": 0}
            qs = ",".join("?" * len(term))

            sql = ("UPDATE tasks SET archived_at=? "
                   "WHERE archived_at IS NULL AND column_id IN (%s)" % qs)
            args: list[Any] = [ref.isoformat()] + term

            cutoff = None
            if days is not None and float(days) > 0:
                # ★ 用 finished_at，缺了才退回 updated_at —— 判断"完成多久了"必须
                #   以**完成时间**为准。只看 updated_at 的话，一条三天前完成、
                #   但刚被改过标签的任务会被误判成"刚完成"，永远归档不掉。
                cutoff = (ref - timedelta(days=float(days))).isoformat()
                sql += " AND COALESCE(finished_at, updated_at, created_at) < ?"
                args.append(cutoff)

            cur = self._conn.execute(sql, args)
            n = cur.rowcount or 0
            self._meta_set("archived_check_at", ref.isoformat())
            self._conn.commit()
            return {"ok": True, "archived": n, "cutoff": cutoff,
                    "older_than_days": None if days is None else float(days)}

    def archive_on_start(self) -> dict[str, Any]:
        """挂件启动时跑一次归档（默认行为）。

        ★ 为什么把触发点放在"启动"而不是"运行中定期"：
          运行中定期归档是**静默**的 —— 用户既不知道它跑没跑，也看不到它做了什么
          （实测因此被质疑"依旧没有归档"）。放到启动时，清理时机由用户自己掌握：
          重启一次 = 清一次，立竿见影。
        """
        if not bool(self.cfg.board.archive_on_startup):
            return {"ok": True, "skipped": "startup_archive_off", "archived": 0}
        return self.archive_done(force=True)

    def archived_count(self) -> int:
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) c FROM tasks WHERE archived_at IS NOT NULL").fetchone()
            return int(r["c"])

    def restore_archived(self, task_id: str | None = None) -> dict[str, Any]:
        """取消归档。不给 task_id 就全部还原。"""
        with self._lock:
            if task_id:
                cur = self._conn.execute(
                    "UPDATE tasks SET archived_at=NULL WHERE id=? AND archived_at IS NOT NULL",
                    (task_id,))
            else:
                cur = self._conn.execute(
                    "UPDATE tasks SET archived_at=NULL WHERE archived_at IS NOT NULL")
            n = cur.rowcount or 0
            self._conn.commit()
            return {"ok": True, "restored": n}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ 列同步
    def sync_columns(self) -> None:
        """把配置里的列定义物化到库里。

        配置是唯一真相源：配置里删掉的列会从 columns 表移除（但其上的任务
        会被拒绝删除，改为报错，避免静默丢数据）。
        """
        with self._lock:
            cur = self._conn.cursor()
            want = {c["id"]: c for c in self.cfg.board.columns}
            have = {r["id"] for r in cur.execute("SELECT id FROM columns")}

            for cid, spec in want.items():
                cur.execute(
                    """INSERT INTO columns(id, title, position, color, wip_limit, is_terminal)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         title=excluded.title, position=excluded.position,
                         color=excluded.color, wip_limit=excluded.wip_limit,
                         is_terminal=excluded.is_terminal""",
                    (cid, spec.get("title", cid),
                     self.cfg.column_ids().index(cid),
                     spec.get("color"),
                     spec.get("wip_limit"),
                     1 if spec.get("is_terminal") else 0))

            for cid in have - set(want):
                n = cur.execute("SELECT COUNT(*) n FROM tasks WHERE column_id=?",
                                (cid,)).fetchone()["n"]
                if n:
                    raise StoreError(
                        "配置已移除列 %r，但该列上还有 %d 个任务。"
                        "请先把任务迁走，或把该列加回配置。" % (cid, n))
                cur.execute("DELETE FROM columns WHERE id=?", (cid,))
            self._conn.commit()

    def list_columns(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM columns ORDER BY position").fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------ 事件登记
    def _log_event(self, cur: sqlite3.Cursor, *, task_id: str | None, kind: str,
                   from_col: str | None = None, to_col: str | None = None,
                   actor: str = "system", detail: Any = None) -> None:
        cur.execute(
            """INSERT INTO events(task_id, kind, from_col, to_col, actor, detail, created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (task_id, kind, from_col, to_col, actor,
             json.dumps(detail, ensure_ascii=False) if detail is not None else None,
             now_iso()))

    def list_events(self, *, limit: int = 100, task_id: str | None = None,
                    since_seq: int = 0) -> list[dict[str, Any]]:
        """事件流。`since_seq` 让调用方可以增量拉取（看板轮询用）。"""
        with self._lock:
            sql = "SELECT * FROM events WHERE seq > ?"
            args: list[Any] = [since_seq]
            if task_id:
                sql += " AND task_id = ?"
                args.append(task_id)
            sql += " ORDER BY seq DESC LIMIT ?"
            args.append(int(limit))
            rows = self._conn.execute(sql, args).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d.get("detail"):
                    try:
                        d["detail"] = json.loads(d["detail"])
                    except json.JSONDecodeError:
                        pass
                out.append(d)
            return out

    # ------------------------------------------------------------ 任务读写
    def create_task(self, *, title: str, column_id: str | None = None,
                    description: str | None = None, tags: Iterable[str] | None = None,
                    fields: dict[str, Any] | None = None, priority: int = 0,
                    task_id: str | None = None, actor: str = "agent") -> Task:
        col = column_id or self.cfg.column_ids()[0]
        # 进度字段允许只给 total（新任务只知道总量，还没开始跑）——
        # current 缺省补 0，这样一建出来就能渲染成 0% 的进度条，
        # 而不是要等第一次上报才「突然出现」一根条。
        fields = self._seed_progress_fields(fields)
        validate_task_payload(self.cfg, title=title, fields_in=fields,
                              column_id=col)

        with self._lock:
            cur = self._conn.cursor()
            tid = task_id or new_id()
            exists = cur.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone()
            if exists:
                raise StoreError("任务 id 已存在: %s" % tid)

            mx = cur.execute("SELECT COALESCE(MAX(position), 0) m FROM tasks "
                             "WHERE column_id=?", (col,)).fetchone()["m"]
            ts = now_iso()
            started = ts if not (self.cfg.column_by_id(col) or {}).get("is_terminal") \
                else None
            cur.execute(
                """INSERT INTO tasks(id,title,description,column_id,position,priority,
                                     tags,fields,created_at,updated_at,started_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, title.strip(), description, col, mx + POS_STEP, int(priority),
                 json.dumps(list(tags or []), ensure_ascii=False),
                 json.dumps(fields or {}, ensure_ascii=False), ts, ts, started))
            self._log_event(cur, task_id=tid, kind="create", to_col=col,
                            actor=actor, detail={"title": title.strip()})
            self._conn.commit()

        return self.get_task(tid)

    def get_task(self, task_id: str) -> Task:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?",
                                     (task_id,)).fetchone()
            if row is None:
                raise StoreError("任务不存在: %s" % task_id)
            return Task.from_row(row)

    def list_tasks(self, *, column_id: str | None = None,
                   tag: str | None = None, limit: int = 2000,
                   include_archived: bool = False) -> list[Task]:
        """列出任务。默认**不含已归档**的（归档 = 完成已久，挪出主视图）。

        归档任务没有被删除，只是默认不出现；要看就把 include_archived 打开。
        """
        with self._lock:
            sql = "SELECT * FROM tasks WHERE 1=1"
            args: list[Any] = []
            if not include_archived:
                sql += " AND archived_at IS NULL"
            if column_id:
                sql += " AND column_id=?"
                args.append(column_id)
            if tag:
                # tags 是 JSON 文本，用 LIKE 做粗筛后再精确过滤，避免全表 Python 过滤
                sql += " AND tags LIKE ?"
                args.append('%"%s"%' % tag)
            sql += " ORDER BY column_id, position LIMIT ?"
            args.append(int(limit))
            rows = self._conn.execute(sql, args).fetchall()
            tasks = [Task.from_row(r) for r in rows]
            if tag:
                tasks = [t for t in tasks if tag in t.tags]
            return tasks

    def _seed_progress_fields(self, fields: dict[str, Any] | None
                              ) -> dict[str, Any] | None:
        """建任务时把 progress 字段的 current 补成 0（若只给了 total）。"""
        if not fields:
            return fields
        out = dict(fields)
        import copy as _copy
        for spec in progress_fields(self.cfg):
            key = spec["key"]
            raw = out.get(key)
            if not isinstance(raw, dict):
                continue
            raw = _copy.deepcopy(raw)
            if raw.get("current") is None and raw.get("total") is not None:
                raw["current"] = 0
            out[key] = raw
        return out

    def _normalize_progress_fields(self, merged: dict[str, Any], cur_task: Task,
                                   *, incoming: dict[str, Any] | None = None
                                   ) -> dict[str, Any]:
        """让进度字段支持「原子推进」：只传 current，其余从旧值继承。

        为什么需要它：调用方最自然的写法是「推进到 300」——只想给一个数。但如果
        这条记录覆写整个字段，total 就丢了：进度算不出百分比，看板显示不出来，
        **而且单调闸门也会因为算不出百分比而静默失效**（两处同时坏，且都不报错）。

        继承规则：total / weight_bytes / message 缺省时沿用旧值；done 不继承
        （它是本次的动作，不是历史状态）。
        """
        from .models import progress_fields as _pf
        for spec in _pf(self.cfg):
            key = spec["key"]
            new_raw = merged.get(key)
            if not isinstance(new_raw, dict):
                continue
            old_raw = (cur_task.fields or {}).get(key)
            old_raw = old_raw if isinstance(old_raw, dict) else {}
            # 只在调用方**确实传了这个键**、且没显式给 total 时才继承；
            # 显式传 total=None 表示「我要清掉它」，尊重调用方意图。
            sent = (incoming or {}).get(key)
            sent_is_dict = isinstance(sent, dict)
            if sent_is_dict and "total" not in sent and "total" in old_raw:
                new_raw = dict(new_raw)
                new_raw["total"] = old_raw["total"]
            if sent_is_dict and "weight_bytes" not in sent and "weight_bytes" in old_raw:
                new_raw = dict(new_raw)
                new_raw["weight_bytes"] = old_raw["weight_bytes"]
            merged[key] = new_raw
        return merged

    def _guard_progress_monotonic(self, cur_task: Task,
                                  incoming: dict[str, Any],
                                  merged: dict[str, Any]) -> dict[str, Any]:
        """★ 进度显示值单调不减 —— 唯一的兜底闸门。

        为什么必须放在 Store（而不是各个调用方）：
        MCP 工具、HTTP 接口、外部脚本都从不同入口写进度。**只在某一处做检查，
        等于没做**。收敛到这里，任何写入路径都绕不过去。

        为什么允许「拒绝」而不是「静默上调」：静默修正会把一次真实的写入失败
        变成「看起来成功了」，这正是本项目最不想要的失败模式（改了却没生效）。
        所以传回过期值时**显式报错**，并告诉调用方当前值是多少。

        注意：唯一例外是**任务被移列**（在途任务被退回/换列）—— 这时进度归零是
        预期行为，由 move_task 走 replace 路径直接覆盖，不经过本闸门。
        """
        for spec in progress_fields(self.cfg):
            key = spec["key"]
            if key not in incoming:
                continue  # 本次没改这个字段
            sent = incoming.get(key)
            if not isinstance(sent, dict):
                continue  # 结构问题交给 validate_task_payload 报错
            # ★ 基准必须取 merged（已做 total 继承）而不是 incoming：
            #   调用方只传 {current: 300} 是**正常**用法，total 由上一步补齐。
            #   若拿入参去算，会算不出百分比 ⇒ 要么误报错，要么（更糟）静默放行，
            #   闸门形同虚设。这个坑踩过一次。
            effective = merged.get(key)
            effective = effective if isinstance(effective, dict) else sent

            def _pct(raw: Any) -> float | None:
                got = read_progress(spec, raw)
                return None if got is None else got["pct"]

            new_pct = _pct(effective)
            old_pct = _pct((cur_task.fields or {}).get(key))

            # ★ 想推进、但连补齐后都算不出百分比 ⇒ 显式报错。
            #   最危险的从来不是报错，而是「看起来更好」的静默跳过：闸门放过去，
            #   进度条悄悄不显示，调用方以为上报成功了。宁可当场拦住。
            #
            #   注意这里**不要求 old_pct 存在**：任务上从来没建过进度基准
            #   （没给 total）时，写入一个 current 同样是算不出百分比的坏写入。
            #   早先加了 `and old_pct is not None` 的前置条件，结果「首次上报就缺
            #   total」这条路径被静默放行 —— 被测试抓出来了。
            if new_pct is None and sent.get("current") is not None:
                raise StoreError(
                    "无法计算 fields['%s'] 的进度：本次传入缺少可用的 total"
                    "（任务上已有的 total 也算不出百分比）。"
                    "请带上 total，例如 {\"current\": %r, \"total\": N}。"
                    % (key, sent.get("current")))

            if old_pct is not None and new_pct is not None and new_pct < old_pct:
                raise StoreError(
                    "进度不允许回退：fields['%s'] 当前 %s%%，本次传入 %s%%。"
                    "进度只增不减（对齐 MCP 规范的 progress 语义）；"
                    "若确实要重置，请先把任务移到别的列再移回来。"
                    % (key, old_pct, new_pct))
            # 写回的是「补齐过 total 的」那一份，不是入参
            merged[key] = effective
        return merged

    def update_task(self, task_id: str, *, title: str | None = None,
                    description: str | None = None,
                    tags: Iterable[str] | None = None,
                    fields: dict[str, Any] | None = None,
                    priority: int | None = None,
                    actor: str = "agent") -> Task:
        cur_task = self.get_task(task_id)
        merged: dict[str, Any] | None = None
        if fields is not None:
            merged = dict(cur_task.fields)
            merged.update(fields)
            # ★ 先按 schema 规整：MCP 的「原子推进」（只传 current）要能继承已有的
            #   total / weight_bytes。不补这一步，{current:300} 会覆写成没有 total
            #   的残值 —— 进度显示不出来，而且单调闸门会因为算不出百分比而失效。
            merged = self._normalize_progress_fields(merged, cur_task, incoming=fields)
            validate_task_payload(self.cfg, title=title or cur_task.title,
                                  fields_in=merged, column_id=cur_task.column_id)
            # ★ 单调闸门的结果必须用回去，否则等于没检查
            merged = self._guard_progress_monotonic(cur_task, fields, merged)

        sets, args, detail = [], [], {}
        if title is not None and title.strip() != cur_task.title:
            sets.append("title=?"); args.append(title.strip())
            detail["title"] = title.strip()
        if description is not None:
            sets.append("description=?"); args.append(description)
            detail["description"] = True
        if tags is not None:
            sets.append("tags=?"); args.append(json.dumps(list(tags), ensure_ascii=False))
            detail["tags"] = list(tags)
        if merged is not None:
            sets.append("fields=?"); args.append(json.dumps(merged, ensure_ascii=False))
            # detail 里只记本次**真正传入**的键，避免把上一次的值也当成这次改的
            detail["fields"] = fields
        if priority is not None:
            sets.append("priority=?"); args.append(int(priority))
            detail["priority"] = int(priority)

        if not sets:
            return cur_task  # 没变化就不写库、不记事件

        sets.append("updated_at=?"); args.append(now_iso())
        args.append(task_id)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE tasks SET %s WHERE id=?" % ", ".join(sets), args)
            self._log_event(cur, task_id=task_id, kind="update", actor=actor,
                            detail=detail)
            self._conn.commit()
            return self.get_task(task_id)

    def move_task(self, task_id: str, to_column: str, *,
                  before_task_id: str | None = None,
                  actor: str = "agent") -> Task:
        """把任务移到目标列；可选插到某个任务之前。"""
        t = self.get_task(task_id)
        with self._lock:
            cur = self._conn.cursor()
            cnt = cur.execute("SELECT COUNT(*) n FROM tasks WHERE column_id=?",
                              (to_column,)).fetchone()["n"]
            # 同列内重排时，自己也算在目标列里，不该被自己的 WIP 挡住
            if to_column == t.column_id:
                cnt = max(0, cnt - 1)
            validate_move(self.cfg, t.column_id, to_column, cnt)

            # 计算新位置
            if before_task_id:
                b = cur.execute("SELECT position FROM tasks WHERE id=?",
                                (before_task_id,)).fetchone()
                if b is None:
                    raise StoreError("before_task_id 不存在: %s" % before_task_id)
                prev = cur.execute(
                    "SELECT position FROM tasks WHERE column_id=? AND position<? "
                    "ORDER BY position DESC LIMIT 1",
                    (to_column, b["position"])).fetchone()
                lo = prev["position"] if prev else b["position"] - POS_STEP
                newpos = (lo + b["position"]) / 2.0
            else:
                mx = cur.execute("SELECT COALESCE(MAX(position),0) m FROM tasks "
                                 "WHERE column_id=? AND id<>?",
                                 (to_column, task_id)).fetchone()["m"]
                newpos = mx + POS_STEP

            ts = now_iso()
            sets = ["column_id=?", "position=?", "updated_at=?"]
            args: list[Any] = [to_column, newpos, ts]

            term = self.cfg.terminal_ids()
            # 时间戳是「进度可视化」的基础：进入终态记 finished_at，
            # 从终态退回则清空 finished_at（否则会看起来已完工）
            if to_column in term and not t.finished_at:
                sets.append("finished_at=?"); args.append(ts)
            elif to_column not in term and t.finished_at:
                sets.append("finished_at=?"); args.append(None)
            if t.started_at is None and to_column not in term:
                sets.append("started_at=?"); args.append(ts)

            args.append(task_id)
            cur.execute("UPDATE tasks SET %s WHERE id=?" % ", ".join(sets), args)
            self._log_event(cur, task_id=task_id, kind="move",
                            from_col=t.column_id, to_col=to_column, actor=actor)
            self._conn.commit()

        return self.get_task(task_id)

    def note_progress(self, task_id: str, *, key: str, value: dict[str, Any],
                      actor: str = "agent") -> None:
        """单独登记一条进度推进事件。

        为什么不复用 update 事件：`update` 只说「改过 fields」，而复盘时真正需要
        回答的是「它推进到了哪个值、什么时候推的」。把推进值记成独立事件，
        才能回答「这 720 个文件是分几次、以什么速率报上来的」。
        """
        with self._lock:
            cur = self._conn.cursor()
            self._log_event(cur, task_id=task_id, kind="advance", actor=actor,
                            detail={"field": key, "value": value})
            self._conn.commit()

    def delete_task(self, task_id: str, *, actor: str = "agent") -> None:
        t = self.get_task(task_id)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            self._log_event(cur, task_id=task_id, kind="delete",
                            from_col=t.column_id, actor=actor,
                            detail={"title": t.title})
            self._conn.commit()

    # ------------------------------------------------------------ 停滞检测
    def _last_event_at(self, task_ids: list[str]) -> dict[str, str]:
        """每个任务最后一次事件的时间。

        ★ 为什么用 events 而不是 tasks.updated_at：
        需要一个**不可伪造且单调**的活动证据。updated_at 是普通列，可被任何
        UPDATE 顺带改写，且「截断-重写-等长」这类改写可能让它看起来没变。
        events 是只追加的，seq 单调递增 —— 这是唯一可信的「它还在动」的证据。

        注意语义边界：本方法看的是「该任务**最近一次被写入**的时间」，因此
        周期性地用 update_progress 上报进度的任务，永远不会被判为停滞。
        **它是「上报中断」检测，不是「值没变」检测。**
        真正「上报了但进度不走」的检测依赖 events 流实时采样（见 list_stalled 注释）。
        """
        if not task_ids:
            return {}
        qs = ",".join("?" * len(task_ids))
        with self._lock:
            rows = self._conn.execute(
                "SELECT task_id, MAX(created_at) AS t FROM events "
                "WHERE task_id IN (%s) GROUP BY task_id" % qs, task_ids).fetchall()
            return {r["task_id"]: r["t"] for r in rows if r["task_id"]}

    def stall_threshold(self) -> float:
        """当前生效的停滞阈值（秒）。配置优先，缺失才退回常量。"""
        try:
            v = float(self.cfg.board.stall_seconds)
            if v > 0:
                return v
        except Exception:
            pass
        return STALL_SECONDS

    def list_stalled(self, *, threshold_sec: float | None = None,
                     now: str | None = None) -> list[dict[str, Any]]:
        """列出疑似停滞的任务：在非终态列，且超过 threshold_sec 没有任何事件。

        为什么把「卡住」单独做成一等公民：Agent 干活时最难受的是看不见 —— 而
        「看不见」里最贵的一种是**静默停滞**。慢任务会长嘴，卡死的任务不会。
        所以停滞必须有独立于「完成率」的呈现位。

        ★ threshold_sec 默认不再是模块常量，而是走配置
          （cfg.board.stall_seconds）。常量 90 秒太短：它把"几分钟没写看板"
          这种常态误判成卡住，报警一多就没人看了。
        """
        if threshold_sec is None:
            threshold_sec = self.stall_threshold()
        term = self.terminal_ids()
        tasks = [t for t in self.list_tasks() if t.column_id not in term]
        last = self._last_event_at([t.id for t in tasks])

        ref = None
        if now:
            try:
                ref = datetime.fromisoformat(now.replace("Z", "+00:00"))
            except ValueError as e:
                raise StoreError("now 必须是 ISO8601 时间串，实际 %r" % now) from e
        if ref is None:
            ref = datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        out: list[dict[str, Any]] = []
        for t in tasks:
            stamp = last.get(t.id) or t.updated_at or t.created_at
            try:
                ts = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            idle = (ref - ts).total_seconds()
            if idle <= threshold_sec:
                continue
            prog = progress_of(self.cfg, t.fields)
            d = t.to_dict()
            d["idle_sec"] = round(idle, 1)
            d["suspected_stall"] = True
            d["progress"] = prog
            out.append(d)
        out.sort(key=lambda x: x["idle_sec"], reverse=True)
        return out

    # ------------------------------------------------------------ 聚合视图
    def snapshot(self, *, since_seq: int = 0) -> dict[str, Any]:
        """给看板用的一次性完整快照。前端只读，不做任何写入。

        ⚠️ 例外：这里会顺带跑一次**归档巡检**（把完成已久的任务挪出主视图）。
           它自带节流（见 archive_done），默认每小时最多真正写一次库 ——
           因为本方法会被高频调用（挂件每 2 秒一次），不节流就等于持续写库。
        """
        try:
            self.archive_done()
        except Exception:
            # 归档失败绝不能影响看板能不能读出来
            log = getattr(self, "_dbg_log", None)
            if callable(log):
                log("archive_done 失败")
        cols = self.list_columns()
        tasks = self.list_tasks()
        by_col: dict[str, list[dict[str, Any]]] = {c["id"]: [] for c in cols}
        for t in tasks:
            by_col.setdefault(t.column_id, []).append(t.to_dict())
        for cid in by_col:
            by_col[cid].sort(key=lambda x: (x["position"], x["created_at"]))

        term = self.terminal_ids()
        counts = {c["id"]: len(by_col.get(c["id"], [])) for c in cols}
        total = len(tasks)
        finished = sum(1 for t in tasks if t.column_id in term)

        # 停滞计数随快照一起给前端，避免前端为它单独再打一次请求。
        # ★ idle_sec 必须在这里挂到任务上：早先前端读的是 t.idle_sec，而 snapshot()
        #   的 tasks_by_column 里根本没有这个键 ⇒ 卡片上永远显示「已 0 分钟无进展」。
        #   这是「元数据全绿但产物是错的」的典型 —— stalled_ids 对了，时长却是假的。
        stalled_rows = self.list_stalled()
        stalled_ids = {t["id"] for t in stalled_rows}
        idle_by_id = {t["id"]: t.get("idle_sec") for t in stalled_rows}
        for cid, lst in by_col.items():
            for d in lst:
                if d["id"] in idle_by_id:
                    d["idle_sec"] = idle_by_id[d["id"]]

        # 带进度字段的看板：在快照里预先把每个任务的进度算好（前端不重复解析），
        # 并按「活动任务的进度均值」给出聚合百分比。**分母写死为活跃任务数**，
        # 不因为某个任务没上报进度就把它从分母里摘掉 —— 分母收缩是最危险的
        # 静默失效（进度条会假装到了 100%）。
        if progress_fields(self.cfg):
            actives = [t for t in tasks if t.column_id not in term]
            for cid, lst in by_col.items():
                for d in lst:
                    d["progress"] = progress_of(self.cfg, d.get("fields") or {})
            pcts = [progress_of(self.cfg, t.fields) for t in actives]
            pcts = [p["pct"] for p in pcts if p]
            agg = round(sum(pcts) / len(actives), 1) if actives else 0.0
        else:
            agg = None

        return {
            "title": self.cfg.board.title,
            "refresh_ms": self.cfg.board.refresh_ms,
            "hide_empty_columns": self.cfg.board.hide_empty_columns,
            "columns": cols,
            "tasks_by_column": by_col,
            "stats": {
                "total": total,
                "finished": finished,
                "active": total - finished,
                "percent": round(finished / total * 100, 1) if total else 0.0,
                "agg_progress": agg,
                "stalled": len(stalled_ids),
                "stall_threshold_sec": self.stall_threshold(),
                "per_column": counts,
                "archived": self.archived_count(),
            },
            "fields_spec": self.cfg.board.fields,
            "stalled_ids": sorted(stalled_ids),
            "server_seq": self._max_seq(),
            "generated_at": now_iso(),
        }

    def _max_seq(self) -> int:
        with self._lock:
            r = self._conn.execute("SELECT COALESCE(MAX(seq),0) m FROM events").fetchone()
            return int(r["m"])

    # ------------------------------------------------------------ 配置转发
    def terminal_ids(self) -> set[str]:
        """终态列 id 集合。转发到 Config，避免调用方还得自己持有 cfg。"""
        return self.cfg.terminal_ids()

    def column_ids(self) -> list[str]:
        return self.cfg.column_ids()

    def column_by_id(self, cid: str) -> dict[str, Any] | None:
        return self.cfg.column_by_id(cid)
