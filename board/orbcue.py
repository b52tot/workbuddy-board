"""看板 → OrbCue 事件桥（可选）。

把看板的状态变更翻译成 OrbCue 的生命周期事件，让桌面上的小球能显示
「有几个任务在跑 / 哪个卡住了 / 哪个在等你」。

为什么是「可选 + 异步 + 静默」
------------------------------
1. **可选**：OrbCue 是外部程序。没装它，看板必须照常工作 —— 不能因为
   `orb.exe` 不存在就让建卡失败。
2. **异步**：`orb` 是子进程调用。放在 Store 的写路径上同步跑，会把每次
   建卡 / 移卡 / 上报进度都拖慢几十毫秒。所以这里只把事件丢进队列，
   由一条后台线程消费。
3. **静默**：OrbCue 自己的领域文档写着「**不把声音、窗口和 Agent adapter
   的失败传播回事件发送方**」。我们这边同理 —— 发不出去就当没这回事，
   绝不因为 OrbCue 的问题让看板报错。

事件映射
--------
| 看板动作 | OrbCue 命令 |
|---|---|
| `create_task` | `start <task_id>` |
| `move` → 普通列 | `working <task_id>` |
| `move` → 标了 `orb_state: permission` 的列 | `permission <task_id>` |
| `move` → 终态列 | `complete <task_id>` |
| `note_progress` | `working <task_id>` |
| `delete_task` | `reset --session-id <task_id>` |

**列语义不硬编码在代码里**：哪一列代表「等人处理」，由配置的
`board.columns[].orb_state` 声明。这是本项目的既有原则 ——
业务词汇只出现在配置里。
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

# 事件种类。用常量而不是裸字符串，避免打错字后静默变成"未知事件被忽略"。
KIND_START = "start"
KIND_WORKING = "working"
KIND_PERMISSION = "permission"
KIND_COMPLETE = "complete"
KIND_RESET = "reset"
# ★ 停滞。这一条是整套接入里**最值钱**的：OrbCue 唯二会让用户"被打扰"的
#   状态就是 waiting_input 和 permission_requested，而看板里唯二真正需要
#   人介入的正是「停滞」和「阻塞」——两边的"该吵醒用户"集合是同构的。
KIND_WAITING = "waiting"

# `orb` 子命令与事件种类的对应（reset 参数形式不同，单独处理）
_CLI = {
    KIND_START: "start",
    KIND_WORKING: "working",
    KIND_PERMISSION: "permission",
    KIND_COMPLETE: "complete",
    KIND_WAITING: "waiting",
}

# 队列上限。满了直接丢新事件，**绝不阻塞写路径** ——
# 看板的数据正确性比"OrbCue 少收一条事件"重要得多。
_QUEUE_MAX = 256

# 探测失败后的冷却时长（秒）。没有这个，OrbCue 没装时每次写操作都会白等
# 一次 subprocess 超时 —— 那正是"可选"变成"拖累"的地方。
_PROBE_COOLDOWN = 60.0

# 单次 orb 调用超时。实测正常调用 <100ms；给 3s 是防卡死，不是预期耗时。
_CALL_TIMEOUT = 3.0


def default_exe() -> str | None:
    """按 OrbCue 的默认安装位置找 orb.exe。

    NSIS 安装包默认装到用户目录（per-user，不需要管理员），
    所以这里先看 %LOCALAPPDATA%。PATH 里能找到就用 PATH 里的。
    """
    found = shutil.which("orb")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "OrbCue" / "orb.exe"
        if p.is_file():
            return str(p)
    return None


class OrbCueBridge:
    """把看板事件投递给 OrbCue。线程安全，永不抛异常给调用方。"""

    def __init__(self, enabled: bool = False, exe: str | None = None,
                 source: str = "workbuddy") -> None:
        self.source = source
        self.enabled = bool(enabled)
        # ★ 关着时不探测文件系统：每次 Store 实例化都 which 一遍没必要。
        #   开着的场景才值得为"自动找 orb.exe"付这点开销。
        self.exe = exe or (default_exe() if self.enabled else None)
        self._q: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=_QUEUE_MAX)
        self._thread: threading.Thread | None = None
        self._probe_failed_until = 0.0
        self.sent = 0          # 成功投递计数（供自检/测试读）
        self.dropped = 0       # 丢弃计数（队列满 / orb 不可用）
        if self.enabled and self.exe:
            self._thread = threading.Thread(target=self._worker, daemon=True,
                                            name="orbcue-bridge")
            self._thread.start()

    # ------------------------------------------------------------ 对外接口
    def send(self, kind: str, task_id: str) -> bool:
        """投递一条事件。**非阻塞，永不抛异常。**

        返回是否真的入队（False 表示被丢弃）。调用方不该依赖这个返回值 ——
        它只是给测试用的把手。
        """
        if not (self.enabled and self.exe):
            self.dropped += 1
            return False
        try:
            self._q.put_nowait((kind, task_id))
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def close(self, timeout: float = 2.0) -> None:
        """等队列排空后收工。看板退出时调用，尽量别丢最后几条事件。

        ★ 毒丸也可能投不进去：队列满时 put_nowait 会抛 queue.Full。
        测试抓到过这个 —— 队列被灌满后再关，close 就把异常带出去了。
        对调用方（看板退出路径）来说，宁可少发最后几条事件，
        也不能让收尾抛异常。
        """
        if self._thread is None:
            return
        try:
            self._q.put(("", ""), timeout=timeout)
        except queue.Full:
            pass                      # 队列满 ⇒ 线程还有活干；它是 daemon，不挡退出
        self._thread.join(timeout=timeout)

    # ------------------------------------------------------------ 内部
    def _worker(self) -> None:
        while True:
            try:
                kind, task_id = self._q.get()
            except Exception:
                return
            if kind == "":                       # 毒丸
                return
            try:
                self._emit(kind, task_id)
            except Exception:
                # 静默：OrbCue 的任何问题都不该冒泡到看板。
                self.dropped += 1

    def _emit(self, kind: str, task_id: str) -> None:
        if time.time() < self._probe_failed_until:
            self.dropped += 1
            return
        if kind == KIND_RESET:
            args = [self.exe, "reset", "--source", self.source,
                    "--session-id", task_id]
        else:
            sub = _CLI.get(kind)
            if sub is None:
                self.dropped += 1
                return
            args = [self.exe, sub, task_id, "--source", self.source]
        if self._call_orb(args):
            self.sent += 1
        else:
            # 连不上管道（OrbCue 没在跑）也进冷却，避免每条事件都付一次超时。
            self._probe_failed_until = time.time() + _PROBE_COOLDOWN
            self.dropped += 1

    def _call_orb(self, args: list[str]) -> bool:
        """真正去跑 `orb`。返回值只表示"这条路走通了吗"。

        抽成独立方法是为了让测试能覆盖它 —— 单测不该依赖本机装没装 OrbCue。
        真实通路靠手工实测那次完整验证覆盖（start/working/waiting/complete/reset
        + 反例），两边的职责不重叠。
        """
        try:
            r = subprocess.run(args, capture_output=True, timeout=_CALL_TIMEOUT,
                               # ★ 必须显式给 encoding：中文 Windows 默认 GBK，
                               #   不加会在解码时报 UnicodeDecodeError。
                               text=True, encoding="utf-8", errors="replace")
        except Exception:
            return False
        return r.returncode == 0

    # ------------------------------------------------------------ 自检用
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "exe": self.exe,
            "sent": self.sent,
            "dropped": self.dropped,
            "cooling_down": time.time() < self._probe_failed_until,
        }
