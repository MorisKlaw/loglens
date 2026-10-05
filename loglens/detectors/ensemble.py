"""融合层：把各检测器的单点判定聚合成「异常窗口 / 事件」。

打分规则（可解释、无黑盒权重）
------------------------------
* 同一时间桶里，同一个模板被 z-score 与泊松检验同时命中时只计一次
  （取两者较高权重），避免「同一件事被数两遍」；
* 新模板信号按自身权重计入（错误级新模板可直接成窗）；
* 规则命中有权重上限，防止一条高频规则淹没统计信号；
* EWMA 的「量级突变」「错误量抬升」各自独立计数。

桶分数 >= window_threshold 的连续桶合并成一个 Incident（事件），
事件里保留：涉及模板、命中的检测器、判定依据与原始日志证据。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .base import Anomaly, DetectContext, Evidence, severity_from_score

__all__ = ["Incident", "fuse", "STAT_KINDS"]


STAT_KINDS = ("template-burst", "template-poisson")


@dataclass
class Incident:
    """一段连续的异常窗口。"""

    start: pd.Timestamp
    end: pd.Timestamp
    score: float
    severity: str
    detectors: List[str] = field(default_factory=list)
    kinds: List[str] = field(default_factory=list)
    template_ids: List[int] = field(default_factory=list)
    n_anomalies: int = 0
    summary: str = ""
    details: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    bucket_seconds: int = 60

    @property
    def duration_seconds(self) -> float:
        return float((self.end - self.start).total_seconds()) + self.bucket_seconds

    def to_row(self) -> Dict[str, object]:
        return {
            "start": self.start.isoformat(sep=" "),
            "end": self.end.isoformat(sep=" "),
            "duration_seconds": int(self.duration_seconds),
            "score": round(self.score, 3),
            "severity": self.severity,
            "detectors": ",".join(self.detectors),
            "kinds": ",".join(self.kinds),
            "templates": ",".join("T" + str(t) for t in self.template_ids),
            "n_anomalies": self.n_anomalies,
            "summary": self.summary,
            "evidence": " || ".join(
                ("L" + str(e.line_no) + " | " + e.raw) for e in self.evidence
            ),
        }


def _bucket_table(ctx: DetectContext, anomalies: Sequence[Anomaly]) -> pd.DataFrame:
    fs = ctx.features
    buckets = list(fs.buckets)
    index = {pd.Timestamp(b): i for i, b in enumerate(buckets)}
    scores = np.zeros(len(buckets), dtype=float)
    stat_weight: List[Dict[int, float]] = [dict() for _ in buckets]
    detectors: List[set] = [set() for _ in buckets]
    kinds: List[set] = [set() for _ in buckets]
    rule_totals = np.zeros(len(buckets), dtype=float)
    novelty_totals = np.zeros(len(buckets), dtype=float)
    counts = np.zeros(len(buckets), dtype=int)
    per_bucket_anoms: List[List[Anomaly]] = [[] for _ in buckets]

    for a in anomalies:
        if a.bucket is None:
            continue
        ts = pd.Timestamp(a.bucket)
        i = index.get(ts)
        if i is None:
            continue
        detectors[i].add(a.detector)
        kinds[i].add(a.kind)
        counts[i] += 1
        per_bucket_anoms[i].append(a)
        if a.kind in STAT_KINDS and a.template_id is not None:
            prev = stat_weight[i].get(a.template_id, 0.0)
            weight = 1.0 if a.kind == "template-burst" else 0.9
            stat_weight[i][a.template_id] = max(prev, weight)
        elif a.kind == "new-template":
            novelty_totals[i] += a.score
        elif a.kind.startswith("rule:"):
            rule_totals[i] += a.score
        elif a.kind.startswith("ewma-"):
            scores[i] += a.score

    for i in range(len(buckets)):
        # 各类信号分别封顶后再相加，最后整体封顶：让分数保持「可解释的 0~10」，
        # 不至于被某一类信号（例如一次升级带来 90 个新模板）推到几十上百。
        stat_total = min(3.0, sum(stat_weight[i].values()))
        novelty_total = min(ctx.config.novelty_score_cap, float(novelty_totals[i]))
        scores[i] = min(
            ctx.config.score_cap,
            scores[i] + stat_total + novelty_total + min(float(rule_totals[i]), ctx.config.rule_score_cap),
        )

    table = pd.DataFrame(
        {
            "bucket": buckets,
            "score": scores,
            "n_anomalies": counts,
            "detectors": [",".join(sorted(d)) for d in detectors],
            "kinds": [",".join(sorted(k)) for k in kinds],
        }
    )
    table["flagged"] = table["score"] >= ctx.config.window_threshold
    table.attrs["stat_weight"] = stat_weight
    table.attrs["per_bucket"] = per_bucket_anoms
    return table


def fuse(ctx: DetectContext, anomalies: Sequence[Anomaly]) -> Tuple[pd.DataFrame, List[Incident]]:
    """融合所有检测器输出，返回 (逐桶明细表, 事件列表)。"""
    fs = ctx.features
    if fs.n_buckets == 0:
        return pd.DataFrame(), []

    table = _bucket_table(ctx, anomalies)
    stat_weight = table.attrs["stat_weight"]
    per_bucket = table.attrs["per_bucket"]
    buckets = list(fs.buckets)
    incidents: List[Incident] = []

    flagged = table["flagged"].to_numpy()
    i = 0
    while i < len(buckets):
        if not flagged[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(buckets) and flagged[j + 1]:
            j += 1
        window_anoms: List[Anomaly] = []
        for k in range(i, j + 1):
            window_anoms.extend(per_bucket[k])
        score = float(table["score"].to_numpy()[i : j + 1].max())
        detectors = sorted({a.detector for a in window_anoms})
        kinds = sorted({a.kind for a in window_anoms})
        tmpl_counts: Dict[int, float] = {}
        for k in range(i, j + 1):
            for tid, w in stat_weight[k].items():
                tmpl_counts[tid] = tmpl_counts.get(tid, 0.0) + w
        for a in window_anoms:
            if a.kind == "new-template" and a.template_id is not None:
                tmpl_counts[a.template_id] = tmpl_counts.get(a.template_id, 0.0) + a.score
        template_ids = [t for t, _ in sorted(tmpl_counts.items(), key=lambda kv: -kv[1])][:5]

        evidence: List[Evidence] = []
        seen = set()
        for a in sorted(window_anoms, key=lambda x: -x.score):
            for e in a.evidence:
                if e.raw in seen:
                    continue
                seen.add(e.raw)
                evidence.append(e)
                if len(evidence) >= 6:
                    break
            if len(evidence) >= 6:
                break

        details = [a.detail for a in sorted(window_anoms, key=lambda x: -x.score)]
        summary = _summarize(
            buckets[i], buckets[j], fs.bucket_seconds, score, detectors, template_ids, window_anoms, ctx
        )
        incidents.append(
            Incident(
                start=pd.Timestamp(buckets[i]),
                end=pd.Timestamp(buckets[j]),
                score=score,
                severity=severity_from_score(score),
                detectors=detectors,
                kinds=kinds,
                template_ids=template_ids,
                n_anomalies=len(window_anoms),
                summary=summary,
                details=details,
                evidence=evidence,
                bucket_seconds=fs.bucket_seconds,
            )
        )
        i = j + 1

    incidents.sort(key=lambda x: (-x.score, x.start))
    return table, incidents


def _fmt_duration(seconds: float) -> str:
    """秒数 -> 中文时长（秒 / 分钟 / 小时 / 天）。"""
    if seconds >= 86400:
        return "%.1f 天" % (seconds / 86400.0)
    if seconds >= 3600:
        return "%.1f 小时" % (seconds / 3600.0)
    if seconds >= 60:
        return "%.0f 分钟" % (seconds / 60.0)
    return "%.0f 秒" % seconds


def _summarize(
    start: pd.Timestamp,
    end: pd.Timestamp,
    bucket_seconds: int,
    score: float,
    detectors: Sequence[str],
    template_ids: Sequence[int],
    anomalies: Sequence[Anomaly],
    ctx: DetectContext,
) -> str:
    """生成一句人能读懂的事件摘要。"""
    parts: List[str] = []
    dur = float((end - start).total_seconds()) + bucket_seconds
    span = start.strftime("%m-%d %H:%M:%S") + " ~ " + end.strftime("%H:%M:%S")
    parts.append("窗口 " + span + "（约 " + _fmt_duration(dur) + "）")
    stat = [a for a in anomalies if a.kind in STAT_KINDS]
    if stat:
        # 优先用泊松检验的基线速率（λ 更贴合「每桶平均多少次」的直觉），
        # 没有泊松命中时再退回稳健 z 的中位数
        poisson_hits = [a for a in stat if a.kind == "template-poisson"]
        top = max(poisson_hits or stat, key=lambda a: a.score)
        parts.append(
            "模板 T" + str(top.template_id) + " 计数异常（观测 " + str(int(top.observed)) + " 次 / 基线 "
            + str(round(top.expected, 2)) + " 次）"
        )
    novels = [a for a in anomalies if a.kind == "new-template"]
    if novels:
        parts.append("出现 " + str(len(novels)) + " 个观察期后的新模板")
    rules = sorted({a.kind.split(":", 1)[1] for a in anomalies if a.kind.startswith("rule:")})
    if rules:
        parts.append("规则命中 " + "/".join(rules))
    ewmas = sorted({a.kind for a in anomalies if a.kind.startswith("ewma-")})
    if ewmas:
        parts.append("量级突变 " + "/".join(e.replace("ewma-", "") for e in ewmas))
    parts.append("检测器 " + "+".join(detectors))
    return "；".join(parts)
