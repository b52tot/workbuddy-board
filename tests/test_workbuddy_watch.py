"""WorkBuddy 会话镜像的测试。

为什么这个模块必须有测试：它是**旁路自动写库** —— 一旦判错，
要么把看板灌满垃圾任务，要么把还在跑的任务提前标成完成。
两种失效都发生在后台、都不会报错，只能靠测试挡住。

反向臂（比正向更重要）：
  · 同一个请求**不能重复建任务**（重启/反复巡检都不行）
  · agent 还在跑（有未配对的 function_call）时**不能**把任务标成完成
  · 首次见到文件时**不能**把整个会话的历史请求一次性灌进来
  · 目录不存在 / 坏 JSON / 没权限 —— 只返回失败，**不许抛**
"""

import json
import os
import shutil
import tempfile
import unittest

from board import workbuddy_watch as ww
from board.store import Store

from tests.test_store import make_cfg


def rec(**kw):
    return json.dumps(kw, ensure_ascii=False)


def user_msg(text):
    return rec(type="message", role="user",
               content=[{"type": "input_text", "text": text}])


def asst_msg(text="好的"):
    return rec(type="message", role="assistant",
               content=[{"type": "text", "text": text}])


def fcall(cid):
    return rec(type="function_call", name="Bash", callId=cid, arguments={})


def fresult(cid):
    return rec(type="function_call_result", name="Bash", callId=cid, status="ok",
               output="done")


class WatchBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "w.db")
        self.cfg = make_cfg(self.db)
        self.cfg.board.watch_workbuddy = True
        self.cfg.board.watch_idle_close_sec = 120.0
        self.proj = os.path.join(self.tmp, "projects", "ws-a")
        os.makedirs(self.proj)
        self.tp = os.path.join(self.proj, "s1.jsonl")
        self.cfg.board.watch_projects_dir = os.path.join(self.tmp, "projects")
        self.store = Store(self.cfg)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, lines, mtime=None):
        with open(self.tp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        if mtime is not None:
            os.utime(self.tp, (mtime, mtime))

    def poll_at(self, offset=0.0, **kw):
        """以"文件 mtime + offset"为当前时间巡检。

        ★ 别再用 now=1000.0 这种假时间：文件 mtime 是真实的 1.7e9 量级，
          两边不是一个年代，"安静了多久"会算成负数，判据看着像坏了。
        """
        try:
            base = os.path.getmtime(self.tp)
        except OSError:
            base = 1_700_000_000.0
        return ww.poll(self.store, self.cfg, now=base + offset, **kw)

    def titles(self):
        return [t.title for t in self.store.list_tasks()]

    def doing(self):
        return [t for t in self.store.list_tasks() if t.column_id == "doing"]


class TestBasic(WatchBase):
    def test_new_request_becomes_doing_task(self):
        self.write([user_msg("<user_query>帮我看一下磁盘占用</user_query>"),
                    asst_msg()])
        r = self.poll_at()
        self.assertTrue(r["ok"], r)
        self.assertIn("帮我看一下磁盘占用", r["created"] or "")
        d = self.doing()
        self.assertEqual(len(d), 1, "应恰好建出一条进行中任务")
        self.assertIn(ww.WATCH_TAG, d[0].tags, "镜像任务必须带标记，便于和手工上报区分")

    def test_finished_turn_is_closed_after_idle(self):
        self.write([user_msg("<user_query>跑个测试</user_query>"), asst_msg()])
        # 文件已经安静很久 + 没有未完成调用 ⇒ 应判定这一轮结束
        self.poll_at(9999.0)
        self.assertEqual(self.doing(), [], "安静够久后不该还挂在进行中")
        self.assertEqual(len([t for t in self.store.list_tasks()
                              if t.column_id == "done"]), 1)

    def test_second_request_closes_the_first(self):
        self.write([user_msg("<user_query>第一件事</user_query>"), asst_msg()])
        self.poll_at()
        self.assertEqual(len(self.doing()), 1)

        with open(self.tp, "a", encoding="utf-8") as f:
            f.write(user_msg("<user_query>第二件事</user_query>") + "\n")
        self.poll_at(1.0)
        self.assertEqual(len(self.doing()), 1, "新一轮开始时旧的应已收尾")
        self.assertIn("第二件事", self.doing()[0].title)
        self.assertEqual(len([t for t in self.store.list_tasks()
                              if t.column_id == "done"]), 1)


class TestReverseArms(WatchBase):
    """反向臂：这些是"判错了更坏"的情况。"""

    def test_no_duplicate_on_repeated_poll(self):
        """反复巡检不能重复建任务（挂件每 5 秒轮一次，幂等是硬要求）。"""
        self.write([user_msg("<user_query>只该建一次</user_query>"), asst_msg()])
        for i in range(5):
            self.poll_at(i)
        allt = self.store.list_tasks()
        self.assertEqual(len(allt), 1, "轮询 5 次却建了 %d 条 —— 游标失效" % len(allt))

    def test_busy_turn_not_closed_even_when_file_quiet(self):
        """★ 有未配对的 function_call ⇒ agent 还在干活，即使文件安静也不能收尾。

        这正是"跑长任务时文件也会安静"的情况 —— 判错会把还在跑的任务
        提前标成完成，用户看到的就是"明明还在跑，看板说做完了"。
        """
        self.write([user_msg("<user_query>跑一个很长的任务</user_query>"),
                    fcall("call-1")])          # 只有调用，没有回执
        r = self.poll_at(9999.0)               # 文件很安静，但还在忙
        self.assertTrue(r["busy"], "应识别出「还在忙」")
        self.assertEqual(len(self.doing()), 1, "还在跑的任务不能被收尾")

    def test_closed_after_call_returns(self):
        """回执到了 + 安静够久 ⇒ 才能收尾。"""
        self.write([user_msg("<user_query>短任务</user_query>"),
                    fcall("call-1"), fresult("call-1")])
        self.poll_at(9999.0)
        self.assertEqual(self.doing(), [], "调用已回执且安静够久 ⇒ 应已收尾")

    def test_first_sight_does_not_backfill_history(self):
        """★ 首次见到文件时只认最后一条请求 —— 否则一开开关就灌一屏历史。"""
        self.write([user_msg("<user_query>很久以前的请求 A</user_query>"),
                    asst_msg(),
                    user_msg("<user_query>很久以前的请求 B</user_query>"),
                    asst_msg(),
                    user_msg("<user_query>最近这条</user_query>"),
                    asst_msg()])
        self.poll_at()
        allt = self.store.list_tasks()
        self.assertEqual(len(allt), 1, "首次巡检只能建 1 条（最后那条）")
        self.assertIn("最近这条", allt[0].title)

    def test_two_sessions_do_not_duplicate(self):
        """★★ 回归：两个会话交替被写时，**不能**把同一条请求反复建任务。

        踩过的真实事故：用户同时开着两个会话，两个 jsonl 文件交替更新。
        旧的"全局单游标"实现里，每次"最新文件"一变就被当成"首次见到"
        ⇒ 游标归零 ⇒ 把那条请求**重新建一遍**。
        现象就是同一标题在「进行中」和「已完成」里来回刷：
            A 建 → B 建 → A 又建 → B 又建 …
        修法 = 游标按文件各存各的。这条判据专门盯它。
        """
        a = self.tp
        self.write([user_msg("<user_query>会话A的请求</user_query>"), asst_msg()])
        base = os.path.getmtime(a)
        self.poll_at(1.0)

        b = os.path.join(self.proj, "s2.jsonl")
        with open(b, "w", encoding="utf-8") as f:
            f.write(user_msg("<user_query>会话B的请求</user_query>") + "\n")
        os.utime(b, (base + 10, base + 10))
        self.poll_at(11.0)

        # 来回切 6 次（每次把被切到的文件刷成最新）
        for i in range(6):
            which = b if i % 2 else a
            os.utime(which, (base + 20 + i, base + 20 + i))
            self.poll_at(21.0 + i)

        titles = [t.title for t in self.store.list_tasks()]
        self.assertEqual(len(titles), len(set(titles)),
                         "同一请求被重复建了：%s" % titles)
        self.assertEqual(len(titles), 2,
                         "两个会话各应只有一条，实际：%s" % titles)

    def test_each_session_closes_on_its_own_idle(self):
        """两个会话各管各的收尾：A 的文件安静下来就收 A 的，不影响 B。"""
        a = self.tp
        self.write([user_msg("<user_query>会话A的请求</user_query>"), asst_msg()])
        base = os.path.getmtime(a)
        self.poll_at(1.0)

        b = os.path.join(self.proj, "s2.jsonl")
        with open(b, "w", encoding="utf-8") as f:
            f.write(user_msg("<user_query>会话B的请求</user_query>") + "\n")
        os.utime(b, (base + 10, base + 10))
        self.poll_at(11.0)
        self.assertEqual(len(self.doing()), 2, "两个会话各自都该挂着一条")

        # 只让 A 的文件变旧（B 仍然很新）
        os.utime(a, (base - 99999, base - 99999))
        os.utime(b, (base + 100, base + 100))
        self.poll_at(1000.0)
        left = self.doing()
        self.assertEqual(len(left), 1, "只该收掉 A 的")
        self.assertIn("会话B", left[0].title)

    def test_off_switch_does_nothing(self):
        """开关关掉 ⇒ 一条都不许写。"""
        self.cfg.board.watch_workbuddy = False
        self.write([user_msg("<user_query>不该出现</user_query>"), asst_msg()])
        r = self.poll_at()
        self.assertEqual(r.get("note"), "off")
        self.assertEqual(self.store.list_tasks(), [])


class TestRobustness(WatchBase):
    """旁路模块的铁律：不许抛异常。"""

    def test_missing_dir_is_ok(self):
        self.cfg.board.watch_projects_dir = os.path.join(self.tmp, "nope")
        r = self.poll_at()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r.get("note"), "no-transcript")

    def test_broken_json_lines_are_skipped(self):
        self.write(["{ 这不是合法 json",
                    user_msg("<user_query>坏行之后依然要能识别</user_query>"),
                    "]]]乱码",
                    asst_msg()])
        r = self.poll_at()
        self.assertTrue(r["ok"], r)
        self.assertIn("坏行之后依然要能识别", r["created"] or "")

    def test_message_without_user_query_is_ignored(self):
        """工具回执/系统消息这类没有 <user_query> 的 user 消息不能建任务。"""
        self.write([user_msg("这是工具的中间产物，不是用户请求"), asst_msg()])
        self.poll_at()
        self.assertEqual(self.store.list_tasks(), [])

    def test_empty_query_is_ignored(self):
        self.write([user_msg("<user_query>   </user_query>"), asst_msg()])
        self.poll_at()
        self.assertEqual(self.store.list_tasks(), [])

    def test_corrupt_state_does_not_crash(self):
        self.store.meta_set(ww.K_STATE, "{{{ 不是 json")
        self.write([user_msg("<user_query>状态坏了也要能继续</user_query>"), asst_msg()])
        r = self.poll_at()
        self.assertTrue(r["ok"], r)


class TestTextCleaning(unittest.TestCase):
    def test_strips_image_refs_and_markup(self):
        raw = ("@image#1:Clipboard_Screenshot.png 收起功能异常\n"
               "```\ncode block\n```\n"
               "**加粗** #标题")
        t = ww.make_title(raw)
        self.assertNotIn("image", t.lower())
        self.assertNotIn("```", t)
        self.assertNotIn("**", t)
        self.assertIn("收起功能异常", t)

    def test_title_is_truncated(self):
        t = ww.make_title("啊" * 200)
        self.assertLessEqual(len(t), ww.MAX_TITLE)


if __name__ == "__main__":
    unittest.main()
