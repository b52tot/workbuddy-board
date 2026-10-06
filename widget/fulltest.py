#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""挂件全功能测试 —— 把**真实的 host.inline.html** 装进无头浏览器逐个点。

为什么不用"读代码 + 人肉推演"：
  这个挂件已经出过好几次"按钮绑错、绑漏、点了没反应、点成了别的功能"，
  共同点是**代码看着都对、只有点下去才知道错**。而这类错全都发生在
  内容层（widget.js 画）与宿主层（host.html 事后按 DOM 绑事件）的接缝上 ——
  只有真的把页面跑起来、真的去点，才盖得住。

做法：
  1. 读 host.inline.html（**就是打包进 exe 的那一份**，不是另写一份测试页）
  2. 在 <head> 最前面注入桩桥接（window.pywebview.api），记录每次 IPC 调用
  3. 在 </body> 前注入驱动脚本，逐个点击 / 拖动 / 量文字，产出 JSON 结果
  4. 无头浏览器跑完 --dump-dom，取回结果并判定

用法：
    python fulltest.py              # 跑，打印报告
    python fulltest.py --json       # 只输出 JSON（给上层脚本消费）
返回码：0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "host.inline.html")

# ---------------------------------------------------------------- 桩桥接
# 记录每一次 IPC 调用，测试断言就靠它。
STUB = r"""
<script>
window.__IPC = [];
function __rec(n, a) {
  window.__IPC.push({ n: n, a: Array.prototype.slice.call(a) });
}
window.__FT = { ok: false, result: null };

// 假数据：四列都有人，含一个停滞任务（走「状态」那一行）、
// 一个有 progress 字段的任务（走进度条）、一个超长标题（走省略号）
(function () {
  var now = Date.now();
  function iso(msAgo) { return new Date(now - msAgo).toISOString(); }
  var doing = {
    id: 't-doing',
    title: '修复挂件启动 21 秒无响应（pywebview 桥接递归爆栈）',
    description: 'host.py 里 api.window / api.tray 是公开属性，pywebview 构建 JS 桥接时会递归下钻到 .NET 控件树，撞上 Rectangle.Empty 自引用。',
    column_id: 'doing', position: 1000, priority: 5,
    tags: ['挂件', 'bugfix'], fields: {},
    created_at: iso(37 * 60000), updated_at: iso(37 * 60000),
    started_at: iso(37 * 60000), finished_at: null, idle_sec: 2220
  };
  var doneTask = {
    id: 't-done', title: '一个已经完成的任务', description: null,
    column_id: 'done', position: 1000, priority: 0, tags: [], fields: {},
    created_at: iso(7200000), updated_at: iso(7200000),
    started_at: iso(7200000), finished_at: iso(60 * 60000), idle_sec: null
  };
  window.__BOARD = {
    title: 'WorkBuddy Board',
    columns: [
      { id: 'todo', title: '待办', color: 'gray', is_terminal: 0, wip_limit: null },
      { id: 'doing', title: '进行中', color: 'blue', is_terminal: 0, wip_limit: 5 },
      { id: 'blocked', title: '阻塞', color: 'red', is_terminal: 0, wip_limit: null },
      { id: 'done', title: '已完成', color: 'green', is_terminal: 1, wip_limit: null }
    ],
    tasks_by_column: { todo: [], doing: [doing], blocked: [], done: [doneTask] },
    stats: {
      total: 2, finished: 1, active: 1, percent: 50, agg_progress: 0,
      stalled: 1, stall_threshold_sec: 900,
      per_column: { todo: 0, doing: 1, blocked: 0, done: 1 }
    },
    stalled_ids: ['t-doing'], server_seq: 3, archived: 0,
    generated_at: iso(0)
  };
  window.__EVENTS = {
    't-doing': [{ seq: 1, task_id: 't-doing', kind: 'create', from_col: null,
                  to_col: 'todo', actor: 'agent', detail: { title: doing.title },
                  created_at: iso(37 * 60000) }]
  };
})();

window.pywebview = { api: {
  log: function () { __rec('log', arguments); },
  get_config: function () {
    __rec('get_config', arguments);
    // show_mcp_guide: true = "首次启动且 MCP 未注册"，用来验证引导条会摆出来
    return Promise.resolve({ ok: true, signature: '保大', on_top: true,
                             has_source: true, poll_ms: 2000, poll_ms_mini: 12000,
                             show_mcp_guide: true,
                             build: { frozen: true, when: '10-06 22:08',
                                      mb: '13.99', text: '10-06 22:08 · 13.99 MB' } });
  },
  dismiss_mcp_guide: function () {
    __rec('dismiss_mcp_guide', arguments);
    return Promise.resolve({ ok: true });
  },
  get_state: function () {
    __rec('get_state', arguments);
    return Promise.resolve({ ok: true, width: 300, height: 520, alpha: 1,
                             theme: 'theme-amber', on_top: true });
  },
  get_board: function () {
    __rec('get_board', arguments);
    return Promise.resolve({ ok: true, board: window.__BOARD,
                             events: window.__EVENTS });
  },
  move_window: function () { __rec('move_window', arguments); return Promise.resolve({ ok: true }); },
  hide_to_tray: function () { __rec('hide_to_tray', arguments); return Promise.resolve({ ok: true }); },
  open_board: function () { __rec('open_board', arguments); return Promise.resolve({ ok: true }); },
  resize: function () { __rec('resize', arguments); return Promise.resolve({ ok: true }); },
  save_state: function () { __rec('save_state', arguments); return Promise.resolve({ ok: true }); },
  set_on_top: function () { __rec('set_on_top', arguments); return Promise.resolve({ ok: true }); },
  set_alpha: function () { __rec('set_alpha', arguments); return Promise.resolve({ ok: true }); },
  start_board: function () { __rec('start_board', arguments); return Promise.resolve({ ok: true }); },
  quit: function () { __rec('quit', arguments); return Promise.resolve({ ok: true }); },
  get_events: function () { __rec('get_events', arguments); return Promise.resolve({ ok: true, events: [] }); },
  mcp_status: function () {
    __rec('mcp_status', arguments);
    return Promise.resolve({ ok: true, installed: false, points_to_exe: false,
                             is_exe_mcp: false, command: '', path: 'C:/x/mcp.json',
                             exe: 'C:/Desktop/BoardWidget.exe',
                             steps: '接下来还需要你做三步：\n  1) 重启 WorkBuddy\n  2) 连接器页点信任\n  3) list_tasks 验证' });
  },
  install_mcp: function () {
    __rec('install_mcp', arguments);
    return Promise.resolve({ ok: true, applied: true, path: 'C:/x/mcp.json',
                             backup: 'C:/x/mcp.json.bak-20261005-233000',
                             others: ['kali', 'motrix'],
                             steps: '接下来还需要你做三步：\n  1) 重启 WorkBuddy\n  2) 连接器页点信任\n  3) list_tasks 验证' });
  }
} };
</script>
"""

