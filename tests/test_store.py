"""数据层测试 —— 用纯标准库 unittest，不依赖 pytest。

覆盖的都是「错了会静默出问题」的地方：
不合法流转、WIP 上限、时间戳维护、事件登记、列同步拒绝丢数据。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board.config import Config, ConfigError, load_config  # noqa: E402
from board.models import StateError  # noqa: E402
from board.store import Store, StoreError  # noqa: E402


def make_cfg(db: str) -> Config:
    cfg = Config()
    cfg.board.db_path = db
    cfg.validate()
    return cfg


class TestStoreBasics(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.store = Store(make_cfg(self.db))

    def tearDown(self) -> None:
        self.store.close()

    def test_create_and_get(self) -> None:
        t = self.store.create_task(title="hello")
        self.assertEqual(t.column_id, "todo")
        self.assertIsNotNone(t.started_at, "非终态任务应有 started_at")
        self.assertIsNone(t.finished_at)
        got = self.store.get_task(t.id)
        self.assertEqual(got.title, "hello")

    def test_create_empty_title_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create_task(title="   ")

    def test_unknown_column_rejected(self) -> None:
        with self.assertRaises(ValueError) as cm:
            self.store.create_task(title="x", column_id="__nope__")
        self.assertIn("__nope__", str(cm.exception))

    def test_undefined_field_rejected(self) -> None:
        """未在配置中定义的字段必须报错 —— 否则拼写错误会被静默接受。"""
        with self.assertRaises(ValueError) as cm:
            self.store.create_task(title="x", fields={"typo": 1})
        self.assertIn("typo", str(cm.exception))

    def test_duplicate_id_rejected(self) -> None:
        self.store.create_task(title="a", task_id="fixed")
        with self.assertRaises(StoreError):
            self.store.create_task(title="b", task_id="fixed")


class TestStateMachine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = Store(make_cfg(os.path.join(self.tmp, "s.db")))

    def tearDown(self) -> None:
        self.store.close()

    def test_illegal_transition_rejected(self) -> None:
        """默认配置里 done 只允许回到 todo，因此 done -> doing 应被拒绝。"""
        t = self.store.create_task(title="x")
        self.store.move_task(t.id, "done")
        with self.assertRaises(StateError) as cm:
            self.store.move_task(t.id, "doing")
        msg = str(cm.exception)
        self.assertIn("done", msg)
        self.assertIn("todo", msg, "报错里应列出该列允许的目标")

    def test_finished_at_set_and_cleared(self) -> None:
        t = self.store.create_task(title="x")
        self.assertIsNone(t.finished_at)
        t = self.store.move_task(t.id, "done")
        self.assertIsNotNone(t.finished_at, "进终态应记 finished_at")
        t = self.store.move_task(t.id, "todo")
        self.assertIsNone(t.finished_at, "从终态退回应清空 finished_at，否则看起来像已完工")

    def test_wip_limit_enforced(self) -> None:
        cfg = make_cfg(os.path.join(self.tmp, "w.db"))
        # 把 doing 的 WIP 压到 1
        for c in cfg.board.columns:
            if c["id"] == "doing":
                c["wip_limit"] = 1
        store = Store(cfg)
        try:
            a = store.create_task(title="a")
            b = store.create_task(title="b")
            store.move_task(a.id, "doing")
            with self.assertRaises(StateError) as cm:
                store.move_task(b.id, "doing")
            self.assertIn("上限", str(cm.exception))

            # 已在列内的任务做同列重排不该被自己的 WIP 挡住
            store.move_task(a.id, "doing")
        finally:
            store.close()

    def test_wip_disabled(self) -> None:
        cfg = make_cfg(os.path.join(self.tmp, "w2.db"))
        cfg.board.enforce_wip = False
        for c in cfg.board.columns:
            if c["id"] == "doing":
                c["wip_limit"] = 1
        store = Store(cfg)
        try:
            for i in range(3):
                t = store.create_task(title="t%d" % i)
                store.move_task(t.id, "doing")  # 不该抛
        finally:
            store.close()

    def test_move_before_task_orders_correctly(self) -> None:
        a = self.store.create_task(title="a")
        b = self.store.create_task(title="b")
        c = self.store.create_task(title="c")
        # 把 c 插到 a 之前
        self.store.move_task(c.id, "todo", before_task_id=a.id)
        order = [t.id for t in self.store.list_tasks(column_id="todo")]
        self.assertLess(order.index(c.id), order.index(a.id))
        self.assertLess(order.index(a.id), order.index(b.id))


class TestEvents(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.store = Store(make_cfg(os.path.join(self.tmp, "e.db")))

    def tearDown(self) -> None:
        self.store.close()

    def test_every_write_leaves_a_trace(self) -> None:
        t = self.store.create_task(title="x", actor="agent")
        self.store.move_task(t.id, "doing", actor="agent")
        self.store.update_task(t.id, description="d", actor="user")
        evs = self.store.list_events(task_id=t.id, limit=50)
        kinds = sorted(e["kind"] for e in evs if e["task_id"] == t.id)
        self.assertEqual(kinds, ["create", "move", "update"])
        mv = [e for e in evs if e["kind"] == "move"][0]
        self.assertEqual(mv["from_col"], "todo")
        self.assertEqual(mv["to_col"], "doing")

    def test_noop_update_does_not_log(self) -> None:
        """没实际变化的调用不该污染事件流。"""
        t = self.store.create_task(title="x")
        before = len(self.store.list_events(limit=1000))
        self.store.update_task(t.id, title="x")
        after = len(self.store.list_events(limit=1000))
        self.assertEqual(before, after)


class TestConfigValidation(unittest.TestCase):
    def test_no_terminal_column_rejected(self) -> None:
        cfg = Config()
        for c in cfg.board.columns:
            c["is_terminal"] = False
        with self.assertRaises(ConfigError) as cm:
            cfg.validate()
        self.assertIn("终态", str(cm.exception))

    def test_transition_pointing_to_unknown_column(self) -> None:
        cfg = Config()
        cfg.board.transitions["todo"] = ["__ghost__"]
        with self.assertRaises(ConfigError) as cm:
            cfg.validate()
        self.assertIn("__ghost__", str(cm.exception))

    def test_duplicate_column_id(self) -> None:
        cfg = Config()
        cfg.board.columns.append(dict(cfg.board.columns[0]))
        with self.assertRaises(ConfigError):
            cfg.validate()

    def test_bad_field_type(self) -> None:
        cfg = Config()
        cfg.board.fields = [{"key": "x", "type": "datetime"}]
        with self.assertRaises(ConfigError):
            cfg.validate()

    def test_unknown_top_level_key_rejected(self) -> None:
        """配了不生效的键必须报错，否则用户以为改了其实没改。"""
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text(json.dumps({"board": {}, "web": {}, "typo_section": {}}),
                     encoding="utf-8")
        with self.assertRaises(ConfigError) as cm:
            load_config(str(p))
        self.assertIn("typo_section", str(cm.exception))

    def test_underscore_keys_are_comments(self) -> None:
        """`_` 开头的键是注释，必须被忽略 —— 否则示例配置启动就崩。"""
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text(json.dumps({
            "_comment": "顶层注释",
            "board": {"_columns_comment": "列定义说明", "title": "注释测试"},
            "web": {"_note": "端口说明"},
        }, ensure_ascii=False), encoding="utf-8")
        cfg = load_config(str(p))
        cfg.validate()
        self.assertEqual(cfg.board.title, "注释测试")

    def test_real_typo_still_rejected(self) -> None:
        """注释放宽后，真拼写错误仍要被抓出来。"""
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text(json.dumps({"board": {"titel": "拼错了"}}), encoding="utf-8")
        with self.assertRaises(ConfigError) as cm:
            load_config(str(p))
        self.assertIn("titel", str(cm.exception))

    def test_env_override(self) -> None:
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text(json.dumps({"board": {"title": "FromEnv"}}), encoding="utf-8")
        os.environ["WBB_CONFIG"] = str(p)
        try:
            self.assertEqual(load_config().board.title, "FromEnv")
        finally:
            os.environ.pop("WBB_CONFIG", None)

    def test_defaults_work_without_file(self) -> None:
        """没有任何配置文件时也要能跑起来（克隆即用）。"""
        cfg = load_config("/nonexistent/path.json")
        cfg.validate()
        self.assertTrue(cfg.terminal_ids())


class TestProgress(unittest.TestCase):
    """进度型任务 —— 每条判据都给正反两臂。

    只有正向（"能推进"）通过不携带信息：一个恒真的实现也能通过。
    承载力在反向臂：**构造反事实，判据必须能拒绝它**。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        cfg = make_cfg(os.path.join(self.tmp, "p.db"))
        cfg.board.fields = [
            {"key": "progress", "type": "progress", "label": "进度"},
            {"key": "owner", "type": "text", "label": "负责人"},
        ]
        cfg.validate()
        self.store = Store(cfg)

    def tearDown(self) -> None:
        self.store.close()

    # ---------------------------------------------------------- 正向臂
    def test_seed_with_total_only_renders_zero(self) -> None:
        """建任务时只给 total，应自动补 current=0 并能算出百分比。"""
        t = self.store.create_task(title="搬 720 个", fields={"progress": {"total": 720}})
        snap = self.store.snapshot()
        got = snap["tasks_by_column"]["todo"][0]["progress"]
        self.assertEqual(got["pct"], 0.0)
        self.assertEqual(got["total"], 720)

    def test_atomic_advance_inherits_total(self) -> None:
        """★ 只传 current 是正常用法，total 必须从已有值继承。

        不继承的话进度算不出百分比 —— 而且单调闸门会因算不出而**静默失效**，
        两处同时坏且都不报错。这个坑踩过一次。
        """
        t = self.store.create_task(title="x", fields={"progress": {"total": 720}})
        t = self.store.update_task(t.id, fields={"progress": {"current": 300}})
        got = self.store.snapshot()["tasks_by_column"]["todo"][0]["progress"]
        self.assertEqual(got["total"], 720, "total 必须被继承")
        self.assertEqual(got["pct"], 41.7)

    def test_advance_is_idempotent(self) -> None:
        """传绝对值而非增量 ⇒ 重复上报同一值不会把进度推两遍。"""
        t = self.store.create_task(title="x", fields={"progress": {"total": 100}})
        for _ in range(3):
            self.store.update_task(t.id, fields={"progress": {"current": 50}})
        got = self.store.snapshot()["tasks_by_column"]["todo"][0]["progress"]
        self.assertEqual(got["pct"], 50.0, "重复上报同值不该累加")

    def test_done_derived_when_reaching_total(self) -> None:
        t = self.store.create_task(title="x", fields={"progress": {"total": 10}})
        self.store.update_task(t.id, fields={"progress": {"current": 10}})
        got = self.store.snapshot()["tasks_by_column"]["todo"][0]["progress"]
        self.assertTrue(got["done"], "到达 total 应标记完成")

    def test_advance_leaves_audit_event(self) -> None:
        """进度推进要能在流水里单独查到推进到的值。"""
        t = self.store.create_task(title="x", fields={"progress": {"total": 10}})
        self.store.update_task(t.id, fields={"progress": {"current": 7}})
        self.store.note_progress(t.id, key="progress",
                                 value={"current": 7, "total": 10})
        evs = [e for e in self.store.list_events(task_id=t.id, limit=50)
               if e["kind"] == "advance"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["detail"]["value"]["current"], 7)

    # ---------------------------------------------------------- 反向臂
    def test_regression_is_rejected_not_silently_clamped(self) -> None:
        """★ 反向臂：进度回退必须**显式报错**。

        静默上调同样是错的 —— 那会把「写入被拒绝」伪装成「写入成功」，
        正是本项目最不想要的失败模式。所以要断言「抛异常」而不是「值没变」。
        """
        t = self.store.create_task(title="x", fields={"progress": {"total": 720}})
        self.store.update_task(t.id, fields={"progress": {"current": 300}})
        with self.assertRaises(StoreError) as cm:
            self.store.update_task(t.id, fields={"progress": {"current": 120}})
        msg = str(cm.exception)
        self.assertIn("回退", msg)
        self.assertIn("41.7", msg, "报错要说清当前值是多少")

        # 且库里的值必须**原封不动**（不是被改成了别的数）
        got = self.store.snapshot()["tasks_by_column"]["todo"][0]["progress"]
        self.assertEqual(got["pct"], 41.7)

    def test_equal_value_is_allowed(self) -> None:
        """等值不算回退 —— 否则重试同一进度点会被误拒。"""
        t = self.store.create_task(title="x", fields={"progress": {"total": 100}})
        self.store.update_task(t.id, fields={"progress": {"current": 50}})
        self.store.update_task(t.id, fields={"progress": {"current": 50}})  # 不该抛

    def test_missing_total_is_rejected_not_silently_skipped(self) -> None:
        """★ 反向臂：算不出百分比时必须报错，不能静默放行。

        静默放行会让进度条悄悄不显示，而调用方以为上报成功了。
        """
        cfg = make_cfg(os.path.join(self.tmp, "p2.db"))
        cfg.board.fields = [{"key": "progress", "type": "progress", "label": "进度"}]
        cfg.validate()
        st = Store(cfg)
        try:
            # 建任务时不带 total，此后再推进就无基准可算
            t = st.create_task(title="x")
            with self.assertRaises(StoreError) as cm:
                st.update_task(t.id, fields={"progress": {"current": 5}})
            self.assertIn("total", str(cm.exception))
        finally:
            st.close()

    def test_non_progress_field_unaffected_by_monotonic_gate(self) -> None:
        """单调闸门只作用于 progress 字段，普通 text 字段可任意改写。"""
        t = self.store.create_task(title="x", fields={"owner": "张三"})
        t = self.store.update_task(t.id, fields={"owner": "李四"})
        self.assertEqual(t.fields["owner"], "李四")

    def test_progress_field_requires_label(self) -> None:
        """progress 字段没 label 会渲染成一根不知道代表什么的条 ⇒ 配置期就拒绝。"""
        cfg = Config()
        cfg.board.fields = [{"key": "p", "type": "progress"}]
        with self.assertRaises(ConfigError) as cm:
            cfg.validate()
        self.assertIn("label", str(cm.exception))

    def test_fractional_units_are_lossless(self) -> None:
        """小数进度（如 3.5 GiB / 7 GiB）不能被取整吃掉。"""
        t = self.store.create_task(title="x",
                                   fields={"progress": {"total": 7.0, "current": 0}})
        t = self.store.update_task(t.id, fields={"progress": {"current": 3.5}})
        got = self.store.snapshot()["tasks_by_column"]["todo"][0]["progress"]
        self.assertEqual(got["current"], 3.5)
        self.assertEqual(got["pct"], 50.0)


