"""用**真实渲染器**渲染一份富演示数据，把 DOM 抓下来给主题预览用。

★ 为什么不直接在预览页里手写卡片 HTML：
  那等于另画一张"看起来差不多"的图 —— 进度条宽度、优先级色块、标签间距
  全都是我自己估的，改了样式也判断不准。这里走的是**真的 renderWidget**，
  数据契约也和生产完全一致（BOARD/tasks_by_column/stats 那一套）。

用法：python themedump.py   → 产出 demo_dom.json
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import fulltest  # noqa: E402  复用它的 STUB（桩桥接）与浏览器探测

OUT_DIR = os.path.join(os.path.expanduser("~"), "WorkBuddy",
                       "2026-10-05-12-34-05", ".workbuddy", "tmp")
OUT = os.path.join(OUT_DIR, "demo_dom.json")

# 演示数据取自用户另一条对话里的看板截图（8B 选型 / RAG-Rerank / Milvus 迁移 / 批量跑批）。
# 用真数据形状，是为了让预览里的**进度条、优先级、标签、耗时**都是真渲染出来的。
DEMO_DRIVER = r"""
<script>
(function () {
  var H = 3600, D = 86400;
  function ago(sec) { return new Date(Date.now() - sec * 1000).toISOString(); }

  var board = {
    title: '各类任务一览',
    columns: [
      { id: 'todo',    title: '待办',   color: 'gray' },
      { id: 'doing',   title: '进行中', color: 'blue', wip_limit: 5 },
      { id: 'blocked', title: '阻塞',   color: 'red' },
      { id: 'done',    title: '已完成', color: 'green', is_terminal: true }
    ],
    stalled_ids: ['d3', 'd4'],
    tasks_by_column: {
      todo: [
        { id: 't1', column_id: 'todo', priority: 6,
          title: '调研型：8B 嵌入模型选型（原生 4096 维，不截断）',
          description: '候选若干，评测维度：召回率 / 显存占用 / 推理延迟 / 中文长尾表现。要产出对比表 + 推荐结论，不能只列参数。',
          tags: ['@小枢', 'embedding', '选型'],
          created_at: ago(1 * D + 3 * H), started_at: null, finished_at: null,
          fields: {} }
      ],
      doing: [
        { id: 'd1', column_id: 'doing', priority: 10,
          title: '代码开发：RAG-Rerank 服务推理性能优化',
          description: '批大小与并发度联合调参，目标把 P95 压到 300ms 以内。',
          tags: ['rag', '性能'],
          created_at: ago(1 * D + 7 * H), started_at: ago(1 * D + 7 * H),
          finished_at: null,
          fields: { progress: { current: 3, total: 7, message: '3/7 项优化已落地' } } },
        { id: 'd2', column_id: 'doing', priority: 8,
          title: '数据迁移：kb.db → Milvus（13389 chunks）',
          description: '建集合、灌数据、抽样比对向量一致性。',
          tags: ['milvus', '迁移'],
          created_at: ago(1 * D + 8 * H), started_at: ago(1 * D + 8 * H),
          finished_at: null,
          fields: { progress: { current: 8210, total: 13389,
                                message: '校验通过 8210/13389' } } },
        { id: 'd3', column_id: 'doing', priority: 7,
          title: '批量跑批：全量切片向量重算',
          description: '141 万切片，按 NPU4 批处理重算并落新库。',
          tags: ['批处理'],
          created_at: ago(3 * H), started_at: ago(3 * H), finished_at: null,
          fields: { progress: { current: 41000, total: 1410000,
                                message: '已跑 41000/1410000' } },
          idle_sec: 4200 },
        { id: 'd4', column_id: 'doing', priority: 4,
          title: '文档：超测集 49 题 · 8 类场景的判定口径写清楚',
          description: '哪类题算召回失败、哪类算重排失效，要有可复现的判据。',
          tags: ['文档'],
          created_at: ago(5 * H), started_at: ago(5 * H), finished_at: null,
          fields: {}, idle_sec: 5400 }
      ],
      blocked: [
        { id: 'b1', column_id: 'blocked', priority: 5,
          title: '等 910B 服务器排期：跑 8B 原生 4096 维的全量压测',
          description: '需要独占 8 卡，等窗口。',
          tags: ['资源', '910B'],
          created_at: ago(2 * D), started_at: ago(2 * D), finished_at: null,
          fields: {} }
      ],
      done: [
        { id: 'n1', column_id: 'done', priority: 0,
          title: '挂件：修复折叠箭头无效 / 设置面板卡死',
          description: '根因分别是 CSS 缺 .hidden 规则、pywebview 跨线程写 WinForms。',
          tags: ['挂件', 'bugfix'],
          created_at: ago(2 * D), started_at: ago(2 * D),
          finished_at: ago(1 * D + 20 * H),
          fields: {} }
      ]
    },
    stats: { total: 7, done: 1, stalled: 2, archived: 5, percent: 14 }
  };

  // ★ 必须在**抓取前一刻**再注入一次：页面自己启动时会调一次 get_board()
  //   并把真数据渲染出来，会把早先注入的演示数据覆盖掉。
  //   （第一版就踩了这个：抓到的还是真看板"WorkBuddy Board"。）
  setTimeout(function () {
    window.WB_RELOAD({ board: board, events: {} });
    window.CUR_STATE = 'expanded';
    window.draw();

    setTimeout(function () {
      var a = document.getElementById('app');
      var f = document.getElementById('foot');
      var payload = JSON.stringify({
        app: a ? a.innerHTML : '',
        foot: f ? f.outerHTML : ''
      });
      var pre = document.createElement('pre');
      pre.id = 'demo-dump';
      pre.textContent = btoa(unescape(encodeURIComponent(payload)));
      document.body.appendChild(pre);
    }, 300);
  }, 1800);
})();
</script>
"""


def build_page() -> str:
    with open(os.path.join(HERE, "host.inline.html"), encoding="utf-8") as f:
        html = f.read()
    if "</body>" not in html:
        raise SystemExit("host.inline.html 里没有 </body>")
    return html.replace("</body>", fulltest.STUB + DEMO_DRIVER + "\n</body>", 1)


def main() -> int:
    browser = fulltest.find_browser()
    if not browser:
        print("找不到 Edge/Chrome")
        return 2
    html = build_page()
    fd, path = tempfile.mkstemp(prefix="wbdemo-", suffix=".html")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    try:
        out = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             "--window-size=460,1600", "--virtual-time-budget=20000",
             "--dump-dom", "file:///" + path.replace("\\", "/")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120).stdout
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    m = re.search(r'<pre id="demo-dump">([^<]*)</pre>', out)
    if not m:
        print("没抓到渲染结果 —— 页面可能没跑起来")
        print(out[-1500:])
        return 2
    payload = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("已抓到演示 DOM：%s（app %d 字符）" % (OUT, len(payload["app"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