DRIVER = r"""
<script>
(function () {
  var R = { checks: [], nav: [], ipcSeen: [] };
  function chk(name, ok, detail) {
    R.checks.push({ name: name, ok: !!ok, detail: detail == null ? '' : String(detail) });
  }
  /** 把结果吐到 DOM 里给 --dump-dom 取走。run() 正常结束和异常兜底都走它。 */
  function emit(res) {
    window.__FT = { ok: true, result: res };
    var pre = document.createElement('pre');
    pre.id = 'ft-result';
    pre.textContent = b64(JSON.stringify(res));
    document.body.appendChild(pre);
  }
  function ipc(name) {
    var hit = window.__IPC.filter(function (c) { return c.n === name; });
    return hit.length ? hit[hit.length - 1] : null;
  }
  function ipcCount(name) {
    return window.__IPC.filter(function (c) { return c.n === name; }).length;
  }
  function clearIPC() { window.__IPC.length = 0; }
  // ★ 不能用 requestAnimationFrame 让位：无头模式下 rAF 基本不触发，
  //   用它会让整个驱动脚本悬在那里、一点结果都出不来。
  //   setTimeout(0) 在无头 + 虚拟时钟下是可靠的。
  function frame() {
    return new Promise(function (r) { setTimeout(r, 0); });
  }
  function b64(str) {
    var bytes = new TextEncoder().encode(str), bin = '';
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }
  function q(sel, root) { return (root || document).querySelector(sel); }
  function qa(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }
  function md(target, type, x, y) {
    target.dispatchEvent(new MouseEvent(type, {
      bubbles: true, cancelable: true, view: window,
      screenX: x, screenY: y, clientX: x, clientY: y
    }));
  }
  /** 点一个按钮，并**先确认它真的有回调**。
   *
   *  ★ 为什么要这个包装：直接写 `el.onclick()`，在"按钮压根没绑回调"时
   *    会抛 TypeError，把整个驱动脚本打断 ⇒ 只留下一条"驱动脚本异常"，
   *    真正的原因（哪个按钮是死的）反而说不出来。
   *    变异测试时实测踩到：删掉返回键的绑定，报告只说"驱动脚本异常"。
   *    现在先断言回调存在，失败就明确报"这个按钮没绑"。
   */
  function press(el, label) {
    if (!el) { chk(label + '/按钮存在', false, '找不到元素'); return false; }
    if (typeof el.onclick !== 'function') {
      chk(label + '/按钮有绑定回调', false, 'onclick 为空 —— 这是个死按钮');
      return false;
    }
    chk(label + '/按钮有绑定回调', true, '');
    el.onclick({ stopPropagation: function () {} });
    return true;
  }

  // 拦导航：任何"点了按钮结果跳浏览器/刷新页面"都记下来
  var origOpen = window.open;
  window.open = function (u) { R.nav.push('window.open(' + u + ')'); return null; };
  var startHref = location.href;
  window.addEventListener('beforeunload', function () { R.nav.push('beforeunload'); });

  function widgetRoot() { return q('#app .widget') || q('#app > *'); }
  function viewName() { return (window.CUR_STATE || '?'); }

  /* ---------------------------------------------------------- 文字/布局 */
  function ownText(el) {
    var s = '';
    for (var i = 0; i < el.childNodes.length; i++) {
      var n = el.childNodes[i];
      if (n.nodeType === 3) s += n.nodeValue;
    }
    return s.replace(/\s+/g, '');
  }
  function hasCJK(s) { return /[\u4e00-\u9fff]/.test(s); }

  function checkLayout(tag) {
    var W = widgetRoot();
    if (!W) { chk(tag + '/有挂件根元素', false, '找不到 #app .widget'); return; }
    var wr = W.getBoundingClientRect();

    // 1) 中文不能竖排（宽度小于两个字宽 ⇒ 一字一行）
    var vertical = [];
    qa('*', W).forEach(function (el) {
      var t = ownText(el);
      if (!t || !hasCJK(t)) return;
      var cs = getComputedStyle(el);
      var fs = parseFloat(cs.fontSize) || 12;
      var r = el.getBoundingClientRect();
      if (r.width > 0 && r.width < 2 * fs) {
        vertical.push(el.className + '：「' + t.slice(0, 12) + '」宽 ' + Math.round(r.width) + 'px / 字号 ' + fs);
      }
    });
    chk(tag + '/中文不竖排', vertical.length === 0, vertical.join(' | '));

    // 2) 元素不能横向跑出挂件之外（跑出去 = 被裁掉，字就看不见了）
    var out = [];
    qa('*', W).forEach(function (el) {
      if (el.offsetParent === null && el.tagName !== 'HTML') return;
      var r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      if (r.right > wr.right + 1 || r.left < wr.left - 1) {
        out.push((el.className || el.tagName) + ' [' + Math.round(r.left) + ',' + Math.round(r.right) + '] 挂件 [' + Math.round(wr.left) + ',' + Math.round(wr.right) + ']');
      }
    });
    chk(tag + '/无元素溢出挂件边界', out.length === 0, out.slice(0, 4).join(' | '));

    // 3) 挂件自身不横向溢出容器
    var app = document.getElementById('app');
    chk(tag + '/挂件不撑破容器', W.scrollWidth <= W.clientWidth + 2,
        'scrollWidth=' + W.scrollWidth + ' clientWidth=' + W.clientWidth);
    if (app) {
      chk(tag + '/页面不横向滚动', app.scrollWidth <= app.clientWidth + 2,
          'scrollWidth=' + app.scrollWidth + ' clientWidth=' + app.clientWidth);
    }
  }

  /* ---------------------------------------------------------- 死按钮 */
  var SELECTORS = '.icobtn, #foot .fb, .err .btn, .ghead[data-toggle], .card, [data-back]';
  function checkNoDeadButtons(tag) {
    var dead = [];
    qa(SELECTORS).forEach(function (el) {
      var has = !!el.onclick;
      if (!has && el.classList.contains('ghead')) has = true;   // 列头由 renderWidget 绑
      if (!has) {
        // 事件委托也算（检查祖先有没有监听很难，这里只认直接 onclick）
        dead.push((el.className || el.tagName) + '「' + (el.textContent || '').trim().slice(0, 6) + '」');
      }
    });
    chk(tag + '/没有点了没反应的按钮', dead.length === 0, dead.join(' | '));
  }

  /* ---------------------------------------------------------- 文案 */
  // ★ 文案里不许出现"当前架构里根本不存在的东西"。
  //   这个挂件曾经是「界面 → HTTP → 看板服务 → sqlite」，后来改成
  //   **进程内直读 sqlite**（没有服务、没有端口）。但文案改漏了好几处：
  //   还写着「正在连接看板…」「连不上 127.0.0.1:8791」「启动看板服务」——
  //   让用户去启动一个不存在的东西，比不提示更坏（会把人引到错方向）。
  //   这类失效只有"逐字读界面"才发现得了，所以做成自动判据。
  var BANNED = ['127.0.0.1', '连不上', '看板服务', 'UTC', 'undefined', 'NaN', '[object'];
  function checkWording(tag) {
    var txt = (q('#app').textContent || '') + (q('#foot').textContent || '');
    var hit = BANNED.filter(function (w) { return txt.indexOf(w) >= 0; });
    chk(tag + '/文案里没有过时或异常字样', hit.length === 0,
        '命中：' + hit.join('、'));
  }

  /* ---------------------------------------------------------- 类名必须有样式 */
  // ★ 静态检查：JS 里用 classList 操作的类名，CSS 里必须真有规则。
  //   踩过的坑：`cards.classList.toggle('hidden')` 一直是对的，
  //   但 widget.css 里**没有 `.hidden`** ⇒ 界面毫无变化 ⇒ 用户报"箭头点了没反应"。
  //   而当时的测试只断言 classList，全绿。所以这条必须单独查。
  var CLASS_USED = [];
  // 记录应用**实际**操作过的类名（不写死清单 —— 写死了就测不到新增的）
  (function () {
    var proto = window.DOMTokenList && DOMTokenList.prototype;
    if (!proto) return;
    ['add', 'remove', 'toggle'].forEach(function (m) {
      var orig = proto[m];
      proto[m] = function () {
        for (var i = 0; i < arguments.length; i++) {
          var a = arguments[i];
          if (typeof a === 'string' && CLASS_USED.indexOf(a) < 0) CLASS_USED.push(a);
        }
        return orig.apply(this, arguments);
      };
    });
  })();
  //   ★ 判据的实现方式也踩过坑：一开始是"造一个 <div class='cards X'> 再比对计算样式"，
  //     结果 `.wide`（要 `.widget.wide`）、`#panel.show`、`.fb.on` 全被误报 ——
  //     依赖祖先/后代选择器的规则，合成元素根本复现不出来。
  //     ⇒ 改成**直接扫样式表的选择器**：只要某条规则的 selectorText 里出现过
  //       这个类名就算有定义。既没有误报，也不受 DOM 结构影响。
  function classNamesInStylesheets() {
    var names = {};
    function walk(rules) {
      for (var j = 0; j < rules.length; j++) {
        var r = rules[j];
        if (r.selectorText) {
          var m = r.selectorText.match(/\.([A-Za-z_][\w-]*)/g) || [];
          for (var k = 0; k < m.length; k++) names[m[k].slice(1)] = 1;
        }
        if (r.cssRules && r.cssRules.length) walk(r.cssRules);   // @media / @supports
      }
    }
    for (var i = 0; i < document.styleSheets.length; i++) {
      var rs = null;
      try { rs = document.styleSheets[i].cssRules; } catch (e) { rs = null; }
      if (rs && rs.length) walk(rs);
    }
    return names;
  }

  function checkClassHasStyle() {
    var defined = classNamesInStylesheets();
    var miss = CLASS_USED.filter(function (c) { return !defined[c]; });
    chk('静态/classList 用到的类名在 CSS 里都有规则', miss.length === 0,
        'CSS 里没有规则的类名：' + miss.join('、'));
  }

  async function run() {
    // ---- 等宿主 boot 完成
    for (var i = 0; i < 200; i++) {
      if (window.renderWidget && !document.getElementById('boot')) break;
      await new Promise(function (r) { setTimeout(r, 50); });
    }
    chk('启动/宿主完成 boot', !!window.renderWidget && !document.getElementById('boot'),
        'boot 遮罩=' + (document.getElementById('boot') ? '还在' : '已摘'));

    // 冻结轮询，避免测试中途被重绘打断
    try { clearTimeout(window.TIMER); } catch (e) {}
    window.tick = function () {};

    chk('启动/署名写进了底栏', (q('#fsign').textContent || '').indexOf('保大') >= 0,
        '底栏署名="' + q('#fsign').textContent + '"');
    chk('启动/底部状态行有文字', (q('#fstat').textContent || '').trim().length > 0,
        '"' + q('#fstat').textContent + '"');
    chk('启动/本地时间显示（不含 UTC）',
        (q('#app').textContent || '').indexOf('UTC') < 0, '');
    chk('启动/get_board 被调用', ipcCount('get_board') >= 1, '次数=' + ipcCount('get_board'));

    /* ------------------------------------------------ 列表页 */
    window.CUR_STATE = 'expanded'; window.CUR_TASK = null; window.draw();
    await frame();
    checkLayout('列表页');
    checkWording('列表页');
    checkNoDeadButtons('列表页');

    // 列头折叠（记得住要能扛过重绘）
    var g = q('.ghead[data-col="doing"]');
    chk('列表页/找到进行中列头', !!g, '');
    if (g) {
      g.onclick();
      var cardsA = g.nextElementSibling;
      // ★ 断言"视觉上真的收起了"，不能只看 class ——
      //   class 名换掉但 CSS 没有对应规则时，界面毫无变化（真实踩过）。
      var clsA = cardsA.classList.contains('hidden');
      var visA = getComputedStyle(cardsA).display === 'none';
      chk('列表页/点列头能收起（class 与视觉同时生效）', clsA && visA,
          'class.hidden=' + clsA + ' display=' + getComputedStyle(cardsA).display);
      window.draw(); await frame();
      var g2 = q('.ghead[data-col="doing"]');
      var cardsB = g2.nextElementSibling;
      chk('列表页/收起状态扛过重绘',
          cardsB.classList.contains('hidden') &&
          getComputedStyle(cardsB).display === 'none',
          'class.hidden=' + cardsB.classList.contains('hidden') +
          ' display=' + getComputedStyle(cardsB).display);
      g2.onclick(); window.draw(); await frame();
    }
    // 列名与列数不能被改写
    var titles = qa('.gtitle').map(function (x) { return x.textContent.trim(); });
    chk('列表页/四列列名正确',
        titles.join(',') === '待办,进行中,阻塞,已完成', titles.join(','));
    /* ------------------------------------------------ MCP 首次启动引导条 */
    // ★ 为什么这条重要：MCP 没接上时 agent 一个任务都记不进来，
    //   而"空看板"和"没接上"在界面上**长得一模一样** —— 用户只会觉得这挂件没用。
    //   所以首次启动必须自己说清楚，不能等他去设置里翻。
    await (async function () {
      var g = q('.mcpguide');
      chk('MCP引导/首次启动摆出了引导条', !!g, '');
      chk('MCP引导/在列表页而且插在数据源条之后',
          !!g && g.previousElementSibling
          && g.previousElementSibling.classList.contains('srcbar'),
          g && g.previousElementSibling
            ? g.previousElementSibling.className : '无前兄弟');
      var txt = g ? g.textContent : '';
      chk('MCP引导/说清楚了"记不进来"这件事',
          txt.indexOf('记不进来') >= 0, txt.slice(0, 60));
      chk('MCP引导/视觉上真的可见',
          !!g && getComputedStyle(g).display !== 'none'
          && g.getBoundingClientRect().height > 20,
          g ? (getComputedStyle(g).display + ' h=' +
               Math.round(g.getBoundingClientRect().height)) : '');

      var inst = g && g.querySelector('[data-act="mcp-install"]');
      var dism = g && g.querySelector('[data-act="mcp-dismiss"]');
      chk('MCP引导/两个按钮都按角色绑上了',
          !!(inst && inst.onclick) && !!(dism && dism.onclick),
          'install=' + !!(inst && inst.onclick) + ' dismiss=' + !!(dism && dism.onclick));

      chk('MCP引导/按钮在挂件范围内（不会溢出被裁）',
          !!g && g.getBoundingClientRect().right <=
          document.documentElement.clientWidth + 1,
          g ? (Math.round(g.getBoundingClientRect().right) + ' vs ' +
               document.documentElement.clientWidth) : '');

      press(inst, 'MCP引导/点「立即接上」');
      await frame(); await frame();
      chk('MCP引导/点了会真的去注册', ipcCount('install_mcp') === 1,
          '次数=' + ipcCount('install_mcp'));

      var g2 = q('.mcpguide');
      var t2 = g2 ? g2.textContent : '';
      chk('MCP引导/注册成功后换成「还差两步」而不是直接消失',
          !!g2 && t2.indexOf('重启') >= 0,
          t2.slice(0, 90));

      var dism2 = g2 && g2.querySelector('[data-act="mcp-dismiss"]');
      press(dism2, 'MCP引导/点「知道了」');
      await frame(); await frame();
      chk('MCP引导/关掉后不再出现', !q('.mcpguide'), '');
      chk('MCP引导/关掉会落盘（下次启动不再弹）',
          ipcCount('dismiss_mcp_guide') === 1,
          '次数=' + ipcCount('dismiss_mcp_guide'));
    })();

    chk('列表页/没有凭空多出「卡住了」分区',
        titles.indexOf('卡住了') < 0, titles.join(','));

    // ★ 窗口"只能长、不能缩"的回归判据。
    //   踩过：`#app` 是 flex:1，内容比它矮时 `scrollHeight` 会被钳到容器高度
    //   ⇒ 量出来的永远是"当前窗口高度" ⇒ 用户手动拉高一次之后，
    //   窗口再也回不到贴合内容的高度，下面永远留一大片空白。
    //
    //   ⚠️ 判据必须写成"**贴合内容高度**"，不能写成"比 900 小" ——
    //      后者两种实现都能满足（scrollHeight 也会小于 900），等于装饰品。
    //      反向验证实测：只写"< 900"时，换回旧实现依然全绿。
    (function () {
      // ⚠️ 必须先让无头环境**和真机一样**：真机的 html/body 撑满窗口、#app 是
      //    flex:1 被拉伸，所以 scrollHeight 会被钳到容器高度。
      //    无头下 html{height:100%} 解析不出高度，#app 就不会被拉伸，
      //    scrollHeight 恰好等于内容高度 —— 那样两种实现结果一样，
      //    这条判据就永远为真（反向验证实测：换回旧实现依然全绿）。
      var fix = document.createElement('style');
      fix.textContent = 'html,body{height:900px !important}';
      document.head.appendChild(fix);

      var saveH = window.CFG.height;
      window.CFG.height = 900;                     // 假装用户手动拉得很高
      window.CUR_STATE = 'expanded'; window.draw();
      var want = Math.min(window.contentHeight() + 8, 900);
      var appScroll = document.getElementById('app').scrollHeight;
      clearIPC();
      window.fitWindow();
      var rs = ipc('resize');
      var got = rs ? rs.a[1] : null;
      chk('布局/窗口高度贴合内容（不是固守旧高度）',
          got !== null && Math.abs(got - want) <= 20,
          '期望≈' + Math.round(want) + ' 实际=' + got
          + '（内容=' + Math.round(window.contentHeight())
          + ' #app.scrollHeight=' + appScroll + '）');
      window.CFG.height = saveH;
      fix.remove();
      window.draw();
    })();

    // 说明与标题相同时不该重复显示两遍（会话镜像建的任务就是这种）
    var dupBoard = JSON.parse(JSON.stringify(window.__BOARD));
    dupBoard.tasks_by_column.doing[0] = JSON.parse(
      JSON.stringify(dupBoard.tasks_by_column.doing[0]));
    dupBoard.tasks_by_column.doing[0].description =
      dupBoard.tasks_by_column.doing[0].title;
    window.WB_RELOAD({ _source: 'live', _fetched_at: new Date().toISOString(),
                       board: dupBoard, events: {} });
    window.CUR_STATE = 'expanded'; window.draw(); await frame();
    var dcard = q('.card');
    var dtext = dcard ? (dcard.textContent || '') : '';
    var dt = dupBoard.tasks_by_column.doing[0].title;
    var occurs = dtext.split(dt).length - 1;
    chk('列表页/说明与标题相同时不重复显示', occurs <= 1,
        '标题在卡片文字里出现了 ' + occurs + ' 次');
    window.WB_RELOAD({ _source: 'live', _fetched_at: new Date().toISOString(),
                       board: window.__BOARD, events: window.__EVENTS });
    window.draw(); await frame();

    // ---- 按钮重规划（Windows 惯例）----
    // 右上角 = 窗口控制；右下角 = 应用动作。
    (function () {
      var barBtn = qa('#app .widget .bar .icobtn');
      var acts = barBtn.map(function (b) { return b.getAttribute('data-act'); });
      chk('按钮规划/右上角只有窗口控制（收起 + 关闭）',
          acts.length === 2 && acts.indexOf('mini') >= 0 && acts.indexOf('close') >= 0,
          '实际=' + acts.join(','));
      chk('按钮规划/「打开浏览器」不再混在窗口控制里',
          acts.indexOf('board') < 0, '实际=' + acts.join(','));
      chk('按钮规划/也不再有两个语义重复的"隐藏"按钮',
          acts.indexOf('tray') < 0, '实际=' + acts.join(','));

      var foot = document.getElementById('foot');
      var fa = qa('.fb', foot).map(function (b) { return b.getAttribute('data-act') || b.id; });
      chk('按钮规划/右下角是应用动作（↗ 打开 + ⚙ 设置）',
          fa.indexOf('board') >= 0 && fa.indexOf('gear') >= 0, '实际=' + fa.join(','));
      chk('按钮规划/右下角不再有 ✕（Windows 用户找关闭去右上角）',
          !q('#quit', foot), '');

      // 右上角 ✕ 是"关窗口"不是"退程序"：真 quit 会销毁窗口、再无恢复入口
      clearIPC();
      var bClose = q('[data-act="close"]');
      press(bClose, '按钮规划/右上角 ✕');
      chk('按钮规划/右上角 ✕ = 隐藏到托盘（不是销毁窗口）',
          ipcCount('hide_to_tray') === 1 && ipcCount('quit') === 0,
          'hide_to_tray=' + ipcCount('hide_to_tray') + ' quit=' + ipcCount('quit'));
    })();

    // 右下角 ↗ 打开看板
    clearIPC();
    var bBoard = q('#openboard');
    chk('列表页/右下角存在 ↗ 打开看板按钮', !!bBoard, '');
    press(bBoard, '列表页/↗ 打开看板');
    chk('列表页/↗ 调用 open_board', ipcCount('open_board') === 1, '次数=' + ipcCount('open_board'));

    /* ------------------------------------------------ 点卡片进详情 */
    window.CUR_STATE = 'expanded'; window.CUR_TASK = null; window.draw();
    await frame();
    var card = q('.card');
    chk('列表页/有可点任务卡片', !!card, '');
    if (card) card.onclick();
    await frame();
    chk('列表页/点卡片进详情', window.CUR_STATE === 'detail', '当前=' + window.CUR_STATE);

    /* ------------------------------------------------ 详情页 ★重点 */
    checkLayout('详情页');
    checkWording('详情页');
    checkNoDeadButtons('详情页');

    var backs = qa('[data-back]');
    chk('详情页/有返回按钮（← 与 ×）', backs.length === 2, '个数=' + backs.length);

    // ★ 回归：点返回**绝不能**打开浏览器
    window.CUR_STATE = 'detail';
    clearIPC();
    R.nav.length = 0;
    press(backs[0], '详情页/← 返回');
    await frame();
    chk('详情页/点 ← 返回不调用 open_board', ipcCount('open_board') === 0,
        'open_board 次数=' + ipcCount('open_board'));
    chk('详情页/点 ← 真的回到列表页', window.CUR_STATE === 'expanded',
        '当前=' + window.CUR_STATE);

    if (card) card.onclick();
    await frame();
    var backs2 = qa('[data-back]');
    window.CUR_STATE = 'detail';
    clearIPC();
    R.nav.length = 0;
    press(backs2[1], '详情页/× 返回');
    await frame();
    chk('详情页/点 × 返回不调用 open_board', ipcCount('open_board') === 0,
        'open_board 次数=' + ipcCount('open_board'));
    chk('详情页/点 × 真的回到列表页', window.CUR_STATE === 'expanded',
        '当前=' + window.CUR_STATE);

    // 详情页的值列宽度下限（防"一字一行"）
    window.CUR_STATE = 'detail'; window.CUR_TASK = 't-doing'; window.draw();
    await frame();
    var vs = qa('.kv .v');
    var minV = vs.length ? Math.min.apply(null, vs.map(function (v) {
      return Math.round(v.getBoundingClientRect().width);
    })) : -1;
    chk('详情页/值列宽度≥96px（不竖排）', minV >= 96, '最小值=' + minV + 'px');

    // ★ 上面那条在正常宽度下恒为 216px —— **它不可能失败，等于装饰品**。
    //   真正会出问题的是"容器被压窄"这个条件，所以这里把挂件强行压到 120px
    //   再量一次：没有 minmax(96px,1fr) 那道防线时，值列会被压到 ~21px，
    //   也就是**一个汉字一行**（用户报的"状态文字竖着排列"）。
    var narrow = document.createElement('style');
    narrow.textContent = '#app .widget{width:120px !important}';
    document.head.appendChild(narrow);
    window.CUR_STATE = 'detail'; window.CUR_TASK = 't-doing'; window.draw();
    await frame();
    var vs2 = qa('.kv .v');
    var minV2 = vs2.length ? Math.min.apply(null, vs2.map(function (v) {
      return Math.round(v.getBoundingClientRect().width);
    })) : -1;
    chk('详情页/容器压到 120px 时值列仍≥96px（防一字一行）', minV2 >= 96,
        '最小值=' + minV2 + 'px');
    narrow.remove();
    window.draw(); await frame();
    var kvLabel = qa('.kv .k').map(function (k) { return k.textContent.trim(); });
    chk('详情页/状态行存在（停滞任务）', kvLabel.indexOf('状态') >= 0, kvLabel.join(','));
    chk('详情页/状态行文字是本地化短语',
        (q('#app').textContent || '').indexOf('无进展') >= 0, '');
    chk('详情页/返回箭头与关闭都在左上/右上', backs.length === 2, '');

    /* ------------------------------------------------ 收起态 */
    window.CUR_STATE = 'expanded'; window.draw();
    await frame();
    var bMini = q('[data-act="mini"]');
    chk('列表页/存在 — 收起按钮', !!bMini, '');
    press(bMini, '列表页/— 收起');
    await frame();
    chk('收起/点 — 进入收起态', window.CUR_STATE === 'mini', '当前=' + window.CUR_STATE);
    checkLayout('收起态');
    checkWording('收起态');
    var bExp = q('[data-act="expand"]');
    chk('收起态/存在 ▴ 展开按钮', !!bExp, '');
    press(bExp, '收起态/▴ 展开');
    await frame();
    chk('收起态/点 ▴ 回到展开态', window.CUR_STATE === 'expanded', '当前=' + window.CUR_STATE);

    /* ------------------------------------------------ 设置面板 */
    var gear = q('#gear'), panel = q('#panel');
    gear.onclick();
    chk('设置/点 ⚙ 面板打开', panel.classList.contains('show'), '');
    /* ---- 字号滑条（取代了原来的"宽度"）----
       判据量的是**卡片标题的计算字号**，不是 CFG.font 这个变量 ——
       变量改了但样式没消费，是"看着像做了"的典型。 */
    clearIPC();
    var titleQ = '#app .widget .ctitle';
    var fsBefore = parseFloat(getComputedStyle(q(titleQ)).fontSize);
    var rf = q('#rf');
    chk('设置/没有宽度滑条了（已换成字号）', !q('#rw'), '');
    rf.value = 17; rf.oninput.call(rf);
    await frame();
    chk('设置/字号滑条改的是字号', window.CFG.font === 17,
        'CFG.font=' + window.CFG.font);
    chk('设置/字号滑条保存状态',
        ipcCount('save_state') >= 1 &&
        (ipc('save_state').a[0] || {}).font === 17,
        JSON.stringify(ipc('save_state') && ipc('save_state').a[0]));
    chk('设置/字号值回显', (q('#vf').textContent || '').indexOf('17') === 0,
        q('#vf').textContent);
    var fsAfter = parseFloat(getComputedStyle(q(titleQ)).fontSize);
    chk('设置/字号滑条真的把字放大了（量计算字号）',
        fsAfter > fsBefore + 1,
        fsBefore + 'px → ' + fsAfter + 'px');
    chk('设置/字号滑条在合法区间内',
        +rf.min === 10 && +rf.max === 18, 'min=' + rf.min + ' max=' + rf.max);
    rf.value = 13; rf.oninput.call(rf); await frame();

    /* ---- 透明度：必须作用于**整个前端**，不是只把背景色调淡 ---- */
    var ra = q('#ra');
    ra.value = 40; ra.oninput.call(ra);
    await frame();
    chk('设置/透明度值回显', (q('#va').textContent || '').indexOf('40') === 0,
        q('#va').textContent);
    var winA = getComputedStyle(document.documentElement)
                 .getPropertyValue('--win-alpha').trim();
    chk('设置/透明度写到了 --win-alpha（整前端那一档）',
        Math.abs(parseFloat(winA) - 0.4) < 0.02, '--win-alpha=' + winA);
    var htmlOp = parseFloat(getComputedStyle(document.documentElement).opacity);
    chk('设置/整个前端真的变透了（量 html 的计算 opacity）',
        Math.abs(htmlOp - 0.4) < 0.02, 'html opacity=' + htmlOp);
    ra.value = 100; ra.oninput.call(ra); await frame();

    clearIPC();
    var ra = q('#ra');
    ra.value = 60; ra.oninput.call(ra);
    await frame();
    chk('设置/透明度滑条保存状态',
        ipcCount('save_state') >= 1 && (ipc('save_state').a[0] || {}).alpha === 0.6,
        JSON.stringify(ipc('save_state') && ipc('save_state').a[0]));

    clearIPC();
    var rt = q('#rontop');
    if (rt) { rt.checked = false; rt.onchange.call(rt); }
    chk('设置/置顶勾选调用 set_on_top(false)',
        ipcCount('set_on_top') === 1 && ipc('set_on_top').a[0] === false,
        JSON.stringify(ipc('set_on_top') && ipc('set_on_top').a));

    /* ---- 四套主题：每一套都要真的换出颜色来 ---- */
    // 判据量的是**计算出来的颜色**（:root 上的 --accent），不是 class 名 ——
    // 只切 class 而 CSS 里没有对应规则，是"看着像做了"的经典失效。
    await (async function () {
      // ★ 先把透明度复位到 100%。上一个用例把滑条停在 60% 上没收尾，
      //   而底栏半透明时它的背景是**桌面**（color-mix 出来带 0.6 的 alpha），
      //   对比度在这个前提下根本没有定义。
      //   判据必须在明确条件下量 —— 不能"碰巧当前是什么就量什么"，
      //   否则它测的是上一个用例的遗留状态，不是主题本身。
      var _raB = q('#ra'), _alphaBak = _raB ? _raB.value : null;
      if (_raB) { _raB.value = 100; _raB.oninput.call(_raB); }
      await frame(); await frame();

      var want = ['theme-midnight', 'theme-amber', 'theme-ink', 'theme-ex'];
      var names = qa('#theme button').map(function (b) {
        return { t: b.dataset.t, n: b.textContent.trim() };
      });
      chk('设置/主题区有四个按钮', names.length === 4,
          names.map(function (x) { return x.n; }).join(','));
      chk('设置/四个主题名一字不改',
          names.map(function (x) { return x.n; }).join(',') ===
          '午夜蓝,琥珀石墨,亚克力白,前妻整容前',
          names.map(function (x) { return x.n; }).join(','));

      // 对比度小工具（WCAG 2.1 相对亮度）。判据要量"看不看得清"，
      // 就得把颜色还原成亮度比，而不是比字符串 —— 颜色字符串不同 ≠ 可读。
      function parseRGB(s) {
        s = (s || '').trim();
        var m = /rgba?\(([^)]+)\)/.exec(s);
        if (m) {
          var p = m[1].split(/[,\s\/]+/).filter(function (x) { return x.length; })
                       .map(parseFloat);
          return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
        }
        // ★ Edge/Chrome 把 color-mix() 的结果序列化成 `color(srgb r g b)`（0~1 浮点），
        //   不是 rgb()。只认 rgb() 的解析器会在这里静默返回 null ——
        //   第一版就是这么挂的：crN 恒为 0，判据说"只量到 0 套"。
        m = /color\(\s*srgb\s+([^)]+)\)/.exec(s);
        if (m) {
          var seg = m[1].split('/');
          var v = seg[0].trim().split(/\s+/).map(parseFloat);
          return { r: v[0] * 255, g: v[1] * 255, b: v[2] * 255,
                   a: seg.length > 1 ? parseFloat(seg[1]) : 1 };
        }
        return null;
      }
      function lum(c) {
        function f(v) {
          v /= 255;
          return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
        }
        return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
      }
      function contrast(a, b) {
        var la = lum(a), lb = lum(b);
        return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
      }

      var seen = {}, seenFoot = {}, ok = true, detail = [], crBad = [], crN = 0;
      for (var i = 0; i < want.length; i++) {
        var b = q('#theme button[data-t="' + want[i] + '"]');
        if (!b) { ok = false; detail.push(want[i] + ':按钮缺失'); continue; }
        b.onclick();
        await frame(); await frame();
        // ★ 必须量**渲染出来的颜色**。第一版读的是
        //   getComputedStyle(document.documentElement) 上的 --accent，
        //   而主题变量是设在 #app 上的 ⇒ 读到的一直是 :root 的默认值，
        //   四套主题全"撞色"。这是判据自己找错了元素，不是样式坏了。
        var bgc = getComputedStyle(q('#app .widget')).backgroundColor;
        // ★ 底栏也要跟着变 —— 踩过：主题类只挂在 #app 上，而 #foot/#panel 是
        //   它的**兄弟节点**，拿不到主题变量 ⇒ "换主题只有中间变了、底下一成不变"。
        var footBg = getComputedStyle(document.getElementById('foot')).backgroundColor;
        if (seenFoot[footBg]) {
          ok = false;
          detail.push(want[i] + ' 的底栏色和 ' + seenFoot[footBg] + ' 撞了');
        }
        seenFoot[footBg] = want[i];
        // ★ 底栏那行小字（「共 N 个任务」/ 署名）只有 10px，走的是 --fg-3 这一档。
        //   只量"颜色换没换"远远不够 —— 四套主题的 --fg-3 原先全在 3.0~4.0:1，
        //   颜色确实换了，可浅色底上根本读不清。用户报的就是这个观感。
        //   所以这里量**对比度**：它才是"用户看不看得清"这个最终效果本身。
        var _fgRaw = getComputedStyle(q('#fstat')).color;
        var _bgRaw = getComputedStyle(document.getElementById('foot')).backgroundColor;
        var _fgc = parseRGB(_fgRaw);
        var _bgc = parseRGB(_bgRaw);
        if (_bgc && _bgc.a < 0.999) {
          // 半透明时背景色是"桌面"，无法定义对比度。明说跳过，不假装通过。
          detail.push(want[i] + ' 底栏半透明(a=' + _bgc.a + ')：对比度不适用，跳过');
        } else if (_fgc && _bgc) {
          var _cr = contrast(_fgc, _bgc);
          crN++;
          detail.push(want[i] + ' 底栏对比度 ' + _cr.toFixed(2) + ':1');
          if (_cr < 4.5) { crBad.push(want[i] + ' ' + _cr.toFixed(2) + ':1'); }
        } else {
          // 带上原始串，否则"读不出来"这四个字帮不上任何忙。
          crBad.push(want[i] + ' 颜色读不出来（fg=' + _fgRaw + ' bg=' + _bgRaw + '）');
        }
        if (seen[bgc]) { ok = false; detail.push(want[i] + ' 的配色和 ' + seen[bgc] + ' 撞了'); }
        seen[bgc] = want[i];
        detail.push(want[i] + '→' + bgc);
        if (!bgc || bgc === 'rgba(0, 0, 0, 0)') {
          ok = false; detail.push(want[i] + ':背景色读不出来');
        }
      }
      chk('设置/四套主题各换出不同配色（中间 + 底栏都量）', ok, detail.join('  '));
      // ★ crN === 4 这条不能省：万一四个主题按钮一个都点不到，循环全走 continue，
      //   crBad 会是空的 ⇒ "全部达标"和"一个都没量"长得一模一样。
      //   写死分母，才排除这种"分母收缩"式的假绿。
      chk('★设置/底栏小字在四套主题下都读得清（对比度 ≥ 4.5:1，四套全量到）',
          crBad.length === 0 && crN === 4,
          crN !== 4 ? ('只量到 ' + crN + ' 套（期望 4）｜' + detail.join('  ')) : detail.join('  '));

      // 「前妻整容前」必须真的带上了背景图层
      var exBtn = q('#theme button[data-t="theme-ex"]');
      if (exBtn) { exBtn.onclick(); await frame(); await frame(); }
      var bgLayer = q('#app .widget .ex-bg');
      chk('设置/前妻整容前带上了背景大图层', !!bgLayer, '');
      chk('设置/背景层是两层的（整图 + 面部提亮）',
          !!bgLayer && !!bgLayer.querySelector('i') && !!bgLayer.querySelector('b'), '');
      chk('设置/背景层在挂件内部（否则百分比基准错、图会偏小偏位）',
          !!bgLayer && bgLayer.parentElement.classList.contains('widget'),
          bgLayer ? bgLayer.parentElement.className : '无');
      chk('设置/内容抬在背景之上（不然卡片会被盖住）',
          !!bgLayer && getComputedStyle(q('#app .widget .bar')).zIndex === '3',
          bgLayer ? getComputedStyle(q('#app .widget .bar')).zIndex : '无');

      // 回到默认主题
      var dflt = q('#theme button[data-t="theme-amber"]');
      if (dflt) { dflt.onclick(); await frame(); }
      // 把进本段前的透明度还回去（别给后面的用例留个"惊喜"）
      if (_raB && _alphaBak !== null) { _raB.value = _alphaBak; _raB.oninput.call(_raB); }
      await frame();
      clearIPC();
    })();

    gear.onclick();
    chk('设置/再点 ⚙ 面板收起', !panel.classList.contains('show'), '');

    /* ------------------------------------------------ MCP 注册引导 */
    // 挂件是"看"的、MCP 是"记"的；这一步接不上，agent 一个任务都记不进来。
    // 判据盯三件事：状态读得出来 / 按钮绑得上 / **后续引导（重启+信任）真的摆出来**。
    gear.onclick();
    await frame();
    var vmcp = q('#vmcp');
    chk('MCP/面板里有 MCP 状态行', !!vmcp, '');
    chk('MCP/状态能读出来（没停在"读取中"）',
        !!vmcp && vmcp.textContent.indexOf('读取中') < 0 &&
        vmcp.textContent.trim().length > 0, vmcp ? vmcp.textContent : '');
    chk('MCP/未注册时明确提示 agent 记不进任务',
        !!vmcp && vmcp.textContent.indexOf('记不进') >= 0, vmcp ? vmcp.textContent : '');

    var mcpBtn = q('#mcpbtn');
    chk('MCP/有注册按钮', !!mcpBtn, '');
    press(mcpBtn, 'MCP/注册按钮');
    await frame(); await frame();
    chk('MCP/点注册调用了 install_mcp', ipcCount('install_mcp') === 1,
        '次数=' + ipcCount('install_mcp'));

    var stepsBox = q('#mcpsteps');
    chk('MCP/引导区显示出来了', !!stepsBox && stepsBox.classList.contains('show'),
        stepsBox ? ('class=' + stepsBox.className) : '不存在');
    var stepTxt = stepsBox ? stepsBox.textContent : '';
    chk('MCP/引导里明确要求「重启 WorkBuddy」', stepTxt.indexOf('重启') >= 0, stepTxt.slice(0, 80));
    chk('MCP/引导里明确要求去「连接器」点「信任」',
        stepTxt.indexOf('连接器') >= 0 && stepTxt.indexOf('信任') >= 0, stepTxt.slice(0, 120));
    chk('MCP/引导里给了验证办法',
        stepTxt.indexOf('list_tasks') >= 0, stepTxt.slice(0, 160));
    chk('MCP/引导区在视觉上真的可见',
        !!stepsBox && getComputedStyle(stepsBox).display !== 'none',
        stepsBox ? getComputedStyle(stepsBox).display : '');
    chk('MCP/引导区文字不竖排不溢出',
        !!stepsBox && stepsBox.getBoundingClientRect().width >=
        (parseFloat(getComputedStyle(stepsBox).fontSize) || 12) * 2
        && stepsBox.scrollWidth <= stepsBox.clientWidth + 2,
        stepsBox ? (Math.round(stepsBox.getBoundingClientRect().width) + 'px, scrollW='
                   + stepsBox.scrollWidth + ' clientW=' + stepsBox.clientWidth) : '');
    gear.onclick();

    clearIPC();
    var openb = q('#openboard');
    press(openb, '底栏/↗');
    chk('底栏/↗ 打开浏览器看板', ipcCount('open_board') === 1, '次数=' + ipcCount('open_board'));
    chk('底栏/没有 ✕（它已经在右上角了）', !q('#quit'), '');

    /* ------------------------------------------------ 拖动 / 拖拉 */
    var savedW = window.CFG.width, savedH = window.CFG.height;
    window.CFG.width = 300; window.CFG.height = 520;
    window.CUR_STATE = 'expanded'; window.draw();
    await frame();
    clearIPC();
    var ttl = q('#app .widget .bar .ttl');
    if (ttl) {
      md(ttl, 'mousedown', 1000, 500);
      md(document, 'mousemove', 1040, 530);
      await frame(); await frame();
      md(document, 'mouseup', 1040, 530);
      await frame();
    }
    chk('拖动/拖顶栏调用 move_window(40,30)',
        !!ipc('move_window') && ipc('move_window').a[0] === 40 && ipc('move_window').a[1] === 30,
        JSON.stringify(ipc('move_window') && ipc('move_window').a));

    // 拖按钮不能拖窗口
    clearIPC();
    var bTray2 = q('[data-act="close"]');
    if (bTray2) {
      md(bTray2, 'mousedown', 1000, 500);
      md(document, 'mousemove', 1080, 600);
      await frame();
      md(document, 'mouseup', 1080, 600);
      await frame();
    }
    chk('拖动/拖按钮不会移动窗口', ipcCount('move_window') === 0,
        'move_window 次数=' + ipcCount('move_window'));

    window.CFG.width = 300; window.CFG.height = 520;
    window.draw(); await frame();
    clearIPC();
    var grip = q('#grip');
    md(grip, 'mousedown', 1000, 500);        // 起点 (1000,500)，此时 300x520
    md(window, 'mousemove', 1100, 560);      // 右 100、下 60
    await frame();
    md(window, 'mouseup', 1100, 560);
    await frame();
    chk('拖拉/拖右下角调用 resize(400,580)',
        !!ipc('resize') && ipc('resize').a[0] === 400 && ipc('resize').a[1] === 580,
        JSON.stringify(ipc('resize') && ipc('resize').a));
    chk('拖拉/结束时保存尺寸',
        ipcCount('save_state') >= 1 && (ipc('save_state').a[0] || {}).width === 400,
        JSON.stringify(ipc('save_state') && ipc('save_state').a[0]));
    window.CFG.width = savedW; window.CFG.height = savedH;

    /* ------------------------------------------------ 点击的"位置" */
    // 同一个按钮，点它的不同部位（图标 / 文字 / 内层 span）都必须生效 ——
    // 事件目标是子节点，靠冒泡到容器；有一条链断了就会"点左边有反应、点右边没反应"。
    window.CUR_STATE = 'expanded'; window.CUR_TASK = null; window.draw();
    await frame();
    function clickOn(el) {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    }
    var headProbes = ['.cbar', '.gtitle', '.gcount', '.chev'];
    var headResults = [];
    // ★ 必须用 for 而不是 forEach：这里的回调里要 await，
    //   而 forEach 的回调不是 async 函数 ⇒ `await` 直接是语法错误
    //   （整个驱动脚本一行都不执行，现象是"测试没产出结果"）。
    for (var hi = 0; hi < headProbes.length; hi++) {
      var sel = headProbes[hi];
      var g = q('.ghead[data-col="doing"]');
      if (!g) { headResults.push(sel + ':无列头'); continue; }
      // ★ 同样量**视觉**状态，不量 class
      var before = getComputedStyle(g.nextElementSibling).display === 'none';
      var target = g.querySelector(sel);
      if (!target) { headResults.push(sel + ':不存在'); continue; }
      clickOn(target);
      var gAfter = q('.ghead[data-col="doing"]');
      var after = getComputedStyle(gAfter.nextElementSibling).display === 'none';
      headResults.push(sel + (after !== before ? ':生效' : ':**没反应**'));
      window.draw(); await frame();
    }
    chk('点击位置/点列头的每个部位都能折叠（图标/标题/计数/箭头）',
        headResults.every(function (s) { return /:生效$/.test(s); }), headResults.join(' '));

    var cardProbes = ['.ctitle', '.ctitle + *', '.cmeta'];
    var cardRes = [];
    for (var ci = 0; ci < cardProbes.length; ci++) {
      window.CUR_STATE = 'expanded'; window.CUR_TASK = null; window.draw();
      var c0 = q('.card');
      var inner = c0 && c0.querySelector(cardProbes[ci]);
      if (!inner) { cardRes.push(cardProbes[ci] + ':不存在'); continue; }
      clickOn(inner);
      cardRes.push(cardProbes[ci] + (window.CUR_STATE === 'detail' ? ':生效' : ':**没反应**'));
    }
    chk('点击位置/点卡片的每个部位都能进详情',
        cardRes.every(function (s) { return /:生效$/.test(s); }), cardRes.join(' '));

    // 非交互区域点了不能有任何副作用
    window.CUR_STATE = 'expanded'; window.CUR_TASK = null; window.draw();
    await frame();
    clearIPC(); R.nav.length = 0;
    [q('#fstat'), q('#fsign'), q('#app')].forEach(function (el) { if (el) clickOn(el); });
    chk('点击位置/点状态行·署名·空白处没有任何副作用',
        window.CUR_STATE === 'expanded' && ipcCount('open_board') === 0 &&
        ipcCount('hide_to_tray') === 0 && ipcCount('quit') === 0 && R.nav.length === 0,
        'state=' + window.CUR_STATE + ' ipc=' + window.__IPC.map(function (c) { return c.n; }).join(','));

    /* ------------------------------------------------ 文字注入（每个字都要安全） */
    var evil = '"><img src=x onerror=window.__PWNED=1><a href="https://evil.example">点我</a>';
    var keep = window.__BOARD.tasks_by_column.doing.slice();
    window.__BOARD.tasks_by_column.doing = [{
      id: 'x"><img src=x onerror=window.__PWNED=2>',
      title: evil, description: evil, column_id: 'doing', position: 1,
      priority: 0, tags: [evil], fields: {}, created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(), started_at: new Date().toISOString(),
      finished_at: null, idle_sec: 2220
    }];
    window.__BOARD.stats.stalled = 0;
    window.__BOARD.stalled_ids = [];
    window.WB_RELOAD({ _source: 'live', _fetched_at: new Date().toISOString(),
                       board: window.__BOARD, events: {} });
    window.CUR_STATE = 'expanded'; window.draw(); await frame();
    var injectedE = qa('#app img, #app iframe, #app script, #app a[href]');
    chk('注入/列表页不会把任务文字当 HTML 执行', injectedE.length === 0,
        injectedE.map(function (e) { return e.tagName; }).join(','));
    chk('注入/没有触发 onerror 之类的执行', !window.__PWNED, 'PWNED=' + window.__PWNED);
    chk('注入/危险文字按字面显示出来',
        (q('#app').textContent || '').indexOf('<img') >= 0, '');

    window.CUR_STATE = 'detail'; window.CUR_TASK = 'x"><img src=x onerror=window.__PWNED=2>';
    window.draw(); await frame();
    var injectedD = qa('#app img, #app iframe, #app script, #app a[href]');
    chk('注入/详情页同样不会执行任务文字', injectedD.length === 0,
        injectedD.map(function (e) { return e.tagName; }).join(','));
    chk('注入/详情页也没触发执行', !window.__PWNED, 'PWNED=' + window.__PWNED);
    // 复原数据
    window.__BOARD.tasks_by_column.doing = keep;
    window.__BOARD.stats.stalled = 1;
    window.__BOARD.stalled_ids = ['t-doing'];
    window.WB_RELOAD({ _source: 'live', _fetched_at: new Date().toISOString(),
                       board: window.__BOARD, events: window.__EVENTS });
    window.CUR_STATE = 'expanded'; window.CUR_TASK = null; window.draw(); await frame();

    /* ------------------------------------------------ 导航兜底 */
    chk('导航/全程没有任何页面跳转', R.nav.length === 0, R.nav.join(' | '));
    chk('导航/location 未变', location.href === startHref, location.href);
    var anchors = qa('a[href]');
    chk('导航/界面里没有 <a href> 链接', anchors.length === 0,
        anchors.map(function (a) { return a.getAttribute('href'); }).join(','));

    /* ------------------------------------------------ 空 / 读取失败视图 */
    // ★ 这两个视图必须单独查文案 —— 老架构的措辞（"连不上 127.0.0.1"、"看板服务"）
    //   恰恰就藏在这里，而且平时根本不显示，只有专门渲染才看得见。
    //   （变异测试实测：不渲染它们，把文案改回老写法也抓不到。）
    try { window.WB_RELOAD({ _source: 'empty', board: window.__BOARD, events: {} }); } catch (e) {}
    window.CUR_STATE = 'empty';
    try { window.draw(); } catch (e) { chk('空视图/渲染不抛异常', false, String(e)); }
    await frame();
    checkWording('空视图');
    checkLayout('空视图');
    chk('空视图/渲染不抛异常', true, '');

    try {
      window.WB_RELOAD({ _source: 'unreachable', board: window.__BOARD, events: {} });
    } catch (e) {}
    window.CUR_STATE = 'unreachable';
    try { window.draw(); } catch (e) { chk('读不到数据视图/渲染不抛异常', false, String(e)); }
    await frame();
    checkWording('读不到数据视图');
    chk('读不到数据视图/渲染不抛异常', true, '');
    var errBtn2 = q('.err .btn');
    chk('读不到数据视图/重试按钮有绑定回调',
        !!errBtn2 && typeof errBtn2.onclick === 'function',
        errBtn2 ? '存在' : '按钮不存在');

    // 版本戳：界面上必须能答出"我跑的是哪一版"。
    // 这轮出过"改了没生效"的困惑 —— 文件换了但界面上看不出差别，
    // 于是被判断成"桌面未更新"。有这一行，和 dist 的 mtime/体积一对就知道。
    var vb = q('#vbuild');
    chk('版本/设置面板里有版本戳', !!vb, '');
    chk('版本/版本戳有内容（没停在占位符）',
        !!vb && vb.textContent.indexOf('—') < 0 && vb.textContent.trim().length > 0,
        vb ? vb.textContent : '');
    chk('版本/版本戳带时间和体积（能跟 dist 对上）',
        !!vb && /\d\d-\d\d \d\d:\d\d/.test(vb.textContent)
        && /\d+(\.\d+)? MB/.test(vb.textContent),
        vb ? vb.textContent : '');

    checkClassHasStyle();

    R.ipcSeen = window.__IPC.map(function (c) { return c.n; });
    emit(R);
  }

  run().catch(function (e) {
    // ★ 崩了不能把已经跑完的结果丢掉 —— 原来这里只写一条"驱动脚本异常"，
    //   于是"到底是哪一项坏了"完全看不出来（变异测试时实测踩到两次）。
    //   现在把异常**追加**成一条失败项，前面跑过的检查原样保留。
    R.checks.push({
      name: '驱动脚本异常（后面的检查没跑到）',
      ok: false, detail: (e && e.stack) || String(e)
    });
    emit(R);
  });
})();
</script>
"""


