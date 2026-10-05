"""特征工程：时间分桶、模板计数矩阵、级别与来源分布。

设计说明
--------
异常检测的统计口径需要先回答两个问题：多长时间算一个「窗口」、以及每个
窗口里看什么指标。本模块的做法：

1. 自动分桶：按数据跨度选一个桶宽，使桶数落在 60~400 之间，兼顾分辨率与
   统计显著性（2k 行的样例日志若用 1 秒桶，绝大多数桶是空的）；
2. 模板计数矩阵：行 = 时间桶，列 = 模板 id，值 = 该桶内该模板出现次数；
   后续所有统计检验都在这张稀疏矩阵上做；
3. 级别 / 来源分布：用于规则检测与报告展示；
4. 无时间戳日志的降级方案：按行号构造伪时间轴，并在结果里显式标注
   synthetic_time=True，避免把「按行号分桶」当成真实时间结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .schema import LogRecord, is_error_level, is_warn_level

__all__ = ["FeatureSet", "choose_bucket_seconds", "build_features", "BUCKET_CHOICES"]

#: 候选桶宽（秒）
BUCKET_CHOICES: Tuple[int, ...] = (
    1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400,
)


def choose_bucket_seconds(span_seconds: float, target_buckets: int = 240) -> int:
    """选择桶宽：让桶数尽量接近 target_buckets。"""
    if not np.isfinite(span_seconds) or span_seconds <= 0:
        return 60
    ideal = span_seconds / max(1, target_buckets)
    for choice in BUCKET_CHOICES:
        if choice >= ideal:
            return choice
    return BUCKET_CHOICES[-1]


def humanize_seconds(seconds: float) -> str:
    """把秒数写成人能一眼看懂的形式（整除时用整数，否则保留一位小数）。"""
    s = float(seconds)
    for unit, name in ((86400.0, "d"), (3600.0, "h"), (60.0, "min")):
        if s >= unit:
            if abs(s % unit) < 1e-6:
                return str(int(round(s / unit))) + name
            return "%.1f%s" % (s / unit, name)
    return "%.0fs" % s


@dataclass
class FeatureSet:
    """一次实验的全部统计特征。"""

    frame: pd.DataFrame
    buckets: pd.DatetimeIndex
    template_counts: pd.DataFrame
    level_counts: pd.DataFrame
    source_counts: pd.DataFrame
    bucket_seconds: int
    synthetic_time: bool = False
    span_seconds: float = 0.0
    template_ids: List[int] = field(default_factory=list)

    @property
    def n_buckets(self) -> int:
        return len(self.buckets)

    @property
    def volume(self) -> pd.Series:
        """每桶总日志量。"""
        return self.template_counts.sum(axis=1)

    def level_volume(self, levels: Sequence[str]) -> pd.Series:
        cols = [c for c in levels if c in self.level_counts.columns]
        if not cols:
            return pd.Series(np.zeros(self.n_buckets), index=self.buckets)
        return self.level_counts[cols].sum(axis=1)

    def bucket_label(self, ts) -> str:
        return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def build_features(
    records: Sequence[LogRecord],
    bucket_seconds: Optional[int] = None,
    target_buckets: int = 240,
) -> FeatureSet:
    """把解析后的日志转成统计特征。"""
    if not records:
        empty = pd.DataFrame()
        idx = pd.DatetimeIndex([])
        return FeatureSet(
            frame=empty,
            buckets=idx,
            template_counts=pd.DataFrame(),
            level_counts=pd.DataFrame(),
            source_counts=pd.DataFrame(),
            bucket_seconds=bucket_seconds or 60,
        )

    frame = pd.DataFrame([r.to_row() for r in records])
    frame["ts"] = pd.to_datetime(frame["ts"])
    synthetic = bool(frame["ts"].isna().all())
    if synthetic:
        # 全部没有时间戳：按行号构造伪时间轴（1 行 = 1 秒），并显式标注
        base = datetime(1970, 1, 1)
        frame["ts"] = [base + timedelta(seconds=int(ln)) for ln in frame["line_no"]]
    elif frame["ts"].isna().any():
        frame["ts"] = frame["ts"].ffill().bfill()

    ts_min = frame["ts"].min()
    ts_max = frame["ts"].max()
    span_seconds = float((ts_max - ts_min).total_seconds())
    if bucket_seconds is None:
        bucket_seconds = choose_bucket_seconds(span_seconds, target_buckets)
    bucket_seconds = max(1, int(bucket_seconds))

    freq = str(bucket_seconds) + "s"
    frame["bucket"] = frame["ts"].dt.floor(freq)
    frame["is_error"] = frame["level"].map(lambda x: is_error_level(x))
    frame["is_warn"] = frame["level"].map(lambda x: is_warn_level(x))

    buckets = pd.date_range(frame["bucket"].min(), frame["bucket"].max(), freq=freq)

    def pivot(column: str, fill_value: int = 0) -> pd.DataFrame:
        sub = frame.dropna(subset=[column])
        if sub.empty:
            return pd.DataFrame(index=buckets)
        table = sub.groupby(["bucket", column], observed=True).size().unstack(fill_value=fill_value)
        table = table.reindex(buckets, fill_value=fill_value)
        if column == "template_id":
            table = table.reindex(sorted(table.columns), axis=1)
        return table

    template_counts = pivot("template_id")
    level_counts = pivot("level")
    source_counts = pivot("source")

    template_ids = [int(c) for c in template_counts.columns]

    return FeatureSet(
        frame=frame,
        buckets=buckets,
        template_counts=template_counts,
        level_counts=level_counts,
        source_counts=source_counts,
        bucket_seconds=bucket_seconds,
        synthetic_time=synthetic,
        span_seconds=span_seconds,
        template_ids=template_ids,
    )
