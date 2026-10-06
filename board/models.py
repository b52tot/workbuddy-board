"""数据模型与状态机校验。

这里**不含任何业务词汇**。所有语义（哪些列、怎么流转、有哪些自定义字段）
都由 Config 注入，因此同一份代码可用于任意场景。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import Config


def now_iso() -> str:
    """统一用 UTC + 显式时区，避免跨机器/跨时区比较出错。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class StateError(Exception):
    """状态流转非法。"""


@dataclass
class Task:
    id: str
    title: str
    column_id: str
    position: float = 0.0
    description: str | None = None
    priority: int = 0
    tags: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None

    # ---------------------------------------------------------- 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "column_id": self.column_id,
            "position": self.position,
            "priority": self.priority,
            "tags": list(self.tags),
            "fields": dict(self.fields),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> "Task":
        """从 sqlite3.Row 构造。tags / fields 是 JSON 文本，需还原。"""
        def _load(txt: Any, fallback: Any) -> Any:
            if not txt:
                return fallback
            try:
                return json.loads(txt)
            except json.JSONDecodeError:
                # 脏数据不该让整块看板打不开：退化为空值并保留原文
                return fallback

        return cls(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            column_id=row["column_id"],
            position=row["position"],
            priority=row["priority"],
            tags=_load(row["tags"], []),
            fields=_load(row["fields"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


def validate_task_payload(cfg: Config, *, title: str,
                          fields_in: dict[str, Any] | None = None,
                          column_id: str | None = None) -> None:
    """在写入前校验输入。失败要能一句话讲清「哪个字段、期望、实际」。"""
    if not title or not str(title).strip():
        raise ValueError("title 不能为空")
    if len(str(title)) > 500:
        raise ValueError("title 过长（%d > 500）" % len(str(title)))
    if column_id is not None and cfg.column_by_id(column_id) is None:
        raise ValueError("未知的 column_id: %r（可用: %s）"
                         % (column_id, ", ".join(cfg.column_ids())))

    fields_in = fields_in or {}
    spec = {f["key"]: f for f in cfg.board.fields}
    unknown = set(fields_in) - set(spec)
    if unknown:
        raise ValueError("fields 含未定义的键: %s（已在 config.board.fields 中定义: %s）"
                         % (sorted(unknown), sorted(spec) or "无"))

    for key, val in fields_in.items():
        want = spec[key].get("type", "text")
        if want == "number" and not isinstance(val, (int, float)):
            raise ValueError("fields['%s'] 期望 number，实际 %r" % (key, val))
        if want == "bool" and not isinstance(val, bool):
            raise ValueError("fields['%s'] 期望 bool，实际 %r" % (key, val))
        if want == "enum":
            allowed = spec[key].get("values") or []
            if val not in allowed:
                raise ValueError("fields['%s'] 必须是 %s 之一，实际 %r"
                                 % (key, allowed, val))
        if want == "text" and not isinstance(val, str):
            raise ValueError("fields['%s'] 期望 text，实际 %r" % (key, val))
        if want == "progress" and not isinstance(val, dict):
            raise ValueError("fields['%s'] 期望 progress 对象（如 "
                             '{"current": 3, "total": 10}），实际 %r' % (key, val))


def read_progress(field_spec: dict[str, Any], raw: Any) -> dict[str, Any] | None:
    """把 progress 字段的原始值规整成统一结构。

    为什么单独抽一个函数：进度既可能来自 MCP 的 update_progress，也可能由调用方
    在 create_task/update_task 的 fields 里直接塞 —— 两条路径必须得到**完全一致**
    的解释，否则看板会因为入口不同而显示不同结果。

    返回 None 表示「这个字段里没有可展示的进度」；**不抛异常**，因为这是读路径，
    脏数据不该让整块看板打不开（与 Task.from_row 的容错策略一致）。
    """
    if field_spec.get("type") != "progress":
        return None
    if not isinstance(raw, dict):
        return None

    def _num(x: Any) -> float | None:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        if x != x or x in (float("inf"), float("-inf")):  # NaN / Inf
            return None
        return float(x)

    cur, tot = _num(raw.get("current")), _num(raw.get("total"))
    if cur is None or tot is None or tot <= 0:
        return None

    pct = round(max(0.0, min(100.0, cur / tot * 100.0)), 1)
    out: dict[str, Any] = {"current": cur, "total": tot, "pct": pct}
    msg = raw.get("message")
    if isinstance(msg, str) and msg.strip():
        out["message"] = msg.strip()
    weight = _num(raw.get("weight_bytes"))
    if weight is not None and weight > 0:
        out["weight_bytes"] = weight
    if raw.get("done") is True or cur >= tot:
        out["done"] = True
    return out


def progress_fields(cfg: Config) -> list[dict[str, Any]]:
    """配置里所有 type=progress 的字段定义。"""
    return [f for f in cfg.board.fields if f.get("type") == "progress"]


def progress_of(cfg: Config, fields: dict[str, Any]) -> dict[str, Any] | None:
    """从任务的自定义字段里取出主进度（第一个声明为 progress 的字段）。

    「主进度」用于看板顶部的聚合进度条与停滞判据；其余 progress 字段只在卡片上各自展示。
    """
    for spec in progress_fields(cfg):
        got = read_progress(spec, (fields or {}).get(spec["key"]))
        if got is not None:
            return got
    return None


def validate_move(cfg: Config, src_col: str, dst_col: str,
                  current_count_in_dst: int | None = None) -> None:
    """校验一次状态流转是否被允许；顺带检查 WIP 上限。"""
    if cfg.column_by_id(dst_col) is None:
        raise StateError("目标列不存在: %r" % dst_col)
    if src_col == dst_col:
        return  # 同列内重排是合法的
    if not cfg.can_move(src_col, dst_col):
        allowed = cfg.board.transitions.get(src_col, [])
        raise StateError("不允许从 %r 流转到 %r（%r 允许的目标: %s）"
                         % (src_col, dst_col, src_col, allowed or "无"))

    dst = cfg.column_by_id(dst_col) or {}
    limit = dst.get("wip_limit")
    if (cfg.board.enforce_wip and limit is not None
            and not dst.get("is_terminal")
            and current_count_in_dst is not None
            and current_count_in_dst >= limit):
        raise StateError("列 %r 已达在制品上限 %d，拒绝迁入" % (dst_col, limit))