# 兜底哨兵：独立于驱动脚本，驱动脚本语法错误时它照样能跑
GUARD = r"""
<script>
setTimeout(function () {
  if (document.getElementById('ft-result')) return;
  var pre = document.createElement('pre');
  pre.id = 'ft-result';
  var bytes = new TextEncoder().encode(JSON.stringify({
    checks: [{ name: '驱动脚本没跑完（语法错误 / 中途卡住）', ok: false,
               detail: '页面已加载但 15 秒内没有产出结果' }],
    nav: []
  }));
  var bin = '';
  for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  pre.textContent = btoa(bin);
  document.body.appendChild(pre);
}, 15000);
</script>
"""


def find_browser() -> str | None:
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def build_page() -> str:
    if not os.path.isfile(SRC):
        raise SystemExit("找不到 %s —— 先跑 `python make_inline.py`" % SRC)
    with open(SRC, encoding="utf-8") as f:
        html = f.read()

    # 桩桥接必须排在所有脚本**之前**，否则宿主 boot 时还没有 window.pywebview
    m = re.search(r"<head[^>]*>", html, re.I)
    if not m:
        raise SystemExit("host.inline.html 里没有 <head>，无法注入桩桥接")
    html = html[:m.end()] + "\n" + STUB + html[m.end():]

    if "</body>" not in html:
        raise SystemExit("host.inline.html 里没有 </body>，无法注入驱动脚本")
    # ★ 兜底哨兵：驱动脚本要是**语法错误**，它的 .catch 也救不了（根本不会执行），
    #   现象是"页面跑完了但没有任何结果" —— 静默失败，最难查。
    #   （这个坑踩了两次：一次 forEach 回调里写 await，一次编码函数用错。）
    #   所以另起一个独立的 <script>：它只需要不依赖驱动脚本就能跑，
    #   超时没看到结果就把"没跑完"报成一条失败项。
    html = html.replace("</body>", DRIVER + GUARD + "\n</body>", 1)

    fd, path = tempfile.mkstemp(prefix="wbfulltest-", suffix=".html")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return path


