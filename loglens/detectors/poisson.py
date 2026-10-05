"""泊松尾检验检测器：用「稀有事件概率」判断突增是否显著。

与稳健 z-score 的区别
--------------------
z-score 回答「偏离正常水位多少个标准差」，泊松检验回答「在历史平均速率下，
出现这么多条的概率有多大」。后者对低频模板更合适，而且能给出 p 值，
让我们可以对成百上千次检验做多重比较校正（Benjamini-Hochberg FDR），
把「测得多、必然蒙对几个」的假阳性压下去。

实现细节：用对数空间累加泊松 PMF，避免 scipy 依赖与 exp(-lambda) 下溢。
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

from .base import Anomaly, DetectContext, collect_evidence, severity_from_score

__all__ = ["PoissonDetector", "poisson_sf", "benjamini_hochberg"]


def _log_pmf(k: int, lam: float) -> float:
    """log P(X = k), X ~ Poisson(lam)。"""
    if lam <= 0:
        return 0.0 if k == 0 else -math.inf
    return -lam + k * math.log(lam) - math.lgamma(k + 1.0)


def _logsumexp(values: Sequence[float]) -> float:
    vals = [v for v in values if v > -math.inf]
    if not vals:
        return -math.inf
    m = max(vals)
    return m + math.log(sum(math.exp(v - m) for v in vals))


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k)，X ~ Poisson(lam)：上尾概率（越小越异常）。"""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    logs = [_log_pmf(i, lam) for i in range(k)]
    log_cdf = _logsumexp(logs)
    if log_cdf == -math.inf:
        return 1.0
    cdf = math.exp(log_cdf)
    return max(0.0, min(1.0, 1.0 - cdf))


def benjamini_hochberg(p_values: Sequence[float], q: float = 0.05) -> List[bool]:
    """Benjamini-Hochberg FDR 控制，返回每个 p 值是否被拒绝。"""
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p_values[i])
    rejected = [False] * n
    kmax = -1
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= q * rank / n:
            kmax = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= kmax:
            rejected[idx] = True
    return rejected


class PoissonDetector:
    """低频/中频模板的计数显著性检验（带 FDR 校正）。"""

    name = "poisson"

    def detect(self, ctx: DetectContext) -> List[Anomaly]:
        fs = ctx.features
        cfg = ctx.config
        counts = fs.template_counts
        out: List[Anomaly] = []
        if counts is None or counts.empty or fs.n_buckets < 3:
            return out

        matrix = counts.to_numpy(dtype=float)
        n_buckets = matrix.shape[0]
        totals = matrix.sum(axis=0)

        # 只对稀疏的非零格子做检验（零格子不可能「突增」）
        rows, cols, pvals, rates = [], [], [], []
        for c in range(matrix.shape[1]):
            total = totals[c]
            if total < cfg.min_template_total:
                continue
            for r in range(n_buckets):
                x = matrix[r, c]
                if x < cfg.min_observed:
                    continue
                lam = (total - x) / max(1, n_buckets - 1)
                if lam < cfg.poisson_min_lambda:
                    continue
                rows.append(r)
                cols.append(c)
                rates.append(lam)
                pvals.append(poisson_sf(int(x), lam))
        if not pvals:
            return out

        # BH 的家族大小必须是全部检验数：先对整族做 FDR 校正，
        # 再用 poisson_alpha 做第二道门限（比只对子集校正更保守、更规范）
        rejected = benjamini_hochberg(pvals, cfg.fdr_q)
        family_size = len(pvals)

        buckets = fs.buckets
        for idx, keep in enumerate(rejected):
            if not keep or pvals[idx] > cfg.poisson_alpha:
                continue
            r, c, lam, p = rows[idx], cols[idx], rates[idx], pvals[idx]
            tid = int(counts.columns[c])
            bucket_ts = pd.Timestamp(buckets[r])
            observed = float(matrix[r, c])
            ratio = observed / lam if lam > 0 else float("inf")
            score = float(min(5.0, 1.0 + max(0.0, -math.log10(max(p, 1e-12))) / 4.0))
            out.append(
                Anomaly(
                    detector=self.name,
                    kind="template-poisson",
                    severity=severity_from_score(score),
                    score=score,
                    bucket=bucket_ts,
                    subject="T" + str(tid),
                    template_id=tid,
                    p_value=p,
                    observed=observed,
                    expected=lam,
                    detail=(
                        "模板 T" + str(tid) + " 本桶 " + str(int(observed)) + " 次，基线速率 "
                        + str(round(lam, 3)) + " 次/桶（放大 " + str(round(ratio, 1)) + " 倍），"
                        + "泊松上尾 p = " + ("%.2e" % p) + "，在 " + str(family_size)
                        + " 次检验中经 BH-FDR(q=" + str(cfg.fdr_q) + ") 校正后仍显著"
                    ),
                    evidence=collect_evidence(
                        fs, bucket=bucket_ts, template_id=tid, limit=cfg.evidence_per_anomaly
                    ),
                )
            )
        out.sort(key=lambda a: -a.score)
        return out[: cfg.max_anomalies]