class TestStallDetection(unittest.TestCase):
    """停滞检测 —— 语义是「上报中断」，必须与「值没变」区分开。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        cfg = make_cfg(os.path.join(self.tmp, "s2.db"))
        cfg.board.fields = [{"key": "progress", "type": "progress", "label": "进度"}]
        cfg.validate()
        self.cfg = cfg
        self.store = Store(cfg)
        self.db = cfg.board.db_path

    def tearDown(self) -> None:
        self.store.close()

    def _age_events(self, task_id: str, iso: str) -> None:
        """把某任务的事件时间改早，用于构造「静默了很久」。"""
        c = sqlite3.connect(self.db)
        c.execute("UPDATE events SET created_at=? WHERE task_id=?", (iso, task_id))
        c.commit()
        c.close()

    def test_no_stall_when_recently_active(self) -> None:
        """正向臂：刚写过的任务不能被误判停滞。"""
        t = self.store.create_task(title="活跃")
        self.assertEqual([x["id"] for x in self.store.list_stalled()], [])

    def test_stall_detected_when_silent(self) -> None:
        t = self.store.create_task(title="卡住")
        t = self.store.update_task(t.id, fields={"progress": {"total": 100, "current": 1}})
        self._age_events(t.id, "2020-01-01T00:00:00+00:00")
        st = self.store.list_stalled()
        self.assertEqual([x["id"] for x in st], [t.id])
        self.assertGreater(st[0]["idle_sec"], 90)
        self.assertTrue(st[0]["suspected_stall"])
        self.assertEqual(self.store.snapshot()["stats"]["stalled"], 1)

    def test_terminal_task_is_never_stalled(self) -> None:
        """★ 反向臂：已完成的任务停在那儿是正常的，不该被报成停滞 ——
        否则看板永远挂着一堆假告警，真告警就被淹没了。"""
        t = self.store.create_task(title="已完成")
        self.store.move_task(t.id, "done")
        self._age_events(t.id, "2020-01-01T00:00:00+00:00")
        self.assertEqual([x["id"] for x in self.store.list_stalled()], [])

    def test_periodic_reporting_prevents_false_stall(self) -> None:
        """★ 反向臂：周期性上报进度的长任务不该被判停滞。

        这是本判据最关键的一臂 —— 长任务本来就要跑几小时，靠「值变大」来
        证明活着是错的（值不变也可能是正常传输中），靠「有上报」才对。
        """
        t = self.store.create_task(title="长任务",
                                   fields={"progress": {"total": 1000, "current": 0}})
        for i in range(1, 4):
            self.store.update_task(t.id, fields={"progress": {"current": i * 10}})
        self._age_events(t.id, "2020-01-01T00:00:00+00:00")
        # 模拟「刚才又上报了一次」
        self.store.note_progress(t.id, key="progress",
                                 value={"current": 30, "total": 1000})
        self.assertNotIn(t.id, [x["id"] for x in self.store.list_stalled()])

    def test_threshold_zero_flags_everything_active(self) -> None:
        """阈值边界：0 秒阈值下所有活跃任务都应被列出（判据确实在计算，不是恒假）。"""
        a = self.store.create_task(title="a")
        b = self.store.create_task(title="b")
        got = {x["id"] for x in self.store.list_stalled(threshold_sec=0)}
        self.assertEqual(got, {a.id, b.id})

    def test_snapshot_cards_carry_idle_sec(self) -> None:
        """★ 正向臂：快照里的停滞卡片必须自带 idle_sec，且与 list_stalled 一致。

        这个坑踩过一次：stalled_ids 给对了，但 tasks_by_column 里的任务没有
        idle_sec 键 —— 前端 `t.idle_sec || 0` 拿到 undefined 就显示成
        「已 0 分钟无进展」。元数据（stalled_ids）全绿，产物（时长文案）是假的。
        判据落在**内容层**：卡片上的秒数必须等于真实静默秒数。
        """
        t = self.store.create_task(title="卡住")
        self._age_events(t.id, "2020-01-01T00:00:00+00:00")
        snap = self.store.snapshot()

        cards = [x for lst in snap["tasks_by_column"].values() for x in lst]
        card = next(x for x in cards if x["id"] == t.id)
        # 先证明它确实被标了停滞，否则下面的断言会在错误的场景下通过
        self.assertIn(t.id, snap["stalled_ids"])
        self.assertIn("idle_sec", card)
        self.assertIsNotNone(card["idle_sec"])
        self.assertGreater(card["idle_sec"], 90)

        ref = next(x for x in self.store.list_stalled() if x["id"] == t.id)
        self.assertAlmostEqual(card["idle_sec"], ref["idle_sec"], delta=5.0)

    def test_snapshot_non_stalled_cards_have_no_idle_sec(self) -> None:
        """★ 反向臂：没停滞的卡片不该带 idle_sec —— 否则前端可能误渲染出告警。"""
        t = self.store.create_task(title="活跃")
        snap = self.store.snapshot()
        cards = [x for lst in snap["tasks_by_column"].values() for x in lst]
        card = next(x for x in cards if x["id"] == t.id)
        self.assertNotIn(t.id, snap["stalled_ids"])
        self.assertIsNone(card.get("idle_sec"))


class TestColumnSync(unittest.TestCase):
    def test_removing_occupied_column_is_rejected(self) -> None:
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "c.db")
        cfg = make_cfg(db)
        store = Store(cfg)
        try:
            store.create_task(title="x", column_id="blocked")
            cfg.board.columns = [c for c in cfg.board.columns if c["id"] != "blocked"]
            cfg.board.transitions.pop("blocked", None)
            with self.assertRaises(StoreError) as cm:
                store.sync_columns()
            self.assertIn("blocked", str(cm.exception))
        finally:
            store.close()


class TestArchiveDone(unittest.TestCase):
    """已完成任务的自动归档。

    这一步关系到用户数据"看不见了"，所以**两臂都要验**：
      正向 —— 满足保留期的任务必须被归档出主视图；
      反向 —— 刚完成的任务**不能**被误归档，且归档后必须能原样拿回来。
    只验正向的话，"把所有已完成任务一股脑藏起来"也能通过。

    保留期语义（改了要同步这里）：> 0 天 / = 0 全部 / < 0 关闭。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "a.db")
        self.cfg = make_cfg(self.db)
        self.cfg.board.archive_done_after_days = 3.0     # 显式给保留期
        self.store = Store(self.cfg)

    def tearDown(self) -> None:
        self.store.close()

    def _finish(self, title: str, *, days_ago: float) -> str:
        """建一个已完成任务，并把它的完成时间改成 N 天前。"""
        t = self.store.create_task(title=title)
        self.store.move_task(t.id, "done")
        stamp = (datetime.now(timezone.utc)
                 - timedelta(days=days_ago)).isoformat()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE tasks SET finished_at=?, updated_at=? WHERE id=?",
                      (stamp, stamp, t.id))
        return t.id

    def test_old_done_is_archived_and_recent_is_not(self) -> None:
        old = self._finish("五天前完成的", days_ago=5)
        fresh = self._finish("刚完成的", days_ago=0.02)      # 约半小时前

        r = self.store.archive_done(force=True)             # 保留期 3 天
        self.assertEqual(r["archived"], 1, "只应归档 1 个（五天的那个）")

        ids = [t.id for t in self.store.list_tasks()]
        self.assertNotIn(old, ids, "完成 5 天的任务应已归档出主视图")
        self.assertIn(fresh, ids, "★ 刚完成的任务不能被误归档")

        # 归档 ≠ 删除：数据必须还在
        all_ids = [t.id for t in self.store.list_tasks(include_archived=True)]
        self.assertIn(old, all_ids, "归档的任务数据必须仍然存在")

        snap = self.store.snapshot()
        self.assertEqual(snap["stats"]["archived"], 1)
        self.assertNotIn(old, [x["id"] for x in snap["tasks_by_column"]["done"]])
        self.assertIn(fresh, [x["id"] for x in snap["tasks_by_column"]["done"]])

    def test_restore_brings_it_back(self) -> None:
        old = self._finish("要还原的", days_ago=5)
        self.store.archive_done(force=True)
        self.assertNotIn(old, [t.id for t in self.store.list_tasks()])

        r = self.store.restore_archived(old)
        self.assertEqual(r["restored"], 1)
        self.assertIn(old, [t.id for t in self.store.list_tasks()],
                      "还原后必须重新出现在主视图")
        self.assertEqual(self.store.archived_count(), 0)

    def test_zero_days_archives_everything(self) -> None:
        """★ 保留期 = 0 = **归档当时已完成的全部**（默认值，重启即清空已完成列）。"""
        a = self._finish("很久以前完成的", days_ago=999)
        b = self._finish("刚完成的", days_ago=0.01)
        cfg2 = make_cfg(self.db)
        cfg2.board.archive_done_after_days = 0
        s2 = Store(cfg2)
        try:
            r = s2.archive_done(force=True)
            self.assertEqual(r["archived"], 2, "0 天应把已完成的全部归档")
            self.assertEqual(s2.archived_count(), 2)
        finally:
            s2.close()

    def test_negative_days_disables_archive(self) -> None:
        """反向臂：负数 = 关闭归档，一个都不许动。"""
        self._finish("很久以前完成的", days_ago=999)
        cfg2 = make_cfg(self.db)
        cfg2.board.archive_done_after_days = -1
        s2 = Store(cfg2)
        try:
            r = s2.archive_done(force=True)
            self.assertEqual(r.get("skipped"), "disabled")
            self.assertEqual(s2.archived_count(), 0)
        finally:
            s2.close()

    def test_run_archive_off_by_default(self) -> None:
        """★ 运行期归档**默认关闭**：不带 force 时 snapshot 里的巡检必须直接跳过。

        （原先是每 60 分钟静默跑一次，用户既看不见也不知道跑没跑 ——
          实测因此被质疑"依旧没有归档"。改成只在启动时做一次。）
        """
        self._finish("五天了", days_ago=5)
        r = self.store.archive_done()          # 不带 force
        self.assertEqual(r.get("skipped"), "run_archive_off")
        self.assertEqual(r.get("archived") or 0, 0, "运行期默认不该归档任何东西")
        # 快照也不该偷偷归档
        self.store.snapshot()
        self.assertEqual(self.store.archived_count(), 0)

    def test_throttled_when_run_archive_enabled(self) -> None:
        """把运行期归档打开后，才轮到节流生效。"""
        self._finish("五天了", days_ago=5)
        cfg2 = make_cfg(self.db)
        cfg2.board.archive_done_after_days = 3.0
        cfg2.board.archive_during_run = True
        s2 = Store(cfg2)
        try:
            first = s2.archive_done()                   # 首次：真的跑
            self.assertEqual(first.get("archived"), 1)
            self._finish("又一个五天", days_ago=6)
            second = s2.archive_done()                  # 立刻再来：应被节流
            self.assertEqual(second.get("skipped"), "throttled")
            self.assertEqual(second.get("archived") or 0, 0)
        finally:
            s2.close()

    def test_archive_on_start_is_the_default_trigger(self) -> None:
        """★ 启动归档才是默认触发点：重启一次就清一次（用户可掌握、可观察）。"""
        self._finish("重启前就完成的", days_ago=0.01)
        # 默认配置：archive_on_startup=True, archive_done_after_days=0
        cfg2 = make_cfg(self.db)
        s2 = Store(cfg2)
        try:
            before = s2.archived_count()
            r = s2.archive_on_start()
            self.assertGreaterEqual(r.get("archived") or 0, 1,
                                    "启动归档应把已完成的清出去")
            self.assertGreater(s2.archived_count(), before)
        finally:
            s2.close()

    def test_archive_on_start_can_be_disabled(self) -> None:
        cfg2 = make_cfg(self.db)
        cfg2.board.archive_on_startup = False
        s2 = Store(cfg2)
        try:
            r = s2.archive_on_start()
            self.assertEqual(r.get("skipped"), "startup_archive_off")
            self.assertEqual(s2.archived_count(), 0)
        finally:
            s2.close()


