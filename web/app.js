/* WorkBuddy Board — 前端逻辑
 *
 * 关键约定
 * --------
 * 1. **前端只读**：所有渲染都基于 /api/board 的快照。默认不提供写操作，
 *    写操作走 MCP 工具，保证事件流完整可溯。若后端开了 allow_write，
 *    这里也仍走 /api/task，由后端同一套 Store 落库。
 * 2. **列定义来自后端**：不硬编码「待办/进行中/已完成」。后端给了什么列
 *    就渲染什么列，颜色/顺序/在制品上限全部照搬 —— 这是通用性的关键。
 * 3. **单调显示**：进度百分比只增不减，避免并发刷新时数字回跳。
 */

const $ = (id) => document.getElementById(id);
const API_BOARD = "/api/board";
const API_EVENTS = "/api/events";

let refreshMs = 1000;
let lastPercent = -1;
let lastAggProgress = -1;
let lastBoard = null;
let failStreak = 0;
let lastBoardHtml = null;   // 上次渲染出的 HTML，用于「内容没变就不重建 DOM」

/* ---------------------------------------------------------------- 工具 */

const COL_ORDER = ["gray", "blue", "amber", "green", "red", "purple",
                   "teal", "pink", "coral"];

function colorVar(name) {
  const key = COL_ORDER.includes(name) ? name : "gray";
  return `var(--col-${key})`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* 数值格式化：进度可能是 3.5 GiB 这种小数，也可能是 3000 这种整数。
   整数不带小数点，小数最多留两位 —— 避免「720.0 / 720.0」这种读起来别扭的输出。 */
function fmtNum(n) {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, "");
}

function relTime(iso) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return Math.round(s) + " 秒前";
  if (s < 3600) return Math.round(s / 60) + " 分钟前";
  if (s < 86400) return Math.round(s / 3600) + " 小时前";
  return Math.round(s / 86400) + " 天前";
}

/** ISO 串 → 本地时间 HH:MM:SS。
 *
 * ★ 不要再写 `iso.slice(11, 19)` —— 服务端给的是 UTC 的 ISO 串，
 *   直接切片等于把 UTC 当本地时间显示，在东八区会**整整差 8 小时**。
 *   这类错误还特别隐蔽：格式完全正常、不报错，只有跟墙上的钟一比才发现。
 *   （挂件那边踩过同一个坑，两处都已收敛到这个函数。）
 */
