"""OrbCue 事件桥的测试。

测试的边界说明（避免"看起来测过了"）
----------------------------------
单测**不碰真实的 orb.exe** —— 覆盖 `_call_orb` 来断言"该发的命令发了、
不该发的一条没发"。真实通路（真的把事件送进 OrbCue 管道并被接受）
由手工实测那张表覆盖，两边的职责不重叠：

  单测：映射逻辑 / 开关 / 非阻塞 / 丢弃策略
  实测：真实 subprocess → 命名管道 → Dock 状态机

为什么这几条值得测：它们都是**错了不会报错**的那类 ——
开关失效会静默多发事件、非阻塞失效会静默拖慢每次写操作、
终态列多发了 start 会让小球上永远挂着一个"工作中"的幽灵会话。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board.config import Config                                       # noqa: E402
from board.orbcue import (KIND_COMPLETE, KIND_PERMISSION, KIND_RESET,  # noqa: E402
                          KIND_START, KIND_WAITING, KIND_WORKING,
                          OrbCueBridge)
from board.store import Store                                          # noqa: E402


class FakeBridge(OrbCueBridge):
    """把真实调用换掉，只记录"发了什么"。"""

    def __init__(self, **kw):
        self.calls: list[list[str]] = []
        self.ok = True
        super().__init__(**kw)

    def _call_orb(self, args: list[str]) -> bool:
        self.calls.append(list(args))
        return self.ok

    def drain(self, timeout: float = 3.0) -> None:
        """等后台线程把队列吃完。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._q.empty():
                time.sleep(0.05)      # 再确认一次，避免"刚取走还没处理完"
                if self._q.empty():
                    return
            time.sleep(0.02)


class TestDisabled(unittest.TestCase):
    """关着的时候必须完全不动 —— 这是"可选"的底线。"""

    def test_disabled_sends_nothing(self) -> None:
        b = FakeBridge(enabled=False, exe="X:/fake/orb.exe")
        for k in (KIND_START, KIND_WORKING, KIND_COMPLETE, KIND_RESET):
            b.send(k, "t1")
        b.drain()
        self.assertEqual(b.calls, [], "关着却发了事件")
        self.assertEqual(b.sent, 0)
        self.assertGreater(b.dropped, 0, "关着时应该计入 dropped 而不是静默忽略")

    def test_disabled_does_not_probe_filesystem(self) -> None:
        """★ 关着时不该去找 orb.exe —— 不能让"没装 OrbCue"变成每次启动的开销。"""
        b = OrbCueBridge(enabled=False)
        self.assertIsNone(b.exe, "关着时仍探测了 exe 路径")

    def test_send_never_raises(self) -> None:
        """send() 是写路径上的调用，任何情况下都不许抛。"""
        b = OrbCueBridge(enabled=True, exe="X:/definitely/not/here.exe")
        for k in ("start", "bogus-kind", KIND_RESET):
            try:
                b.send(k, "t")
            except Exception as e:            # noqa: BLE001
                self.fail("send 抛异常了: %r" % e)
        b.close(timeout=3)


class TestMapping(unittest.TestCase):
    """命令映射：每一种事件要翻译成哪条 orb 命令。"""

    def setUp(self) -> None:
        self.b = FakeBridge(enabled=True, exe="X:/fake/orb.exe",
                            source="workbuddy")

    def tearDown(self) -> None:
        self.b.close(timeout=3)

    def test_start_maps_to_start(self) -> None:
        self.b.send(KIND_START, "task-1")
        self.b.drain()
        self.assertEqual(self.b.calls, [["X:/fake/orb.exe", "start", "task-1",
                                         "--source", "workbuddy"]])

    def test_working_maps_to_working(self) -> None:
        self.b.send(KIND_WORKING, "task-1")
        self.b.drain()
        self.assertIn("working", self.b.calls[0])

    def test_permission_maps_to_permission(self) -> None:
        self.b.send(KIND_PERMISSION, "task-1")
        self.b.drain()
        self.assertIn("permission", self.b.calls[0])

    def test_complete_maps_to_complete(self) -> None:
        self.b.send(KIND_COMPLETE, "task-1")
        self.b.drain()
        self.assertIn("complete", self.b.calls[0])

    def test_reset_uses_session_id_flag(self) -> None:
        """★ reset 的参数形式与其他不同：不是位置参数，而是 --session-id。

        写错的话 `orb` 会报错退出 ⇒ 被静默计入 dropped ⇒
        小球上会永远留着一行删不掉的任务。所以单独钉一条。
        """
        self.b.send(KIND_RESET, "task-9")
        self.b.drain()
        self.assertEqual(self.b.calls, [["X:/fake/orb.exe", "reset",
                                         "--source", "workbuddy",
                                         "--session-id", "task-9"]])

    def test_unknown_kind_is_dropped_not_sent(self) -> None:
        """未知事件种类不许瞎发 —— 宁可丢，不可发错。"""
        self.b.send("no-such-kind", "t")
        self.b.drain()
        self.assertEqual(self.b.calls, [])
        self.assertGreater(self.b.dropped, 0)

    def test_source_is_configurable(self) -> None:
        b = FakeBridge(enabled=True, exe="X:/fake/orb.exe", source="myboard")
        try:
            b.send(KIND_START, "t")
            b.drain()
            self.assertIn("myboard", b.calls[0])
        finally:
            b.close(timeout=3)


