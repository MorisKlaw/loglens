"""检测器公共契约：Anomaly / DetectConfig / DetectContext。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from ..features import FeatureSet
from ..templates import LogTemplate

__all__ = [
    "Evidence",
    "Anomaly",
    "DetectConfig",
    "DetectContext",
    "collect_evidence",
    "severity_from_score",
]


@dataclass
class Evidence:
    """一条可回溯的原始日志证据。"""

    ts: Optional[pd.Timestamp]
    line_no: int
    raw: str

    def to_row(self) -> Dict[str, Any]:
        return {
            "ts": None if self.ts is None else self.ts.isoformat(sep=" "),
            "line_no": self.line_no,
            "raw": self.raw,
        }


@dataclass
class Anomaly:
    """一个检测器给出的单点异常判定（最终会被融合成异常窗口）。"""

    detector: str
    kind: str
    severity: str
    score: float
    bucket: Optional[pd.Timestamp]
    subject: str
    detail: str
    template_id: Optional[int] = None
    observed: float = 0.0
    expected: float = 0.0
    p_value: Optional[float] = None
    evidence: List[Evidence] = field(default_factory=list)

    def to_row(self) -> Dict[str, Any]:
        return {
            "detector": self.detector,
            "kind": self.kind,
            "severity": self.severity,
            "score": round(float(self.score), 3),
            "bucket": None if self.bucket is None else self.bucket.isoformat(sep=" "),
            "subject": self.subject,
            "template_id": self.template_id,
            "observed": self.observed,
            "expected": round(float(self.expected), 3),
            "p_value": self.p_value,
            "detail": self.detail,
            # 证据里保留行号，导出的 CSV 也能回溯到原始文件
            "evidence": " || ".join(
                ("L" + str(e.line_no) + " | " + e.raw) for e in self.evidence
            ),
        }


@dataclass
class DetectConfig:
    """所有阈值集中在这里，便于复现与调参。"""

    # 稳健 z-score
    z_threshold: float = 5.0
    min_template_total: int = 4
    min_observed: int = 3
    z_extra_over_median: float = 3.0
    # 泊松尾检验
    poisson_alpha: float = 0.01
    fdr_q: float = 0.05
    poisson_min_lambda: float = 0.05
    # EWMA 控制图
    ewma_lambda: float = 0.25
    ewma_L: float = 4.0
    ewma_min_value: int = 5
    # 新模板
    novelty_warmup_frac: float = 0.10
    novelty_score: float = 0.6
    novelty_error_score: float = 1.2
    novelty_score_cap: float = 3.0
    # 规则
    rule_score: float = 0.4
    rule_score_cap: float = 1.2
    level_error_score: float = 0.5
    # 检测器开关（CLI 的 --no-* 参数对应这里）
    enable_rules: bool = True
    enable_z: bool = True
    enable_poisson: bool = True
    enable_ewma: bool = True
    enable_novelty: bool = True
    # 融合
    window_threshold: float = 1.0
    score_cap: float = 10.0
    evidence_per_anomaly: int = 3
    max_anomalies: int = 400
    top_buckets_per_rule: int = 8


@dataclass
class DetectContext:
    """检测器输入。"""

    features: FeatureSet
    templates: Sequence[LogTemplate]
    config: DetectConfig = field(default_factory=DetectConfig)

    def template(self, tid: Optional[int]) -> Optional[LogTemplate]:
        if tid is None or tid < 0 or tid >= len(self.templates):
            return None
        return self.templates[tid]

    def template_name(self, tid: Optional[int], width: int = 70) -> str:
        tmpl = self.template(tid)
        if tmpl is None:
            return "?"
        name = tmpl.pattern
        return name if len(name) <= width else name[: width - 3] + "..."


def severity_from_score(score: float) -> str:
    if score >= 3.0:
        return "high"
    if score >= 1.5:
        return "medium"
    return "low"


def collect_evidence(
    features: FeatureSet,
    bucket: Optional[pd.Timestamp] = None,
    template_id: Optional[int] = None,
    limit: int = 3,
    only_error: bool = False,
    regex: Optional[str] = None,
) -> List[Evidence]:
    """从明细表中抽取原始日志证据（可回溯到行号）。"""
    frame = features.frame
    if frame is None or frame.empty:
        return []
    sub = frame
    if bucket is not None:
        sub = sub[sub["bucket"] == bucket]
    if template_id is not None:
        sub = sub[sub["template_id"] == template_id]
    if only_error:
        sub = sub[sub["is_error"]]
    if regex:
        sub = sub[sub["message"].astype(str).str.contains(regex, case=False, regex=True, na=False)]
    if limit and limit > 0:
        sub = sub.head(limit)
    out: List[Evidence] = []
    for _, row in sub.iterrows():
        out.append(
            Evidence(
                ts=row["ts"] if isinstance(row["ts"], pd.Timestamp) else pd.Timestamp(row["ts"]),
                line_no=int(row["line_no"]),
                raw=str(row["raw"]),
            )
        )
    return out