function hhmmss(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

function fmtDuration(fromIso, toIso) {
  if (!fromIso) return "—";
  const a = Date.parse(fromIso);
  const b = toIso ? Date.parse(toIso) : Date.now();
  if (Number.isNaN(a) || Number.isNaN(b)) return "—";
  let s = Math.max(0, (b - a) / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}天${h}小时`;
  if (h) return `${h}小时${m}分`;
  return `${m}分`;
}

/* 静默时长：停滞卡片上显示「多久没动了」，比「几秒前」更直白。
   用 floor 不用 round：90 秒该说「1 分钟」而不是「2 分钟」；不足 1 分钟说秒，
   否则一个刚过阈值(90s)的卡片会显示成「2 分钟」，看着像已经卡了很久。 */
function fmtIdle(sec) {
  if (typeof sec !== "number" || !isFinite(sec)) return "—";
  if (sec < 60) return `${Math.floor(sec)} 秒`;
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时`;
  return `${Math.floor(sec / 86400)} 天`;
}

/* ---------------------------------------------------------------- 渲染 */

function renderHeader(snap) {
  $("title").textContent = snap.title || "WorkBuddy Board";
  const st = snap.stats || {};
  $("sTotal").textContent = st.total ?? 0;
  $("sActive").textContent = st.active ?? 0;
  $("sDone").textContent = st.finished ?? 0;
  $("sPct").textContent = (st.percent ?? 0) + "%";

  // 停滞计数：有才显示，没有就藏起来（避免常驻一个恒为 0 的数字）
  const stEl = $("sStalled");
  const n = st.stalled ?? 0;
  stEl.textContent = n;
  stEl.parentElement.style.display = n ? "" : "none";

  // ★ 进度条单调不减：并发刷新时数值不能回跳。
  //   这是 MCP 规范对 progress 的要求（"should increase every time"），
  //   在展示层同样成立 —— 刷新竞态不该让用户看到数字倒退。
  const pct = Math.max(lastPercent, st.percent ?? 0);
  lastPercent = pct;
  $("bar").style.width = Math.min(100, pct) + "%";
}

/* 卡片上的单任务进度条。只有配置里声明了 progress 字段才会出现。 */
function progressHtml(p) {
  if (!p || typeof p.pct !== "number") return "";
  const cls = p.done ? "pbar done" : "pbar";
  const msg = p.message ? `<span class="pmsg" title="${esc(p.message)}">${esc(p.message)}</span>` : "";
  return `<div class="prog">
    <div class="${cls}"><i style="width:${Math.min(100, Math.max(0, p.pct))}%"></i></div>
    <div class="pmeta"><span class="ppct">${p.pct}%</span>
      <span class="pnum">${esc(fmtNum(p.current))}/${esc(fmtNum(p.total))}</span>${msg}</div>
  </div>`;
}

function cardHtml(t, stalled) {
  const tags = (t.tags || []).map((x) => `<span class="tag">${esc(x)}</span>`).join("");
  const prio = t.priority ? `<span class="prio">P${t.priority}</span>` : "";
  const desc = t.description ? `<div class="desc">${esc(t.description)}</div>` : "";
  const elapsed = t.started_at
    ? `<span class="elapsed">${fmtDuration(t.started_at, t.finished_at)}</span>`
    : "";
  // 停滞标记必须显眼：静默停滞是最贵的一种「看不见」——慢任务会长嘴，卡死的不会。
  // idle_sec 由后端 snapshot() 挂在任务上；缺失时明确显示「—」而不是假造一个 0。
  const stall = stalled
    ? `<div class="stall">⚠ 疑似停滞 · 已 ${esc(fmtIdle(t.idle_sec))} 无进展</div>`
    : "";
  return `<div class="card${stalled ? " is-stall" : ""}" draggable="true"
      data-id="${esc(t.id)}" data-col="${esc(t.column_id)}">
    <div class="title">${esc(t.title)}</div>
    ${desc}
    ${progressHtml(t.progress)}
    ${stall}
    <div class="meta">${prio}${tags}${elapsed}</div>
  </div>`;
}

function renderBoard(snap) {
  const cols = snap.columns || [];
  const byCol = snap.tasks_by_column || {};
  const hideEmpty = !!snap.hide_empty_columns;
  const stalledMap = {};
  (snap.stalled_ids || []).forEach((id) => { stalledMap[id] = true; });

  const html = cols.filter((c) => !(hideEmpty && !(byCol[c.id] || []).length))
    .map((c) => {
      const list = byCol[c.id] || [];
      const over = c.wip_limit != null && !c.is_terminal && list.length > c.wip_limit;
      const nStall = list.filter((t) => stalledMap[t.id]).length;
      const body = list.length
        ? list.map((t) => cardHtml(t, stalledMap[t.id])).join("")
        : `<div class="empty">空</div>`;
      return `<section class="column" data-col="${esc(c.id)}">
        <div class="col-head">
          <span class="col-bar" style="background:${colorVar(c.color)}"></span>
          <span class="col-title">${esc(c.title)}</span>
          <span class="col-count">${list.length}</span>
          ${nStall ? `<span class="col-stall">${nStall} 停滞</span>` : ""}
          ${over ? `<span class="col-wip">超 WIP ${c.wip_limit}</span>` : ""}
        </div>
        <div class="col-body">${body}</div>
      </section>`;
    }).join("");

  // ★ 内容没变就别碰 DOM。
  //   这里原先无条件 `innerHTML =`，而刷新间隔是 1 秒 ⇒ 每秒重建全部卡片。
  //   后果不只是浪费：人手点击的 mousedown 落在旧元素上，刷新把元素换掉后
  //   mouseup 落在新元素上，**浏览器不会派发 click**（click 要求两者同一元素）
  //   ⇒ 表现为「卡片点了没反应」。自动化测试用原子点击（down/up 几乎同时）
  //   撞不上这个窗口，所以一直测不出来 —— 典型的「元数据全绿但产物是错的」。
  if (html === lastBoardHtml) return;
  lastBoardHtml = html;

  $("board").innerHTML = html;
}

/* ------------------------------------------------- 交互：全部走事件委托 */
/* ★ 为什么不在每次重建后逐个绑定：
   卡片 DOM 会被整体替换。逐个绑定的监听器随旧元素一起被丢弃，且新元素要
   重绑 —— 这中间存在窗口期。更要命的是 click 事件本身要求 mousedown 与
   mouseup 落在**同一个元素**，元素被换掉时浏览器根本不派发 click，
   监听器绑在哪都收不到。
   解法：监听器只绑一次，挂在 document 上；按下时**记下卡片 id**，
   松开时用记下的数据判定 —— 不再依赖「那个元素还在不在」。 */

const DRAG_SLOP = 5;   // 位移超过这么多像素就算拖拽，不当作点击
let press = null;

function cardOf(node) {
  return node && node.closest ? node.closest(".card") : null;
}
function colOf(node) {
  return node && node.closest ? node.closest(".column") : null;
}

document.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;                 // 只认左键
  const card = cardOf(e.target);
  press = card
    ? { id: card.dataset.id, x: e.clientX, y: e.clientY, moved: false }
    : null;
});

