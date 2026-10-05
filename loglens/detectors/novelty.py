"""新模板检测器：第一次出现的日志形态本身就是异常信号。

运维经验里最贵的一类故障是「从没见过的错误」——基于历史频率的统计方法
（z-score、泊松）对它天然失明，因为它没有历史基线，中位数是 0、方差是 0。
本检测器专门补这个盲区：

1. 跳过观察期（默认前 10% 时间桶）用于建立基线；
2. 观察期之后首次出现的模板判定为「新模板」；
3. 若该模板的主要级别是 ERROR/FATAL，权重加倍（可直接触发异常窗口），
   否则作为低权重证据，需与其它检测器相互印证才成窗。
"""

from __future__ import annotations

from typing import List

import pandas as pd

from ..schema import ERROR_LEVELS
from .base import Anomaly, DetectContext, collect_evidence, severity_from_score

__all__ = ["NoveltyDetector"]


class NoveltyDetector:
    """观察期之后首次出现的模板。"""

    name = "novelty"

    def detect(self, ctx: DetectContext) -> List[Anomaly]:
        fs = ctx.features
        cfg = ctx.config
        out: List[Anomaly] = []
        if fs.n_buckets == 0:
            return out
        warmup = max(1, int(fs.n_buckets * cfg.novelty_warmup_frac))
        freq = str(max(1, fs.bucket_seconds)) + "s"
        buckets = fs.buckets

        for tmpl in ctx.templates:
            if tmpl.first_seen is None or tmpl.count <= 0:
                continue
            first_bucket = pd.Timestamp(tmpl.first_seen).floor(freq)
            idx = int(buckets.searchsorted(first_bucket))
            if idx < warmup or idx >= fs.n_buckets:
                continue
            is_error = tmpl.main_level in ERROR_LEVELS
            score = cfg.novelty_error_score if is_error else cfg.novelty_score
            bucket_ts = pd.Timestamp(buckets[idx])
            out.append(
                Anomaly(
                    detector=self.name,
                    kind="new-template",
                    # 严重度与融合权重解耦：从没出现过的「错误级」模板直接判 high，
                    # 而普通新模板只给低权重，靠其它检测器印证
                    severity="high" if is_error else severity_from_score(score),
                    score=score,
                    bucket=bucket_ts,
                    subject="T" + str(tmpl.template_id),
                    template_id=tmpl.template_id,
                    observed=float(tmpl.count),
                    expected=0.0,
                    detail=(
                        "模板 T" + str(tmpl.template_id) + " 首次出现在观察期之后（第 " + str(idx + 1)
                        + "/" + str(fs.n_buckets) + " 个时间桶），全文共 " + str(tmpl.count) + " 次，"
                        + "主要级别 " + str(tmpl.main_level) + "；模板形态："
                        + (tmpl.pattern[:120] + ("..." if len(tmpl.pattern) > 120 else ""))
                    ),
                    evidence=collect_evidence(
                        fs, bucket=bucket_ts, template_id=tmpl.template_id, limit=cfg.evidence_per_anomaly
                    ),
                )
            )
        out.sort(key=lambda a: -a.score)
        return out[: cfg.max_anomalies]
