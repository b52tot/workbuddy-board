# -*- coding: utf-8 -*-
"""挂件宿主的状态读写 —— host.py 的 BoardApi.get_state / save_state。

★ 为什么值得单独立一个文件盯这两个方法：
  "设置存住了"这件事在界面上**看不出来** —— 调完当场生效，重启才发现丢了。
  踩过一次：save_state 的白名单里漏了 font，而页面那边
  `api().save_state({font: CFG.font})` 照调不误、还返回 {"ok": True}，
  只是什么也没存 ⇒ 字号"调完即忘"，全程零报错。
  同一个键漏在 get_state 里，则是"存进去了但读不回来"，一样静默。

★ 为什么用 __new__ 绕过 __init__：
  BoardApi.__init__ 会起**会话镜像线程**，那个线程往真实看板数据库写任务。
  单测绝不能碰生产数据，所以这里只借这两个纯状态方法。
"""
import ast
import importlib.util
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_PY = os.path.join(ROOT, "widget", "host.py")
HOST_HTML = os.path.join(ROOT, "widget", "host.html")

# 面板上"改了就该记住"的键。**加面板项时同步加这里**，否则下面几条判据
# 会因为分母变小而永远绿 —— 那正是它们要防的事。
PANEL_KEYS = {"width", "height", "alpha", "theme", "font", "on_top"}


@pytest.fixture(scope="module")
def host_mod():
    spec = importlib.util.spec_from_file_location("wbb_host_under_test", HOST_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pytest.skip("本机没有 pywebview，host.py 顶层会直接 raise SystemExit")
    return mod


@pytest.fixture()
def api(host_mod, tmp_path, monkeypatch):
    # 状态文件挪到 tmp：绝不碰开发态/生产态的 state.json
    monkeypatch.setattr(host_mod, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(host_mod, "log", lambda *a, **k: None)
    a = host_mod.BoardApi.__new__(host_mod.BoardApi)
    a.cfg = dict(host_mod.DEFAULTS)
    return a


@pytest.mark.parametrize("key,val", [
    ("font", 16), ("width", 400), ("height", 600),
    ("alpha", 0.6), ("theme", "theme-ink"), ("on_top", False),
])
def test_面板上的每一项都存得进也读得回(api, key, val):
    """★ 正向判据：save 进去什么，get_state 就得原样吐回来。"""
    r = api.save_state({key: val})
    assert r.get("ok") is True, "save_state 没返回 ok：%r" % (r,)
    assert api.get_state().get(key) == val, (
        "%s 存进去是 %r，读回来是 %r —— 白名单或 get_state 漏了这个键"
        % (key, val, api.get_state().get(key)))


def test_后续的保存不会把之前的键挤掉(api):
    """面板是逐项保存的：改字号发一次、改主题再发一次。
    第二次的 patch 里没有 font，但 font 必须还在。"""
    api.save_state({"font": 15})
    api.save_state({"theme": "theme-ex"})
    st = api.get_state()
    assert st["font"] == 15, "后一次 save_state 把 font 冲掉了"
    assert st["theme"] == "theme-ex"


def test_保存要真的落盘而不只是改内存(api, host_mod):
    """★ 这一条盯的是"进程重启还能不能读回来"。
    只改 self.cfg 不落盘，界面上一模一样，重启才发现白调。"""
    import json
    api.save_state({"font": 17})
    with open(host_mod.STATE_PATH, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk.get("font") == 17, "save_state 没有把 font 落盘，重启必丢"


def _save_state_whitelist():
    """从 AST 里取 save_state 的键白名单（不执行任何代码）。"""
    tree = ast.parse(open(HOST_PY, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "save_state":
            for sub in ast.walk(node):
                if isinstance(sub, ast.For) and isinstance(sub.iter, ast.Tuple):
                    return {e.value for e in sub.iter.elts if isinstance(e, ast.Constant)}
    return set()


def _get_state_keys():
    """从 AST 里取 get_state 返回的那个 dict 的键。"""
    tree = ast.parse(open(HOST_PY, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_state":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    return {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
    return set()


def test_保存白名单必须覆盖全部面板键():
    """★ 白名单漏一个键 = 那一项调完即忘，而且不报错。失败时会直接点名。"""
    wl = _save_state_whitelist()
    assert wl, "没能从 AST 里解析出 save_state 的白名单（判据自己失效了）"
    missing = PANEL_KEYS - wl
    assert not missing, "save_state 白名单漏了 %s —— 这些设置将无法持久化" % sorted(missing)


def test_get_state_必须回传全部面板键():
    """★ get_state 漏键 = 存是存住了，下次启动读不回来，同样静默。"""
    keys = _get_state_keys()
    assert keys, "没能从 AST 里解析出 get_state 的返回键（判据自己失效了）"
    missing = PANEL_KEYS - keys
    assert not missing, "get_state 没有回传 %s —— 这些设置重启后会丢" % sorted(missing)


def test_默认配置覆盖全部面板键(host_mod):
    """DEFAULTS 缺键 ⇒ 首次启动（没有 state.json）时该项没有基线值。"""
    missing = PANEL_KEYS - set(host_mod.DEFAULTS)
    assert not missing, "DEFAULTS 缺 %s" % sorted(missing)


def test_默认主题必须是面板上真实存在的按钮(host_mod):
    """theme-a/b/c 是「布局」时代的旧名，面板上早已没有这三个按钮。
    默认值落在不存在的类上 ⇒ 样式全丢，而且不报错。
    （host.html 里还有一张 OLD_THEME 迁移表兜着，但那是给老用户升级用的，
      不该靠它来兜默认值。）"""
    html = open(HOST_HTML, encoding="utf-8").read()
    valid = set(re.findall(r'data-t="(theme-[a-z]+)"', html))
    assert len(valid) >= 4, \
        "host.html 里只解析到 %d 个主题按钮，判据本身失效了：%s" % (len(valid), sorted(valid))
    assert host_mod.DEFAULTS["theme"] in valid, (
        "DEFAULTS['theme']=%r 不在面板主题按钮 %s 里"
        % (host_mod.DEFAULTS["theme"], sorted(valid)))
