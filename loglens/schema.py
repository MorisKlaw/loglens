"""数据模型：解析后的日志记录与日志级别定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

#: 级别 -> 严重度数值（用于比较与聚合；数值越大越严重）
LEVEL_RANK: Dict[str, int] = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "NOTICE": 25,
    "CONFIG": 25,
    "WARN": 30,
    "WARNING": 30,
    "ERROR": 40,
    "ERR": 40,
    "SEVERE": 40,
    "CRIT": 50,
    "CRITICAL": 50,
    "FATAL": 50,
    "ALERT": 60,
    "PANIC": 60,
    "EMERG": 70,
}

#: 错误级（ERROR 及以上）
ERROR_LEVELS = frozenset(
    {"ERROR", "ERR", "SEVERE", "CRIT", "CRITICAL", "FATAL", "ALERT", "EMERG", "PANIC"}
)
#: 告警级
WARN_LEVELS = frozenset({"WARN", "WARNING"})

#: 未解析出级别时，用关键词兜底推断级别（按顺序匹配）
LEVEL_HINTS = (
    (("panic", "emerg"), "PANIC"),
    (("fatal", "critical"), "FATAL"),
    (("exception", "traceback", "error", "failed", "failure", "refused", "timeout"), "ERROR"),
    (("warn",), "WARN"),
    (("debug",), "DEBUG"),
    (("info",), "INFO"),
)


def normalize_level(level: Optional[str]) -> Optional[str]:
    """统一级别写法（WARNING -> WARN，小写 -> 大写）。"""
    if not level:
        return None
    up = str(level).strip().upper()
    if up == "WARNING":
        return "WARN"
    if up == "ERR":
        return "ERROR"
    if up == "CRITICAL":
        return "FATAL"
    return up


def level_rank(level: Optional[str]) -> int:
    """级别的严重度数值；未知级别返回 0。"""
    if not level:
        return 0
    return LEVEL_RANK.get(str(level).strip().upper(), 0)


def is_error_level(level: Optional[str]) -> bool:
    if not level:
        return False
    return str(level).strip().upper() in ERROR_LEVELS


def is_warn_level(level: Optional[str]) -> bool:
    if not level:
        return False
    return str(level).strip().upper() in WARN_LEVELS


def infer_level(text: str) -> Optional[str]:
    """没有显式级别字段时，从文本里猜级别（只用于兜底日志行）。"""
    low = text.lower()
    for keys, level in LEVEL_HINTS:
        for k in keys:
            if k in low:
                return level
    return None


@dataclass
class LogRecord:
    """一条解析后的日志。

    ts 为 None 表示该行没有可解析的时间戳（例如堆栈续行）；下游会在开启
    forward_fill_ts 时沿用上一条的时间。
    """

    line_no: int
    raw: str
    message: str
    ts: Optional[datetime] = None
    level: Optional[str] = None
    source: Optional[str] = None
    pid: Optional[int] = None
    host: Optional[str] = None
    parser: str = "generic"
    template_id: Optional[int] = None
    template: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def rank(self) -> int:
        return level_rank(self.level)

    def to_row(self) -> Dict[str, Any]:
        """转换为扁平行（用于写出 CSV / 构造 DataFrame）。"""
        row: Dict[str, Any] = {
            "line_no": self.line_no,
            "raw": self.raw,
            "ts": self.ts,
            "level": self.level,
            "rank": self.rank,
            "source": self.source,
            "pid": self.pid,
            "host": self.host,
            "parser": self.parser,
            "template_id": self.template_id,
            "template": self.template,
            "message": self.message,
        }
        row.update({("extra_" + k): v for k, v in self.extra.items()})
        return row