class TestNonBlockingAndFailure(unittest.TestCase):
    """这两条错了不会报错，只会让看板变慢或报错 —— 所以必须钉住。"""

    def test_send_does_not_block_even_if_orb_hangs(self) -> None:
        """★ 反向臂：让 _call_orb 卡住 2 秒，send() 仍必须立刻返回。

        没有这条，把 send 改成同步调用也照样"测试全绿"，
        而实际上是每次建卡都要等 orb 子进程 —— 卡顿极难归因。
        """
        class HangBridge(OrbCueBridge):
            def _call_orb(self, args):        # noqa: ANN001
                time.sleep(2.0)
                return True

        b = HangBridge(enabled=True, exe="X:/fake/orb.exe")
        b.send(KIND_START, "t1")               # 占住后台线程
        time.sleep(0.1)
        t0 = time.time()
        for i in range(50):
            b.send(KIND_WORKING, "t%d" % i)
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.5,
                        "50 次 send 花了 %.2fs —— 说明 send 被后台调用阻塞了" % elapsed)
        b.close(timeout=6)

    def test_failure_enters_cooldown(self) -> None:
        """★ 失败后要冷却：否则 OrbCue 没在跑时，每条事件都白等一次超时。"""
        b = FakeBridge(enabled=True, exe="X:/fake/orb.exe")
        b.ok = False
        try:
            b.send(KIND_START, "t1")
            b.drain()
            first = len(b.calls)
            self.assertGreater(first, 0, "第一次应该真的尝试了")
            # 冷却期内再发，不该产生新的调用
            for _ in range(5):
                b.send(KIND_WORKING, "t2")
            b.drain()
            self.assertEqual(len(b.calls), first,
                             "冷却期内仍在反复调用 —— 冷却机制没生效")
            self.assertTrue(b.stats()["cooling_down"])
        finally:
            b.close(timeout=3)

    def test_queue_full_drops_instead_of_blocking(self) -> None:
        """★ 队列满时必须丢弃新事件，不能阻塞写路径。

        构造方式：让 _call_orb 卡住，然后灌爆队列。
        """
        class HangBridge(OrbCueBridge):
            def _call_orb(self, args):        # noqa: ANN001
                time.sleep(3.0)
                return True

        b = HangBridge(enabled=True, exe="X:/fake/orb.exe")
        b.send(KIND_START, "hold")             # 占住消费者
        time.sleep(0.1)
        t0 = time.time()
        results = [b.send(KIND_WORKING, "t%d" % i) for i in range(400)]
        elapsed = time.time() - t0
        self.assertLess(elapsed, 1.0, "灌队列时被阻塞了 %.2fs" % elapsed)
        self.assertIn(False, results, "队列应该被打满并开始丢弃（而不是无限增长）")
        self.assertGreater(b.dropped, 0)
        b.close(timeout=5)

    def test_worker_thread_is_daemon(self) -> None:
        """后台线程必须是 daemon：看板退出时不能被它挂住。"""
        b = OrbCueBridge(enabled=True, exe="X:/fake/orb.exe")
        try:
            self.assertIsNotNone(b._thread)
            self.assertTrue(b._thread.daemon)
        finally:
            b.close(timeout=3)