class TestStallThresholdFromConfig(unittest.TestCase):
    """停滞阈值必须来自配置，而不是写死的常量。

    ★ 反向臂很重要：如果阈值被静默忽略（永远用模块常量 900），
      把配置调成 1 秒应该"什么都停滞"——不测这条就发现不了"配了不生效"。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "s.db")
        self.cfg = make_cfg(self.db)

    def test_config_drives_threshold(self) -> None:
        self.cfg.board.stall_seconds = 1.0
        store = Store(self.cfg)
        try:
            t = store.create_task(title="刚建的")
            # 1 秒阈值：用「未来 10 秒」当参照点 ⇒ 必判停滞
            future = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
            got = store.list_stalled(now=future)
            self.assertIn(t.id, [x["id"] for x in got],
                          "阈值配成 1 秒却没判停滞 ⇒ 配置没生效")
            self.assertEqual(store.stall_threshold(), 1.0)
            self.assertEqual(store.snapshot()["stats"]["stall_threshold_sec"], 1.0)
        finally:
            store.close()

    def test_default_threshold_is_not_90s(self) -> None:
        """90 秒是"心跳"不是"卡住"，默认值必须已经提高。"""
        cfg = make_cfg(os.path.join(tempfile.mkdtemp(), "d.db"))
        self.assertGreaterEqual(float(cfg.board.stall_seconds), 300.0)


class TestMigrationOnExistingDb(unittest.TestCase):
    """老库迁移：**已存在的**库要能被新代码正常打开。

    ★ 这条路径差点漏掉，而且是真炸过的：schema.sql 里有
      `CREATE INDEX ... ON tasks(archived_at)`，而 executescript 是整段执行的
      —— 老库上还没有 archived_at 列时，建索引会先抛
          sqlite3.OperationalError: no such column: archived_at
      补列的代码根本轮不到跑，服务直接起不来。
      下面的用例全部走"全新库"，所以一路绿灯，直到装在真实老库上才炸。
      这个测试专门造一个**老结构**的库来堵这个洞。
    """

    def _make_old_db(self, path: str) -> None:
        """用 schema.sql 造库，但把 archived_at 相关部分摘掉 = 老结构。"""
        sql = (Path(__file__).resolve().parent.parent / "board" / "schema.sql"
               ).read_text(encoding="utf-8")
        lines = [ln for ln in sql.splitlines()
                 if "archived_at" not in ln and "idx_tasks_archived" not in ln]
        with sqlite3.connect(path) as c:
            c.executescript("\n".join(lines))
            c.execute("INSERT INTO columns(id,title,position,color,is_terminal) "
                      "VALUES('todo','待办',1000,'gray',0)")
            c.execute("INSERT INTO tasks(id,title,column_id,position,priority,"
                      "created_at,updated_at) "
                      "VALUES('old1','老库里的任务','todo',1000,0,"
                      "'2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')")

    def test_open_old_db_and_upgrade(self) -> None:
        db = os.path.join(tempfile.mkdtemp(), "old.db")
        self._make_old_db(db)

        # 造出来的库必须**确实**没有 archived_at，否则这个测试就是摆设
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
        self.assertNotIn("archived_at", cols, "老结构造错了，测试不成立")

        store = Store(make_cfg(db))          # ← 修复前这里会抛 OperationalError
        try:
            ids = [t.id for t in store.list_tasks()]
            self.assertIn("old1", ids, "老库里的数据必须还能读出来")
            # 迁移要真的把列补上，不能靠"反正没查它"
            with sqlite3.connect(db) as c:
                cols2 = {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
            self.assertIn("archived_at", cols2, "迁移没把列补上")
            self.assertEqual(store.archived_count(), 0)
        finally:
            store.close()

    def test_old_db_can_archive_after_upgrade(self) -> None:
        """迁移完之后，归档功能要能正常用（列补上了、索引也在）。"""
        db = os.path.join(tempfile.mkdtemp(), "old2.db")
        self._make_old_db(db)
        store = Store(make_cfg(db))
        try:
            t = store.create_task(title="新的")
            store.move_task(t.id, "done")
            stamp = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
            with sqlite3.connect(db) as c:
                c.execute("UPDATE tasks SET finished_at=?, updated_at=? WHERE id=?",
                          (stamp, stamp, t.id))
            r = store.archive_done(force=True)
            self.assertGreaterEqual(r["archived"], 1)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