document.addEventListener("mousemove", (e) => {
  if (!press) return;
  if (Math.abs(e.clientX - press.x) > DRAG_SLOP ||
      Math.abs(e.clientY - press.y) > DRAG_SLOP) {
    press.moved = true;
  }
});

document.addEventListener("mouseup", (e) => {
  const p = press;
  press = null;
  if (!p) return;
  if (p.moved) return;                        // 拖拽结束，不打开详情
  if (!cardOf(e.target)) return;              // 松手时已不在卡片上
  if (e.target.closest(".modal")) return;     // 弹窗内的点击不重开详情
  openDetail(p.id);
});

/* 拖拽落地：同样用委托，避免每次重建都要重绑 */
document.addEventListener("dragstart", (e) => {
  const card = cardOf(e.target);
  if (!card) return;
  card.classList.add("dragging");
  e.dataTransfer.setData("text/plain", card.dataset.id);
  e.dataTransfer.effectAllowed = "move";
});
document.addEventListener("dragend", (e) => {
  const card = cardOf(e.target);
  if (card) card.classList.remove("dragging");
});
document.addEventListener("dragover", (e) => {
  const col = colOf(e.target);
  if (!col) return;
  e.preventDefault();
  col.classList.add("drag-over");
});
document.addEventListener("dragleave", (e) => {
  const col = colOf(e.target);
  if (col && !col.contains(e.relatedTarget)) col.classList.remove("drag-over");
});
document.addEventListener("drop", async (e) => {
  const col = colOf(e.target);
  if (!col) return;
  e.preventDefault();
  col.classList.remove("drag-over");
  const id = e.dataTransfer.getData("text/plain");
  const to = col.dataset.col;
  if (!id || !to) return;
  if (!lastBoard || !lastBoard.allow_write) {
    toast("看板为只读模式：状态流转请通过 MCP 工具 move_task");
    return;
  }
  await postTask({ action: "move", task_id: id, to_column: to, actor: "user" });
});

/* ---------------------------------------------------------------- 详情 */

