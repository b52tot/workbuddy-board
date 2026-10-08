/* ============================================================
   任务挂件 · 渲染逻辑
   ------------------------------------------------------------
   数据来源：window.WB_DATA（由 refresh-data.py 生成）
   数据契约：/api/board 的返回结构，见 README.md

   三个渲染入口：
     renderWidget(el, state)   渲染一个挂件到容器 el
       state: 'expanded' | 'detail' | 'mini' | 'unreachable' | 'empty'
     state 之外的派生量（停滞/耗时/进度）全部从数据算，不写死。

   改这个文件时请保持：所有显示出来的数字都能在 WB_DATA 里找到出处。
   ============================================================ */
(function () {
  'use strict';

  var D = {}, BOARD = {}, EVENTS = {};
  var COLS = [], BYCOL = {}, STALLED = {}, TERM = {}, ALL = [];

  /**
   * 装载（或重载）数据。
   *
   * ★ 为什么要有这个函数、而不是在加载期把数据读成常量：
   *   宿主是长驻的，会周期性拉到新数据。若数据在 IIFE 里被固化成常量，
   *   轮询到新数据后页面上还是旧的 —— 表现成"挂件不刷新"，而且不会报错。
   *   抽成函数后，宿主只需 `WB_RELOAD(newData)` 就能换一整套数据。
   */
  function loadData(data) {
    D = data || window.WB_DATA || {};
    BOARD = D.board || {};
    EVENTS = D.events || {};

    COLS = BOARD.columns || [];
    BYCOL = BOARD.tasks_by_column || {};
    STALLED = {};
    (BOARD.stalled_ids || []).forEach(function (id) { STALLED[id] = true; });
    TERM = {};
    COLS.forEach(function (c) { if (c.is_terminal) TERM[c.id] = true; });
    ALL = [];
    COLS.forEach(function (c) {
      (BYCOL[c.id] || []).forEach(function (t) { ALL.push(t); });
    });
  }

  // 初次装载（原型页靠这行；宿主会在拿到数据后再调一次 WB_RELOAD）
  loadData(window.WB_DATA);

  function taskById(id) {
    for (var i = 0; i < ALL.length; i++) if (ALL[i].id === id) return ALL[i];
    return null;
  }

  /* ---------------------------------------------------------- 工具 */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /** 秒 → 「2小时18分」。超过一天显示「1天3小时」。 */
  function dur(sec) {
    if (sec == null || isNaN(sec) || sec < 0) return '';
    sec = Math.round(sec);
    var d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600),
        m = Math.floor(sec % 3600 / 60);
    if (d) return d + '天' + h + '小时';
    if (h) return h + '小时' + m + '分';
    if (m) return m + '分';
    return sec + '秒';
  }

  /** 停滞文案。阈值来自后端 stats.stall_threshold_sec，不自己定。 */
  function stallText(idle) {
    if (idle == null) return '无进展';
    if (idle < 3600) return '已 ' + Math.round(idle / 60) + ' 分钟无进展';
    return '已 ' + dur(idle) + '无进展';
  }

  /** 从 started_at 到 (finished_at 或现在) 的秒数 */
  function elapsed(t) {
    if (!t.started_at) return null;
    var a = Date.parse(t.started_at);
    var b = t.finished_at ? Date.parse(t.finished_at) : Date.now();
    if (isNaN(a) || isNaN(b)) return null;
    return (b - a) / 1000;
  }

  function progOf(t) {
    var p = (t.fields || {}).progress;
    if (!p || p.total == null) return null;
    var pct = p.total > 0 ? Math.round(p.current / p.total * 100) : 0;
    return { cur: p.current, total: p.total, pct: pct, msg: p.message || '' };
  }

  function relTime(iso) {
    var ts = Date.parse(iso);
    if (isNaN(ts)) return '';
    return (Date.now() - ts) / 1000;
  }

  function hhmmss(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return '';
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  /* ---------------------------------------------------------- 窗口尺寸 */

  /** 挂件最小/最大宽度。下限保证卡片还能放下标题，上限是"再宽也没用"。 */
  var W_MIN = 300, W_MAX = 760;

  /**
   * 宽度 → 字号系数。
   *
   * ★ 为什么用 sqrt 而不是等比：
   *   等比的话 720px 宽时字号会到 28px —— 大窗口里满屏都是字，信息反而变少。
   *   平方根让字号**慢于宽度增长**：320→1.00、520→1.24、720→1.44。
   *   配合「宽到一定程度并排两列」，放大的收益才落在"看得更多"而不是"字更大"。
   */
  function scaleFor(w) {
    var k = Math.pow(Math.max(1, w) / 320, 0.45);
    return Math.max(0.85, Math.min(1.45, Math.round(k * 1000) / 1000));
  }

  /** 字号基准。面板里的「字号」以它为中点（13px ⇒ scale 1）。
   *  放大缩小从此由**字号**驱动，宽度只管"要不要并排两列"。 */
  var FS_BASE = 13;

  /** 宽过这个值就并排两列 —— 「放大」的意义是看到更多 */
  var W_WIDE = 470;

  /* ---------------------------------------------------------- 片段 */

  function cardHtml(t) {
    var isStall = !!STALLED[t.id];
    var isDone = !!TERM[t.column_id];
    var cls = 'card' + (isStall ? ' stall' : '') + (isDone ? ' done' : '');
    var h = '<div class="' + cls + '" data-task="' + esc(t.id) + '">';
    h += '<div class="ctitle">' + esc(t.title) + '</div>';

    var p = progOf(t);
    if (p) {
      h += '<div class="pwrap"><div class="pbar' + (isDone ? ' ok' : '') + '">'
         + '<i style="width:' + Math.max(0, Math.min(100, p.pct)) + '%"></i></div>'
         + '<div class="pmeta"><span class="pc">' + p.pct + '%</span>'
         + '<span class="pnum">' + p.cur + '/' + p.total + '</span>'
         + (p.msg ? '<span class="pmsg">' + esc(p.msg) + '</span>' : '')
         + '</div></div>';
    } else if (t.description && !isStall && t.description !== t.title) {
      // ★ 说明和标题一样时不重复显示。
      //   会话镜像建出来的任务就是这种情况（标题本身就是那条请求），
      //   卡片上会原样重复两遍，看起来像渲染出错。
      h += '<div class="cdesc">' + esc(t.description) + '</div>';
    }

    if (isStall) h += '<div class="stallmsg">⚠ ' + esc(stallText(t.idle_sec)) + '</div>';

    // ---- 以下都是「加一个可选字段，卡片就多一条信息」 ----
    // 每个字段的存在与否，直接决定用户能不能看见某类问题。
    var f = t.fields || {};

    // A1 失败/重试：没有它，「失败了」和「卡住了」长得一样
    var fail = f.failed;
    if (fail && fail.count) {
      h += '<div class="failmsg">✗ 失败 ' + fail.count + ' 次'
         + (fail.message ? ' · ' + esc(String(fail.message).slice(0, 40)) : '') + '</div>';
    }

    // A4 截止时间：没有它，只知道「跑了多久」，不知道「还剩多久」
    if (f.due_at) {
      var dl = (Date.parse(f.due_at) - Date.now()) / 1000;
      h += '<div class="duemsg' + (dl < 0 ? ' over' : '') + '">⏰ '
         + (dl < 0 ? '已超期 ' + dur(-dl) : '还剩 ' + dur(dl)) + '</div>';
    }

    h += '<div class="cmeta">';
    if (t.priority) h += '<span class="prio">P' + t.priority + '</span>';
    // A5 执行者：多 agent 并行时，一眼知道哪个是自己要管的
    if (f.assignee) h += '<span class="who">@' + esc(f.assignee) + '</span>';
    (t.tags || []).slice(0, 3).forEach(function (g) {
      h += '<span class="tag">' + esc(g) + '</span>';
    });
    // A3 步骤：卡片上只给个计数，明细在详情里（避免卡片撑高）
    if (f.steps && f.steps.length) {
      var dn = f.steps.filter(function (s) { return s.state === 'done'; }).length;
      h += '<span class="stepsc">▤ ' + dn + '/' + f.steps.length + ' 步</span>';
    }
    // A2 产物：完成类任务的价值就在产物上
    if (f.artifacts && f.artifacts.length) {
      h += '<span class="artsc">📎 ' + f.artifacts.length + '</span>';
    }
    var e = elapsed(t);
    if (e != null) h += '<span class="spacer"></span><span class="el">' + dur(e) + '</span>';
    h += '</div></div>';
    return h;
  }

  /* ---------------------------------------------------------- 折叠状态
   *
   * ★ 为什么必须存在模块里、而不是只靠 DOM 上的 class：
   *   宿主每 2 秒轮询一次数据，数据一变就 draw() ⇒ renderWidget 整块换
   *   innerHTML。折叠状态如果只活在 DOM 上，就被这次重绘抹掉了 ——
   *   用户点一下，两秒内自己弹回来，看起来就是"箭头点了没反应"。
   *   状态必须活在重绘之外，重绘时再把它重新铺上去。
   *
   * 语义：只记**用户显式点过的**列；没点过的列沿用默认（有任务就展开）。
   *   用三态（未记录 / 展开 / 收起）而不是布尔，是因为"空列默认收起"这个
   *   默认值也要能被用户翻过来。
   */
  var COLLAPSED = Object.create(null);

  function isOpen(cid, n) {
    if (cid && Object.prototype.hasOwnProperty.call(COLLAPSED, cid)) {
      return COLLAPSED[cid];
    }
    return n > 0;
  }

  function groupHtml(col, tasks, cls) {
    var open = isOpen(col.id, tasks.length);
    // 列内停滞数：这是 theme-c 原来那个「卡住了」置顶分区的**替代**。
    // 原来是把停滞任务从列里搬走、再合成一个分区 —— 代价是列会被搬空、
    // 连列名都跟着消失（用户报的「进行中的任务显示成卡住」就是这么来的）。
    // 改成"列名不动、只加一个警示角标"：信息一样在，但不破坏列的语义。
    var nStall = tasks.filter(function (t) { return STALLED[t.id]; }).length;
    var h = '<div class="grp' + (cls || '') + '">';
    h += '<div class="ghead" data-toggle="1" data-col="' + esc(col.id) + '">'
       + '<span class="chev">'
       + (open ? '▼' : '▶') + '</span>'
       + '<span class="cbar" style="background:var(--col-' + esc(col.color || 'gray') + ')"></span>'
       + '<span class="gtitle"' + (open ? '' : ' style="color:var(--fg-3)"') + '>'
       + esc(col.title) + '</span>';
    if (nStall) h += '<span class="gstall" title="该列有卡住的任务">⚠' + nStall + '</span>';
    h += '<span class="gcount">' + tasks.length + '</span>';
    if (col.wip_limit && col.id === 'doing') {
      h += '<span class="gwip">WIP ' + tasks.length + '/' + col.wip_limit + '</span>';
    }
    h += '</div><div class="cards' + (open ? '' : ' hidden') + '">'
       + tasks.map(cardHtml).join('') + '</div></div>';
    return h;
  }

  function topbarHtml(stats) {
    var stalled = stats.stalled || 0;
    var h = '<div class="bar"><span class="dot"></span>'
          + '<span class="ttl">' + esc(BOARD.title || 'Board') + '</span>';
    if (stalled) h += '<span class="pill alert">' + stalled + ' 个卡住</span>';
    else h += '<span class="pill ok">全部正常</span>';
    // ★ 按钮重规划（Windows 惯例）：
    //   右上角只放**窗口控制** —— `—` 收起为迷你条、`✕` 关闭（隐藏到托盘）。
    //   原先这里是「↗ — ⤓」：↗ 是应用动作却混在窗口控制里，
    //   而 ⤓ 与 ✕ 语义重复、✕ 又摆在右下角（Windows 用户找关闭一律去右上角）。
    //   「↗ 浏览器打开」已经挪到右下角，和 ⚙ 放在一起。
    //
    // ★ 每个按钮都带 data-act 标注**角色**，宿主按角色绑定，绝不按位置索引。
    //   踩过的坑：宿主原来写 `btns[0].onclick = open_board()`，假设顶栏是
    //   「↗ —」。但详情页的顶栏是「← ✕」⇒ btns[0] 正好是**返回键**，
    //   于是"点返回打开了浏览器"。内容层换一个视图，宿主就绑错一个按钮，
    //   而且**不报错**——只是行为离谱。角色标注让这种错绑不可能发生。
    h += '<span class="icobtn winbtn" data-act="mini" title="收起为迷你条">—</span>'
       + '<span class="icobtn winbtn winbtn-close" data-act="close" '
       + 'title="关闭（隐藏到托盘；托盘右键可退出）">✕</span></div>';
    return h;
  }

  function srcbarHtml() {
    // 三种来源要分得清：实时看板 / 演示数据 / 内置样例。
    // 原先只判断了 live，导致演示数据被标成「看板服务未启动」—— 会让人
    // 误判服务挂了，这类"标注错"比不标更坏（它会让你去查一个不存在的问题）。
    var src = D._source;
    var tag = src === 'live' ? '<span class="live">● 实时看板</span>'
            : src === 'demo' ? '<span class="demo">● 演示数据</span>（代码开发场景，非真实任务）'
            : '<span class="sample">● 样例数据</span>（看板服务未启动）';
    // ★ 这里曾经是 `D._fetched_at.slice(11, 19) + ' UTC'` —— 直接切 ISO 字符串。
    //   ISO 串（toISOString()）的时间部分是 **UTC**，所以标题栏比本地时间少 8 小时，
    //   用户看到的就是"时间是错的"。而且它绕过了下面已有的 hhmmss()，
    //   同一份代码里两种时间口径并存，很难一眼看出来。
    //   一律走 hhmmss()：它用 getHours()/getMinutes()/getSeconds()，即**本地时间**。
    return '<div class="srcbar">数据源：' + tag
         + '<span class="spacer"></span>'
         + '<span>' + esc(hhmmss(D._fetched_at)) + '</span>'
         + '</div>';
  }

  /* ---------------------------------------------------------- 四个状态 */

  function viewExpanded(theme) {
    var stats = BOARD.stats || {};
    var h = '<div class="widget">' + topbarHtml(stats) + srcbarHtml()
          + '<div class="rate"><i style="width:' + (stats.percent || 0) + '%"></i></div>';

    if (!ALL.length) return '<div class="widget">' + topbarHtml(stats) + srcbarHtml()
                             + viewEmptyInner() + '</div>';

    // 列组先拼到 body，最后统一包进 .cols2 ——
    // 窗口宽过 W_WIDE 时由 CSS 变成两栏（而不是把字放大）
    var body = '';
    COLS.forEach(function (c) {
        var list = BYCOL[c.id] || [];
      body += groupHtml(c, list);
    });
    return h + '<div class="cols2">' + body + '</div></div>';
  }

  function viewEmptyInner() {
    return '<div class="empty"><div class="big">没有任务</div>'
         + '看板确实是空的（不是读不到数据）</div>';
  }

  function viewUnreachable() {
    // ★ 这一屏的文案曾经是「看板服务未启动 / 连不上 127.0.0.1:8791 /
    //   启动看板服务」——那是"界面走 HTTP 找服务"的老架构留下的。
    //   现在数据是**进程内直读本地 sqlite**：没有服务、没有端口，
    //   让用户去启动一个不存在的东西，比不提示更坏（会把人引到错的方向）。
    var h = '<div class="widget mini"><div class="bar">'
          + '<span class="dot bad"></span>'
          + '<span class="ttl">' + esc(BOARD.title || 'Board') + '</span>'
          + '<span class="icobtn" data-act="mini" title="收起">—</span></div>'
          + '<div class="err"><div class="e1">读不到看板数据</div>'
          + '<div class="e2">挂件直接读本地数据库文件，当前读不出来。<br>'
          + '数据仍在磁盘上，没有被删。</div>'
          + '<button class="btn">重试读取</button></div></div>';
    return h;
  }

  function viewMini() {
    var stats = BOARD.stats || {};
    var n = stats.total || 0, st = stats.stalled || 0;
    var h = '<div class="widget mini">'
          + '<div class="rate"><i style="width:' + (stats.percent || 0) + '%"></i></div>'
          + '<div class="minibar"><span class="dot' + (st ? ' bad' : '') + '"></span>'
          + '<span class="txt"><span class="num">' + n + '</span> 个任务';
    if (st) h += ' · <span class="alert">' + st + ' 个卡住</span>';
    h += '</span><span class="icobtn" data-act="expand" title="展开">▴</span></div></div>';
    return h;
  }

  function viewDetail(taskId) {
    var t = taskById(taskId) || ALL[0];
    if (!t) return viewEmpty();
    var p = progOf(t), e = elapsed(t);
    var h = '<div class="widget"><div class="bar">'
          + '<span class="icobtn" data-act="back" data-back="1" title="返回">←</span>'
          + '<span class="ttl">任务详情</span>'
          + '<span class="icobtn" data-act="back" data-back="1" title="返回">×</span>'
          + '</div><div class="dbody">'
          + '<div class="dtitle" style="margin-bottom:9px">' + esc(t.title) + '</div>'
          + '<div class="kv">';
    var col = null;
    COLS.forEach(function (c) { if (c.id === t.column_id) col = c; });
    h += '<div class="k">所在列</div><div class="v">' + esc(col ? col.title : t.column_id) + '</div>';
    if (p) {
      h += '<div class="k">进度</div><div class="v">'
         + '<div class="pbar"><i style="width:' + p.pct + '%"></i></div>'
         + '<div class="pmeta"><span class="pc">' + p.pct + '%</span>'
         + '<span class="pnum">' + p.cur + '/' + p.total + '</span></div>';
      if (p.msg) h += '<div style="font-size:11px;color:var(--fg-3);margin-top:3px">' + esc(p.msg) + '</div>';
      h += '</div>';
    }
    // 说明与标题相同时不重复（会话镜像建的任务就是这种）—— 见卡片渲染处的说明
    if (t.description && t.description !== t.title) {
      h += '<div class="k">说明</div><div class="v">' + esc(t.description) + '</div>';
    }
    if ((t.tags || []).length) {
      h += '<div class="k">标签</div><div class="v">'
         + t.tags.map(function (g) { return '<span class="tag">' + esc(g) + '</span>'; }).join(' ')
         + '</div>';
    }
    if (t.priority) h += '<div class="k">优先级</div><div class="v"><span class="prio">P' + t.priority + '</span></div>';
    // 加字段带来的信息，在详情里给完整的
    if ((t.fields || {}).assignee) {
      h += '<div class="k">执行者</div><div class="v"><span class="who">@'
         + esc(t.fields.assignee) + '</span></div>';
    }
    if ((t.fields || {}).due_at) {
      var dl2 = (Date.parse(t.fields.due_at) - Date.now()) / 1000;
      h += '<div class="k">截止</div><div class="v" style="color:'
         + (dl2 < 0 ? 'var(--bad)' : 'var(--fg)') + '">'
         + hhmmss(t.fields.due_at) + '（' + (dl2 < 0 ? '已超期 ' + dur(-dl2) : '还剩 ' + dur(dl2)) + '）</div>';
    }
    if ((t.fields || {}).failed) {
      h += '<div class="k">失败</div><div class="v" style="color:var(--bad)">'
         + t.fields.failed.count + ' 次'
         + (t.fields.failed.message ? ' · ' + esc(t.fields.failed.message) : '') + '</div>';
    }
    if (STALLED[t.id]) h += '<div class="k">状态</div><div class="v" style="color:var(--warn)">'
                          + esc(stallText(t.idle_sec)) + '</div>';
    if (t.created_at) {
      var r = relTime(t.created_at);
      h += '<div class="k">创建于</div><div class="v">'
         + hhmmss(t.created_at) + (r > 0 ? '（' + dur(r) + '前）' : '') + '</div>';
    }
    if (e != null) h += '<div class="k">耗时</div><div class="v">' + dur(e) + '</div>';
    h += '</div>';

    // 子步骤清单 —— 比「树」轻得多，但回答的是同一个问题：走到哪一步了。
    // 进度条说「完成多少」，步骤说「走到哪了」，两者不等价：
    // "3/7 步"往往比"43%"更能说明问题。这里只用扁平列表，不做缩进/递归。
    var steps = (t.fields || {}).steps;
    if (steps && steps.length) {
      var done = steps.filter(function (s) { return s.state === 'done'; }).length;
      h += '<div class="sect"><div class="h">步骤 · ' + done + '/' + steps.length + '</div>';
      steps.forEach(function (s) {
        var m = s.state === 'done' ? '✓' : (s.state === 'running' ? '▶' : '○');
        h += '<div class="step ' + esc(s.state || 'pending') + '">'
           + '<span class="sm">' + m + '</span><span>' + esc(s.name) + '</span></div>';
      });
      h += '</div>';
    }

    // A2 产物：完成类任务的价值就在产物上。现在只看到"完成"，
    // 看不到"产出了什么" —— 而这才是这类任务真正值得记的东西。
    var arts = (t.fields || {}).artifacts;
    if (arts && arts.length) {
      h += '<div class="sect"><div class="h">产出 · ' + arts.length + '</div>';
      arts.forEach(function (a) {
        h += '<div class="art"><span class="ai">📎</span>'
           + '<span class="an">' + esc(a.name || a) + '</span>'
           + (a.note ? '<span class="ad">' + esc(a.note) + '</span>' : '')
           + '</div>';
      });
      h += '</div>';
    }

    var evs = EVENTS[t.id] || [];
    if (evs.length) {
      h += '<div class="sect"><div class="h">变更流水 · ' + evs.length + ' 条</div>';
      evs.forEach(function (ev) {
        var detail = '';
        if (ev.kind === 'move') detail = (ev.from_col || '?') + ' → ' + (ev.to_col || '?');
        else if (ev.kind === 'advance') {
          var v = (ev.detail || {}).value || {};
          detail = '→ ' + (v.current != null ? v.current : '') + (v.total != null ? '/' + v.total : '');
        } else if (ev.kind === 'create') detail = '进入 ' + (ev.to_col || '');
        else if (ev.kind === 'update') detail = JSON.stringify((ev.detail || {}).fields || ev.detail || {});
        else if (ev.kind === 'delete') detail = '删除';
        h += '<div class="ev"><span class="t">' + hhmmss(ev.created_at) + '</span>'
           + '<span class="k2">' + esc(ev.kind) + '</span>'
           + '<span class="d">' + esc(detail) + '</span></div>';
      });
      h += '</div>';
    }
    return h + '</div></div>';
  }

  function viewEmpty() {
    return '<div class="widget mini">' + topbarHtml(BOARD.stats || {})
         + viewEmptyInner() + '</div>';
  }

  /* ---------------------------------------------------------- 对外 */

  window.renderWidget = function (el, state, opts) {
    opts = opts || {};
    var theme = opts.theme || 'theme-amber';
    // 尺寸与透明度：由调用方（工作台 / 桌面窗口）传入
    var w = Math.max(W_MIN, Math.min(W_MAX, Math.round(opts.width || 320)));
    var alpha = opts.alpha == null ? 1 : Math.max(0.25, Math.min(1, opts.alpha));
    // ★ 放大缩小改由**字号**驱动（原来是调宽度推出来的）。
    //   宽度还有用（决定要不要并排两列），但它不该同时兼任"字号"这一个旋钮。
    var sc = (opts.font ? Math.max(0.75, Math.min(1.5, opts.font / FS_BASE)) : scaleFor(w));

    var html;
    switch (state) {
      case 'detail':      html = viewDetail(opts.taskId); break;
      case 'mini':        html = viewMini(); break;
      case 'unreachable': html = viewUnreachable(); break;
      case 'empty':       html = viewEmpty(); break;
      default:            html = viewExpanded(theme);
    }
    el.innerHTML = html;
    el.className = theme;

    // 窗口属性写在挂件根元素上，CSS 通过 var(--w/--scale/--bg-alpha/--win-alpha) 消费
    var widget = el.querySelector('.widget');
    if (widget) {
      // 「前妻整容前」是唯一带背景图的主题：把两层背景塞进挂件内部。
      // ★ 必须是 .widget 的**子元素** —— --bgw 是百分比，按包含块解析，
      //   挂到挂件外面会让图按外面的宽度算，结果偏小偏位（踩过）。
      if (theme === 'theme-ex' && !widget.querySelector('.ex-bg')) {
        var bgLayer = document.createElement('div');
        bgLayer.className = 'ex-bg';
        bgLayer.innerHTML = '<i></i><b></b>';
        widget.insertBefore(bgLayer, widget.firstChild);
      }
      widget.style.setProperty('--w', w + 'px');
      widget.style.setProperty('--scale', String(sc));
      widget.style.setProperty('--bg-alpha', String(alpha));
      // ★ 「透明度」作用于**整个前端**（连文字一起透）——
      //   只调背景色 alpha 的话几乎看不出来，用户报过"调了基本无效"。
      document.documentElement.style.setProperty('--win-alpha', String(alpha));
      widget.classList.toggle('wide', w >= W_WIDE);
      // 高度上限跟着放大，否则"变大"只在横向生效
      widget.style.setProperty('--maxh', Math.round(460 * sc) + 'px');
    }

    // 组头折叠 / 卡片进详情 / 返回，在原型里就地生效
    el.querySelectorAll('[data-toggle]').forEach(function (g) {
      g.onclick = function () {
        var c = g.nextElementSibling, ch = g.querySelector('.chev');
        if (!c) return;
        c.classList.toggle('hidden');
        var nowHidden = c.classList.contains('hidden');
        if (ch) ch.textContent = nowHidden ? '▶' : '▼';
        // ★ 记下来。宿主每 2 秒可能重绘一次，不记就白点。
        var cid = g.getAttribute('data-col');
        if (cid) COLLAPSED[cid] = !nowHidden;
      };
    });
    el.querySelectorAll('.card').forEach(function (c) {
      c.onclick = function () {
        if (opts.onOpenTask) opts.onOpenTask(c.getAttribute('data-task'));
      };
      // ★ 右键菜单：内容层只负责把 **id 和落点** 交给宿主，菜单本身由宿主画。
      //   理由是这个项目一贯的分工 —— 渲染层不碰"会改数据的动作"
      //   （归档/删除都要落库+记事件，属于宿主的事）。
      c.oncontextmenu = function (e) {
        if (e && e.preventDefault) e.preventDefault();
        if (e && e.stopPropagation) e.stopPropagation();
        if (opts.onTaskMenu) opts.onTaskMenu(c.getAttribute('data-task'), e);
        return false;
      };
    });
    // ★ 返回键必须真的能返回。
    //   `data-back` 是详情页那两个「← / ×」上的标记，但**此前没有任何地方
    //   处理它** —— 也就是说详情页压根没有返回通路，只靠内容层画了个箭头。
    //   更糟的是宿主按位置绑按钮，把「←」绑成了"打开浏览器"（见 host.html）。
    //   现在由宿主传 onBack 进来（"回列表"是宿主的状态，内容层不该自己改）。
    el.querySelectorAll('[data-back]').forEach(function (b) {
      b.onclick = function (e) {
        if (e && e.stopPropagation) e.stopPropagation();
        if (opts.onBack) opts.onBack();
      };
    });
    return el;
  };

  // 供工作台/宿主读取，避免把阈值硬编码在多个地方
  window.WB_WIDGET_META = {
    W_MIN: W_MIN, W_MAX: W_MAX, W_WIDE: W_WIDE,
    scaleFor: scaleFor
  };

  /** 宿主换数据用：WB_RELOAD(bundle) 之后再调 renderWidget 即可。
   *  数据没换而只重渲染是浪费 —— 所以这里只换数据，不主动重绘。 */
  window.WB_RELOAD = function (data) { loadData(data); };

  window.WB_META = {
    source: D._source || 'unknown',
    total: (BOARD.stats || {}).total || 0,
    stalled: (BOARD.stats || {}).stalled || 0,
    fetchedAt: D._fetched_at || null
  };
})();
