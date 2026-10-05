"""异常检测器集合。

每个检测器输入统一的 DetectContext，输出统一的 Anomaly 列表，
最终由 ensemble 融合成可解释的异常窗口。
"""

from .base import Anomaly, DetectConfig, DetectContext, Evidence, collect_evidence
from .ensemble import fuse, Incident
from .ewma import EwmaDetector
from .novelty import NoveltyDetector
from .poisson import PoissonDetector
from .robust_z import RobustZDetector
from .rules import RuleDetector

__all__ = [
    "Anomaly",
    "DetectConfig",
    "DetectContext",
    "Evidence",
    "collect_evidence",
    "RuleDetector",
    "RobustZDetector",
    "PoissonDetector",
    "EwmaDetector",
    "NoveltyDetector",
    "fuse",
    "Incident",
    "default_detectors",
]


def default_detectors(config: DetectConfig):
    """按配置返回启用的检测器列表。"""
    detectors = []
    if config.enable_rules:
        detectors.append(RuleDetector())
    if config.enable_z:
        detectors.append(RobustZDetector())
    if config.enable_poisson:
        detectors.append(PoissonDetector())
    if config.enable_ewma:
        detectors.append(EwmaDetector())
    if config.enable_novelty:
        detectors.append(NoveltyDetector())
    return detectors