def main() -> int:
    as_json = "--json" in sys.argv
    browser = find_browser()
    if not browser:
        print("找不到 Edge/Chrome，无法做全功能测试")
        return 2

    page = build_page()
    try:
        out = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             "--window-size=340,900", "--virtual-time-budget=30000",
             "--dump-dom", "file:///" + page.replace("\\", "/")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120).stdout
    finally:
        try:
            os.remove(page)
        except OSError:
            pass

    m = re.search(r'<pre id="ft-result">([^<]*)</pre>', out)
    if not m:
        print("测试没有产出结果 —— 页面可能根本没跑起来")
        print((out or "")[-2000:])
        return 2

    data = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
    checks = data.get("checks", [])
    fails = [c for c in checks if not c["ok"]]

    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("=" * 68)
        print("挂件全功能测试")
        print("=" * 68)
        for c in checks:
            print(("  [通过] " if c["ok"] else "  [失败] ") + c["name"]
                  + (("  ← " + c["detail"]) if (c["detail"] and not c["ok"]) else ""))
        print("-" * 68)
        print("共 %d 项，通过 %d，失败 %d" % (len(checks), len(checks) - len(fails), len(fails)))
        if fails:
            print("\n失败明细：")
            for c in fails:
                print("  · %s：%s" % (c["name"], c["detail"]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
