"""稳健 z-score 检测器：按模板找「计数突增」的时间桶。

思想
----
同一条模板（同一类事件）在正常情况下每分钟出现次数服从某种稳定分布。
用中位数与 MAD 估计「正常水位」与「波动幅度」，就不用假设正态分布，
也不容易被历史里的极端值污染（这正是均值/标准差做不到的）。

    sigma = 1.4826 * MAD          （MAD -> 正态标准差的一致估计）
    z     = (x - median) / sigma

当 MAD = 0（历史上该模板计数几乎不变）时退化为泊松尺度 sigma = sqrt(median)，
避免除零放大噪声。
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from .base import Anomaly, DetectContext, collect_evidence, severity_from_score

__all__ = ["RobustZDetector"]


class RobustZDetector:
    """基于 MAD 的模板计数突增检测。"""

    name = "robust_z"

    def detect(self, ctx: DetectContext) -> List[Anomaly]:
        fs = ctx.features
        cfg = ctx.config
        counts = fs.template_counts
        out: List[Anomaly] = []
        if counts is None or counts.empty or fs.n_buckets == 0:
            return out

        matrix = counts.to_numpy(dtype=float)
        totals = matrix.sum(axis=0)
        keep = totals >= cfg.min_template_total
        if not keep.any():
            return out
        matrix = matrix[:, keep]
        cols = [int(c) for c, k in zip(counts.columns, keep) if k]

        median = np.median(matrix, axis=0)
        mad = np.median(np.abs(matrix - median), axis=0)
        sigma = 1.4826 * mad
        poisson_sigma = np.sqrt(np.maximum(median, 0.0))
        sigma = np.where(sigma > 1e-9, sigma, np.maximum(poisson_sigma, 1.0))

        z = (matrix - median) / sigma
        thresh_count = np.maximum(cfg.min_observed, median + cfg.z_extra_over_median)
        hit_rows, hit_cols = np.where((z >= cfg.z_threshold) & (matrix >= thresh_count))

        buckets = fs.buckets
        for r, c in zip(hit_rows.tolist(), hit_cols.tolist()):
            tid = cols[c]
            bucket_ts = pd.Timestamp(buckets[r])
            observed = float(matrix[r, c])
            score = float(min(5.0, 1.0 + (z[r, c] - cfg.z_threshold) / 10.0))
            out.append(
                Anomaly(
                    detector=self.name,
                    kind="template-burst",
                    severity=severity_from_score(score),
                    score=score,
                    bucket=bucket_ts,
                    subject="T" + str(tid),
                    template_id=tid,
                    detail=(
                        "模板 T" + str(tid) + " 在本桶出现 " + str(int(observed)) + " 次，历史中位数 "
                        + str(round(float(median[c]), 2)) + " 次，稳健 z = " + str(round(float(z[r, c]), 2))
                        + "（阈值 " + str(cfg.z_threshold) + "，sigma=" + str(round(float(sigma[c]), 2)) + "）"
                    ),
                    observed=observed,
                    expected=float(median[c]),
                    evidence=collect_evidence(
                        fs, bucket=bucket_ts, template_id=tid, limit=cfg.evidence_per_anomaly
                    ),
                )
            )
        out.sort(key=lambda a: -a.score)
        return out[: cfg.max_anomalies]
