"""灌入示例数据，方便第一次跑起来就能看到看板的样子。

用法:
    python examples/demo_seed.py            # 灌入示例任务
    python examples/demo_seed.py --reset    # 先清空再灌

★ 数据不绑定具体列 id：按「第 1 / 2 / 3 / 最后一个」列位置分配，
  这样换成任意自定义栏目（发布流程、审批流…）也能直接跑出效果。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from board.config import load_config  # noqa: E402
from board.store import Store  # noqa: E402

# (标题, 列位置, 标签, 优先级)  位置：0=首列 1=第二列 2=第三列 -1=倒数第二列 -2=终态列
DEMO = [
    ("整理项目 README，补安装与配置说明", 0, [], 0),
    ("给数据层补一批边界测试", 1, ["test"], 1),
    ("把看板前端改成无构建步骤的静态页", 1, ["ui"], 0),
    ("调研同类看板的进度呈现方案", -2, ["research"], 0),
    ("等待下游依赖发布，暂时无法继续", 2, ["external"], 0),
    ("给 MCP 工具补 --selftest", 0, ["tooling"], 2),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清空再看板")
    a = ap.parse_args()

    cfg = load_config()
    store = Store(cfg)
    try:
        cols = cfg.column_ids()
        term = cfg.terminal_ids()
        if a.reset:
            n = 0
            for t in store.list_tasks():
                store.delete_task(t.id, actor="demo")
                n += 1
            print("已清空 %d 个任务" % n)

        def pick(pos: int) -> str:
            """按位置挑一列；-2 表示第一个终态列。"""
            if pos == -2:
                for cid in cols:
                    if cid in term:
                        return cid
                return cols[-1]
            idx = pos if pos >= 0 else len(cols) + pos
            idx = max(0, min(idx, len(cols) - 1))
            return cols[idx]

        for title, pos, tags, prio in DEMO:
            col = pick(pos)
            t = store.create_task(title=title, column_id=col, tags=tags,
                                  priority=prio, actor="demo")
            print("  + [%s] %s" % (t.column_id, t.title))

        snap = store.snapshot()
        print("\n当前看板: %d 个任务，完成率 %s%%"
              % (snap["stats"]["total"], snap["stats"]["percent"]))
        print("启动看板:  python -m server.web_server")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