class TestStallNotification(unittest.TestCase):
    """停滞 → OrbCue 提醒。

    这是整套接入里**最值钱**的一条（OrbCue 唯二会打扰用户的状态就是
    waiting_input / permission_requested，而看板里唯二需要人介入的正是
    停滞和阻塞），同时也是**最容易做坏**的一条：做成"每次读快照都提醒"
    就会每秒响一次 —— 提醒变噪音，比不提醒更糟。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.cfg = Config()
        self.cfg.board.db_path = os.path.join(self.tmp, "stall.db")
        self.cfg.orbcue.enabled = True
        self.cfg.orbcue.exe = "X:/fake/orb.exe"
        self.cfg.validate()
        self.st = Store(self.cfg)
        # 换成假桥：只记录发了什么，不真跑 subprocess
        self.st.orb = FakeBridge(enabled=True, exe="X:/fake/orb.exe")

    def tearDown(self) -> None:
        self.st.orb.close(timeout=3)
        self.st.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stall(self, tid: str) -> None:
        """把该任务的全部事件时间回拨 —— 构造「上报中断」。"""
        self.st._conn.execute(
            "UPDATE events SET created_at=? WHERE task_id=?",
            ("2020-01-01T00:00:00+00:00", tid))
        self.st._conn.commit()

    def _waiting_count(self) -> int:
        return sum(1 for c in self.st.orb.calls if "waiting" in c)

    def test_stall_notifies_exactly_once(self) -> None:
        """★ 正向 + 去重：连续读快照只提醒一次。

        没有这条断言，改成"每次 snapshot 都发"也照样通过前面的功能性测试，
        而那等于小球每秒响一次。
        """
        t = self.st.create_task(title="会长跑的任务", column_id="doing")
        self.st.orb.calls.clear()          # 忽略建卡那条 start
        self._stall(t.id)

        for _ in range(5):                 # Web 端 5 秒 = 5 次快照
            self.st.snapshot()
        self.st.orb.drain()
        self.assertEqual(self._waiting_count(), 1,
                         "5 次快照发了 %d 条 waiting —— 去重没生效，会变成噪音"
                         % self._waiting_count())

    def test_recovers_then_stalls_again_notifies_again(self) -> None:
        """★ 恢复后再次停滞，必须能再提醒一次。

        语义是「每一次『动着动着停了』提醒一次」，不是「一个任务只提醒一次」。
        只做去重不做摘除的话，任务第二次卡住就再也不会提醒了 —— 那是**漏报**，
        比噪音更危险。
        """
        t = self.st.create_task(title="反复卡住的任务", column_id="doing")
        self.st.orb.calls.clear()

        self._stall(t.id)
        self.st.snapshot()
        self.st.orb.drain()
        self.assertEqual(self._waiting_count(), 1)

        # 恢复上报（事件时间回到现在）⇒ 不再停滞 ⇒ 应从已通知集合摘除
        self.st.note_progress(t.id, key="progress",
                              value={"current": 1, "total": 10})
        self.st.orb.drain()
        self.assertFalse(self.st.snapshot()["stalled_ids"],
                         "上报之后不该还判停滞")

        # 再次卡住 ⇒ 必须再提醒
        self._stall(t.id)
        self.st.snapshot()
        self.st.orb.drain()
        self.assertEqual(self._waiting_count(), 2,
                         "第二次停滞没有重新提醒 —— 漏报")

    def test_completed_task_is_dropped_from_notified(self) -> None:
        """移到终态后要从"已通知"里摘掉，否则任务被拖回非终态再卡住时不会提醒。"""
        t = self.st.create_task(title="先卡后完成", column_id="doing")
        self._stall(t.id)
        self.st.snapshot()
        self.st.orb.drain()
        self.assertIn(t.id, self.st._stall_notified)

        self.st.move_task(t.id, "done")
        self.st.snapshot()
        self.st.orb.drain()
        self.assertNotIn(t.id, self.st._stall_notified,
                         "终态任务仍留在已通知集合里")

    def test_disabled_bridge_never_notifies(self) -> None:
        """关着桥接时，停滞也不该发任何东西（回归到"可选"的底线）。"""
        self.st.orb = FakeBridge(enabled=False, exe="X:/fake/orb.exe")
        t = self.st.create_task(title="关桥接", column_id="doing")
        self._stall(t.id)
        for _ in range(3):
            self.st.snapshot()
        self.st.orb.drain()
        self.assertEqual(self.st.orb.calls, [], "关着桥接却发了事件")


    def test_orphan_task_gets_session_before_waiting(self) -> None:
        """★ 桥接开启之前就存在的任务：必须先补建会话，再发 waiting。

        实测踩到过这个：本地 `sent=3` 而 OrbCue 侧 `tracked=0` ——
        两边都"成功"，中间什么都没发生。原因是 OrbCue 对未知会话的
        waiting 只 accepted 不建记录。所以顺序是硬要求，不是风格问题。
        """
        t = self.st.create_task(title="桥接前就存在的任务", column_id="doing")
        self.st.orb.calls.clear()        # 忽略建卡那条 start
        self._stall(t.id)

        self.st.snapshot()
        self.st.orb.drain()
        kinds = [c[1] for c in self.st.orb.calls]

        self.assertIn("waiting", kinds, "没有发 waiting")
        self.assertIn("working", kinds,
                      "没有先补建会话 —— waiting 会被 OrbCue 当孤儿事件丢掉")
        self.assertLess(kinds.index("working"), kinds.index("waiting"),
                        "补建会话必须排在 waiting 之前")

    def test_session_ensured_only_once(self) -> None:
        """补建会话本身也要去重，否则每次快照都白发一条。"""
        t = self.st.create_task(title="去重", column_id="doing")
        self.st.orb.calls.clear()
        for _ in range(4):
            self.st.snapshot()
        self.st.orb.drain()
        kinds = [c[1] for c in self.st.orb.calls]
        self.assertEqual(kinds.count("working"), 1,
                         "补建会话发了 %d 次 —— 去重没生效" % kinds.count("working"))


if __name__ == "__main__":
    unittest.main()
