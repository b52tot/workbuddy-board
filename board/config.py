"""配置加载与校验。

设计要点
--------
* 全部配置集中在单个 JSON 文件；环境变量 `WBB_CONFIG` 可覆盖路径。
* **任何业务语义都不写死在代码里** —— 列、流转、自定义字段全部来自配置。
* 配置文件缺失时用内置默认值，保证「克隆下来就能跑」。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENV_CONFIG = "WBB_CONFIG"

# 项目根目录（board/ 的上一级）。作为「找不到配置文件」时的兜底锚点，
# ★ 存在的理由：MCP server 由宿主进程拉起，**CWD 不可控**。
#   若只认 CWD 下的 ./config.json，宿主换个工作目录启动就会：
#   ① 读不到配置 ⇒ 静默退回内置默认列（看着像"我的列定义丢了"）
#   ② db_path 的相对路径解析到别处 ⇒ 数据写进另一个目录（看着像"数据丢了"）
#   两者都不会报错。用「离代码最近的确定位置」当锚点，才不依赖 CWD。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"id": "todo", "title": "待办", "color": "gray", "wip_limit": None, "is_terminal": False},
    {"id": "doing", "title": "进行中", "color": "blue", "wip_limit": 5, "is_terminal": False},
    {"id": "blocked", "title": "阻塞", "color": "red", "wip_limit": None, "is_terminal": False},
    {"id": "done", "title": "已完成", "color": "green", "wip_limit": None, "is_terminal": True},
]

DEFAULT_TRANSITIONS: dict[str, list[str]] = {
    "todo": ["doing", "blocked", "done"],
    "doing": ["todo", "blocked", "done"],
    "blocked": ["todo", "doing", "done"],
    "done": ["todo"],
}


class ConfigError(Exception):
    """配置非法。抛出时请把「哪个键、期望什么、实际什么」讲清楚。"""


def _copy_columns() -> list[dict[str, Any]]:
    """★ 必须深拷贝每一层。

    `list(DEFAULT_COLUMNS)` 只复制了外层列表，内层 dict 仍是**共享引用**。
    调用方（或测试）一旦改了 `cfg.board.columns[0]["is_terminal"]`，就会污染
    模块级常量，导致后续所有 Config 实例都带着脏数据。这个坑踩过一次。
    """
    return [dict(c) for c in DEFAULT_COLUMNS]


def _copy_transitions() -> dict[str, list[str]]:
    return {k: list(v) for k, v in DEFAULT_TRANSITIONS.items()}


@dataclass
class BoardConfig:
    db_path: str = "./data/board.db"
    title: str = "WorkBuddy Board"
    columns: list[dict[str, Any]] = field(default_factory=_copy_columns)
    transitions: dict[str, list[str]] = field(default_factory=_copy_transitions)
    fields: list[dict[str, Any]] = field(default_factory=list)
    enforce_wip: bool = True
    # 与看板展示相关的默认值
    refresh_ms: int = 1000
    hide_empty_columns: bool = False
    # 「停滞」判定阈值（秒）：非终态列的任务，超过这么久没有任何事件 ⇒ 标记疑似停滞。
    #
    # ★ 默认值从 90 秒改成了 900 秒（15 分钟）。原因是 90 秒根本不是"卡住"，
    #   它是"心跳间隔"——而 agent 干一件真实的事（跑一次下载、调一次模型）
    #   动辄几分钟才写一次看板。于是**几乎每个进行中的任务都会被标成卡住**：
    #   报警疲劳一旦形成，真正的停滞反而被淹没，等于把这项能力废掉了。
    stall_seconds: float = 900.0
    # 终态列的保留期（天）。归档 = 打标记（archived_at），**数据一条都不删**。
    #
    # ★ 语义（三者要分清）：
    #     > 0  → 只归档「完成超过 N 天」的
    #     = 0  → **归档当时已完成的全部**（默认值）
    #     < 0  → 关闭归档
    #   默认取 0 是因为"已完成"列本来就是"刚做完的事"的展示位；
    #   留着不放，它会一直长，最后把进行中的任务挤出可视区。
    archive_done_after_days: float = 0.0
    # 挂件**启动时**跑一次归档。
    # ★ 这是唯一的触发点（见下面的 archive_during_run）：重启即清理，时机可控、可观察。
    archive_on_startup: bool = True
    # 运行**期间**是否也定期归档。
    # ★ 默认 False：原来这里是"每 60 分钟静默跑一次"，用户既看不见、又不知道
    #   它到底跑没跑（实测就因此被质疑"依旧没有归档"）。改成默认关闭，
    #   只在重启时做一次 —— 一次看得见的清理，胜过十次看不见的巡检。
    archive_during_run: bool = False
    # 运行期归档的巡检间隔（分钟）。只有 archive_during_run=True 时才有意义。
    archive_check_minutes: float = 60.0

    # ---------------------------------------------------------------- 自动镜像
    # 挂件直接读 WorkBuddy 落盘的会话记录，把"用户刚提了个请求"自动变成一条
    # 「进行中」任务，把"这一轮做完了"自动转「已完成」。
    #
    # ★ 为什么要这个：看板只会知道**被写进去的东西**，而"agent 会不会记得写"
    #   实测靠不住（用户反复反馈「进行中一直为空」，每次根因都是没人写）。
    #   写进 AGENTS.md、写进 memory 都仍依赖自觉 ⇒ 换成直接读事实来源。
    #
    # ★ 只读，绝不写 WorkBuddy 的任何文件。关掉就完全不动。
    watch_workbuddy: bool = True
    # 会话记录根目录。留空 = `~/.workbuddy/projects`
    watch_projects_dir: str = ""
    # 文件安静够久 + 没有未完成调用 ⇒ 判定这一轮结束（秒）。
    # 取 120 是因为：agent 干完在等用户时记录不再增长；而跑长任务时
    # 记录里会有未配对的 function_call，不会被误判成结束。
    watch_idle_close_sec: float = 120.0
    # 巡检间隔（秒）。会话记录是本地小文件，5 秒一次足够，也不会有可观开销。
    watch_poll_sec: float = 5.0


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8791
    open_browser: bool = False
    # 网页端是否允许写操作。默认 False：所有写入都走 MCP 工具，
    # 保证「谁改的」永远有据可查。置 true 后网页拖拽也会走同一个 Store。
    allow_write: bool = False


@dataclass
class Config:
    board: BoardConfig = field(default_factory=BoardConfig)
    web: WebConfig = field(default_factory=WebConfig)
    source: str | None = None  # 实际读到的配置文件路径，便于排查
    base_dir: str | None = None  # 相对路径（db_path 等）的解析基准

    def resolve_path(self, p: str) -> str:
        """把配置里的路径解析成绝对路径。

        ★ 为什么必须相对 base_dir 而不是 CWD：
        配置里的 `./data/board.db` 是**相对配置文件**说的，不是相对"谁碰巧把
        进程启在哪"。MCP server 被宿主拉起时 CWD 由宿主决定，用 CWD 解析会让
        数据库悄悄建到别的地方 —— 服务照常起、工具照常调，但看板是空的。
        这类失效不报错，只会让人以为"数据丢了"。

        绝对路径原样返回（配 `WBB_CONFIG` 指向外部配置时常用）。
        """
        pth = Path(p)
        if pth.is_absolute():
            return str(pth)
        base = Path(self.base_dir) if self.base_dir else PROJECT_ROOT
        return str((base / pth).resolve())

    # ------------------------------------------------------------- 校验
    def validate(self) -> None:
        cols = self.board.columns
        if not cols:
            raise ConfigError("board.columns 不能为空")

        ids = [c.get("id") for c in cols]
        if any(not i for i in ids):
            raise ConfigError("board.columns 每项都必须有非空的 id")
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ConfigError("board.columns 存在重复 id: %s" % sorted(dup))

        terminal = [c["id"] for c in cols if c.get("is_terminal")]
        if not terminal:
            raise ConfigError("board.columns 至少要有 1 个 is_terminal=true 的终态列，"
                              "否则任务永远无法判定完成")

        unknown = set(self.board.transitions) - set(ids)
        if unknown:
            raise ConfigError("board.transitions 引用了未定义的列: %s" % sorted(unknown))
        for src, dsts in self.board.transitions.items():
            bad = set(dsts) - set(ids)
            if bad:
                raise ConfigError("board.transitions['%s'] 指向未定义的列: %s"
                                  % (src, sorted(bad)))

        for f in self.board.fields:
            if not f.get("key"):
                raise ConfigError("board.fields 每项都必须有 key")
            if f.get("type", "text") not in {"text", "number", "enum", "bool", "progress"}:
                raise ConfigError("board.fields['%s'] 的 type 只支持 "
                                  "text/number/enum/bool/progress" % f["key"])
            if f.get("type") == "progress" and not f.get("label"):
                # 进度字段一定会渲染成进度条，没有 label 就只看到一根光秃秃的条，
                # 用户不知道它代表什么。这是刻意的：让它当场报错，而不是上线后才发现。
                raise ConfigError("board.fields['%s'] 是 progress 类型，必须有 label "
                                  "（显示在进度条旁边）" % f["key"])

        if not (1 <= int(self.web.port) <= 65535):
            raise ConfigError("web.port 必须在 1-65535，实际 %r" % self.web.port)

    # ------------------------------------------------------------- 便捷查询
    def column_ids(self) -> list[str]:
        return [c["id"] for c in self.board.columns]

    def terminal_ids(self) -> set[str]:
        return {c["id"] for c in self.board.columns if c.get("is_terminal")}

    def column_by_id(self, cid: str) -> dict[str, Any] | None:
        for c in self.board.columns:
            if c["id"] == cid:
                return c
        return None

    def can_move(self, src: str, dst: str) -> bool:
        return dst in self.board.transitions.get(src, [])


def _is_comment_key(key: str) -> bool:
    """`_` 开头的键视为注释。

    为什么需要它：用户希望在 JSON 里写注释解释每一项，比如
    `"_columns_comment": "id 是稳定标识..."`。若把这类键当未知键报错，
    示例配置会直接启动失败 —— 而「看示例照着改」正是最常见的用法。

    约定：`_` 开头 ⇒ 注释，忽略；其余未知键 ⇒ 报错（防止配了不生效）。
    这样既支持注释，又能抓住真正的拼写错误。
    """
    return key.startswith("_")


def _merge_dataclass(obj: Any, data: dict[str, Any]) -> Any:
    """把 dict 里认识的键覆盖到 dataclass 上；未知键直接报错，避免「配了不生效」。

    ★ 注意 deepcopy：配置里的 columns/transitions 是嵌套结构，直接赋值会让
    cfg 与调用方传入的 dict 共享内层对象，一方修改另一方跟着变。
    """
    import copy
    valid = set(obj.__dataclass_fields__.keys())
    for k, v in data.items():
        if _is_comment_key(k):
            continue
        if k not in valid:
            raise ConfigError("%s 不是合法的配置项（可用: %s。"
                              "以 _ 开头的键会被当作注释忽略）"
                              % (k, ", ".join(sorted(valid))))
        setattr(obj, k, copy.deepcopy(v) if isinstance(v, (dict, list)) else v)
    return obj


def load_config(path: str | None = None) -> Config:
    """读取配置。

    路径优先级：显式参数 > 环境变量 `WBB_CONFIG` > 项目根/config.json > ./config.json

    ★ 为什么把「项目根/config.json」排在 CWD 之前：CWD 不可控（见 PROJECT_ROOT
    注释）。项目根是随代码走的确定位置，优先信它。显式参数与 env 仍可覆盖，
    所以「用外部配置」这条路径不受影响。
    """
    cfg = Config()
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    if os.environ.get(ENV_CONFIG):
        candidates.append(Path(os.environ[ENV_CONFIG]))
    candidates.append(PROJECT_ROOT / "config.json")
    candidates.append(Path("config.json"))

    for p in candidates:
        if p.is_file():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise ConfigError("配置文件 %s 不是合法 JSON: %s" % (p, e)) from e
            if not isinstance(raw, dict):
                raise ConfigError("配置文件 %s 顶层必须是对象" % p)
            if "board" in raw:
                _merge_dataclass(cfg.board, raw["board"])
            if "web" in raw:
                _merge_dataclass(cfg.web, raw["web"])
            unknown = {k for k in raw if k not in ("board", "web")
                       and not _is_comment_key(k)}
            if unknown:
                raise ConfigError("配置文件顶层只支持 board / web，发现: %s"
                                  % sorted(unknown))
            cfg.source = str(p)
            # 相对路径以「配置文件所在目录」为基准 —— 这样不管谁把进程
            # 启在哪个目录，./data/board.db 都指向同一个地方。
            cfg.base_dir = str(p.resolve().parent)
            break

    if cfg.base_dir is None:
        # 一个配置文件都没读到 ⇒ 用内置默认值，基准锚在项目根。
        cfg.base_dir = str(PROJECT_ROOT)

    cfg.validate()
    return cfg
