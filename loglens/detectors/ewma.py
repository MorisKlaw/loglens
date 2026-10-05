"""EWMA 控制图检测器：看「整站日志量 / 错误量」的量级突变。

为什么需要它
------------
按模板的检验只能发现「某类事件变多」，发现不了「所有事件一起变多/变少」，
也发现不了「错误量整体抬升」。EWMA（指数加权移动平均）控制图用一条在线
更新的均值与方差，判断当前窗口是否越出 L 倍标准差控制限：

    m_t = lam * x_t + (1 - lam) * m_{t-1}
    v_t = lam * (x_t - m_{t-1})^2 + (1 - lam) * v_{t-1}
    越限条件： |x_t - m_{t-1}| > L * sqrt(v_{t-1})

它天然适应非平稳流量（早晚高峰），且只需常数内存，适合轻量工具。
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

from ..schema import ERROR_LEVELS, WARN_LEVELS
from .base import Anomaly, DetectContext, collect_evidence, severity_from_score

__all__ = ["EwmaDetector", "ewma_flags"]


def ewma_flags(
    values: Sequence[float],
    lam: float = 0.25,
    L: float = 4.0,
    min_value: float = 5.0,
    allow_drop: bool = False,
) -> List[Tuple[int, float, float, str]]:
    """返回 [(下标, 残差, 当时标准差, 方向)]。"""
    arr = np.asarray(list(values), dtype=float)
    n = arr.size
    flags: List[Tuple[int, float, float, str]] = []
    if n < 5:
        return flags
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median))) * 1.4826
    var = mad * mad if mad > 1e-9 else float(np.var(arr))
    if var <= 1e-12:
        var = 1.0
    mean = median
    for t in range(n):
        x = float(arr[t])
        sigma = math.sqrt(var) if var > 0 else 0.0
        if t > 0 and sigma > 1e-9:
            resid = x - mean
            if resid > L * sigma and x >= min_value:
                flags.append((t, resid, sigma, "up"))
            elif allow_drop and (-resid) > L * sigma and mean >= min_value:
                flags.append((t, resid, sigma, "down"))
        resid = x - mean
        mean = lam * x + (1.0 - lam) * mean
        var = lam * resid * resid + (1.0 - lam) * var
    return flags


class EwmaDetector:
    """对「总日志量 / 错误量 / 告警量」三条序列做 EWMA 控制图。"""

    name = "ewma"

    def _series(self, ctx: DetectContext) -> List[Tuple[str, pd.Series, bool]]:
        fs = ctx.features
        series: List[Tuple[str, pd.Series, bool]] = [
            ("volume", fs.volume, True),
            ("error-volume", fs.level_volume(sorted(ERROR_LEVELS)), False),
            ("warn-volume", fs.level_volume(sorted(WARN_LEVELS)), False),
        ]
        return series

    def detect(self, ctx: DetectContext) -> List[Anomaly]:
        fs = ctx.features
        cfg = ctx.config
        out: List[Anomaly] = []
        if fs.n_buckets < 5:
            return out
        buckets = fs.buckets
        for label, series, allow_drop in self._series(ctx):
            values = series.to_numpy(dtype=float)
            if values.sum() <= 0:
                continue
            min_value = cfg.ewma_min_value if label == "volume" else 2
            for idx, resid, sigma, direction in ewma_flags(
                values, lam=cfg.ewma_lambda, L=cfg.ewma_L, min_value=min_value, allow_drop=allow_drop
            ):
                bucket_ts = pd.Timestamp(buckets[idx])
                ratio = (values[idx] / values[max(0, idx - 1)]) if idx > 0 and values[idx - 1] > 0 else float("inf")
                score = 0.6 if direction == "up" else 0.5
                if label != "volume":
                    score += 0.3
                out.append(
                    Anomaly(
                        detector=self.name,
                        kind="ewma-" + label + "-" + direction,
                        severity=severity_from_score(score),
                        score=score,
                        bucket=bucket_ts,
                        subject=label,
                        observed=float(values[idx]),
                        expected=float(np.median(values)),
                        detail=(
                            label + " 本桶 " + str(int(values[idx])) + " 条，历史中位数 "
                            + str(round(float(np.median(values)), 1)) + " 条，偏离控制限 "
                            + str(round(abs(resid) / sigma, 1)) + " sigma（L=" + str(cfg.ewma_L)
                            + "，方向 " + ("上升" if direction == "up" else "下降") + "）"
                            + ("；环比上一桶 " + str(round(ratio, 2)) + " 倍" if ratio != float("inf") else "")
                        ),
                        evidence=collect_evidence(
                            fs,
                            bucket=bucket_ts,
                            limit=cfg.evidence_per_anomaly,
                            only_error=(label != "volume"),
                        ),
                    )
                )
        out.sort(key=lambda a: -a.score)
        return out[: cfg.max_anomalies]