async function openDetail(id) {
  const snap = lastBoard;
  if (!snap) return;
  let task = null;
  for (const list of Object.values(snap.tasks_by_column || {})) {
    const hit = list.find((t) => t.id === id);
    if (hit) { task = hit; break; }
  }
  if (!task) return;

  const col = (snap.columns || []).find((c) => c.id === task.column_id) || {};
  // progress 字段不再重复打印裸 JSON —— 上面已经画成进度条了
  const fields = Object.entries(task.fields || {})
    .filter(([k]) => !isProgressKey(snap, k))
    .map(([k, v]) => `<div class="k">${esc(labelOf(snap, k))}</div><div class="v">${esc(JSON.stringify(v))}</div>`)
    .join("");
  const stalled = (snap.stalled_ids || []).includes(task.id)
    ? `<div class="kv"><div class="k">状态</div><div class="v stall-text">⚠ 疑似停滞（超过阈值没有任何上报）</div></div>`
    : "";

  $("mTitle").textContent = task.title;
  $("mBody").innerHTML = `
    <div class="kv">
      <div class="k">ID</div><div class="v">${esc(task.id)}</div>
      <div class="k">所在列</div><div class="v">${esc(col.title || task.column_id)}</div>
      <div class="k">优先级</div><div class="v">${task.priority || 0}</div>
      <div class="k">标签</div><div class="v">${(task.tags || []).map(esc).join(", ") || "—"}</div>
      <div class="k">说明</div><div class="v">${esc(task.description || "—")}</div>
      ${task.progress ? `<div class="k">进度</div><div class="v">${progressHtml(task.progress)}</div>` : ""}
      ${stalled}
      <div class="k">创建于</div><div class="v">${esc(task.created_at)}（${relTime(task.created_at)}）</div>
      <div class="k">开始于</div><div class="v">${task.started_at ? esc(task.started_at) : "—"}</div>
      <div class="k">耗时</div><div class="v">${task.started_at ? fmtDuration(task.started_at, task.finished_at) : "—"}</div>
      <div class="k">更新于</div><div class="v">${relTime(task.updated_at)}</div>
      ${fields}
    </div>
    <div class="events"><div class="h">变更流水</div><div id="evList">加载中…</div></div>`;
  $("mask").classList.add("on");

  try {
    const r = await fetch(`${API_EVENTS}?task_id=${encodeURIComponent(id)}&limit=50`);
    const d = await r.json();
    const evs = (d.events || []).slice().reverse();
    $("evList").innerHTML = evs.length ? evs.map((e) => {
      const move = e.kind === "move" ? `${esc(e.from_col || "—")} → ${esc(e.to_col || "—")}` : "";
      // advance 事件要显示推进到的值 —— 复盘时真正想看的就是这个
      let adv = "";
      if (e.kind === "advance" && e.detail && e.detail.value) {
        const v = e.detail.value;
        adv = `→ ${esc(fmtNum(v.current))}/${esc(fmtNum(v.total))}`;
      }
      return `<div class="ev"><span class="tm">${esc(hhmmss(e.created_at))}</span>
        <span>${esc(e.kind)} ${move} ${adv} <span style="color:var(--fg-3)">by ${esc(e.actor || "?")}</span></span></div>`;
    }).join("") : '<div class="empty">暂无记录</div>';
  } catch (e) {
    $("evList").textContent = "读取失败: " + e;
  }
}

/* 哪些 key 是 progress 字段 —— 详情页据此避免重复展示裸 JSON */
function isProgressKey(snap, key) {
  return ((snap && snap.fields_spec) || [])
    .some((f) => f.key === key && f.type === "progress");
}

function labelOf(snap, key) {
  const spec = ((snap && snap.fields_spec) || []).find((f) => f.key === key);
  return (spec && spec.label) || key;
}

/* ---------------------------------------------------------------- 请求 */

async function postTask(payload) {
  try {
    const r = await fetch("/api/task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) toast(d.error || ("HTTP " + r.status));
    else await tick();
  } catch (e) {
    toast("请求失败: " + e);
  }
}

let toastTimer = null;
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.display = "none"; }, 3200);
}

/* ---------------------------------------------------------------- 主循环 */

async function tick() {
  try {
    const r = await fetch(API_BOARD + "?t=" + Date.now());
    const snap = await r.json();
    failStreak = 0;
    $("hb").className = "sub";
    lastBoard = snap;
    const everWritten = snap.allow_write;
    lastBoard.allow_write = !!everWritten;
    renderHeader(snap);
    renderBoard(snap);
    const st = snap.stats || {};
    // 顶部说明文字顺带报出聚合进度与停滞数，不用再去数柱状条
    const extra = [];
    if (st.agg_progress != null) extra.push("任务进度 " + st.agg_progress + "%");
    if (st.stalled) extra.push("停滞 " + st.stalled);
    $("updated").textContent = "更新于 " + hhmmss(snap.generated_at)
      + (extra.length ? "（" + extra.join(" · ") + "）" : "");
    if (snap.refresh_ms && snap.refresh_ms !== refreshMs) {
      refreshMs = snap.refresh_ms;
    }
  } catch (e) {
    failStreak++;
    $("hb").className = "sub stale";
    $("updated").textContent = "服务未响应（连续 " + failStreak + " 次）";
  }
}

/* ---------------------------------------------------------------- 启动 */

$("mask").addEventListener("click", (e) => {
  if (e.target.id === "mask") $("mask").classList.remove("on");
});
$("mClose").addEventListener("click", () => $("mask").classList.remove("on"));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("mask").classList.remove("on");
});

$("btnTheme").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("wbb-theme", next); } catch (_) {}
});

(function initTheme() {
  let t = null;
  try { t = localStorage.getItem("wbb-theme"); } catch (_) {}
  if (!t) t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
})();

tick();
setInterval(tick, refreshMs);
