"""规则检测器：级别聚集 + 已知故障关键词。

规则层解决的是「统计层看不见的、有明确语义的故障」，例如 SSH 暴力破解、
段错误、磁盘写满、HDFS 块损坏。它的优势是可解释、零误学习成本；缺点是
只能发现被写进规则的问题（详见 docs/03 漏报误报分析）。

命中策略：每条规则在每个时间桶内独立统计，命中次数达到阈值的
(规则, 时间桶) 组合输出一条 Anomaly，附带原始日志证据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import pandas as pd

from ..schema import ERROR_LEVELS
from .base import Anomaly, DetectContext, Evidence, collect_evidence, severity_from_score

__all__ = ["Rule", "KEYWORD_RULES", "RuleDetector"]


@dataclass(frozen=True)
class Rule:
    """一条关键词规则。"""

    name: str
    pattern: str
    severity: str
    description: str
    min_hits: int = 1
    flags: int = re.IGNORECASE


#: 关键词规则表：来源是公开运维经验 + 本实验四个数据集的真实故障语义
KEYWORD_RULES: Sequence[Rule] = (
    Rule(
        name="auth-failure",
        pattern=r"authentication failure",
        severity="high",
        description="SSH/系统认证失败：可能是口令爆破或配置错误的凭据",
        min_hits=3,
    ),
    Rule(
        name="unknown-user",
        pattern=r"check pass; user unknown|invalid user",
        severity="medium",
        description="登录了不存在的账号：常见于扫描/爆破",
        min_hits=3,
    ),
    Rule(
        name="segfault",
        pattern=r"segmentation fault|signal 11",
        severity="high",
        description="进程段错误崩溃",
    ),
    Rule(
        name="jk-error",
        pattern=r"mod_jk.*error state|jk2_init\(\).*failed",
        severity="medium",
        description="Apache mod_jk 连接器异常状态",
    ),
    Rule(
        name="exception-traceback",
        pattern=r"\b(?:exception|traceback|stack trace|caused by)\b",
        severity="medium",
        description="异常/堆栈：通常是故障的直接现场",
        min_hits=2,
    ),
    Rule(
        name="io-error",
        pattern=r"ioexception|io error|input/output error|premature eof|connection reset",
        severity="high",
        description="IO/网络异常",
    ),
    Rule(
        name="disk-full",
        pattern=r"no space left on device|disk (?:error|failure)|read-only file system",
        severity="high",
        description="磁盘写满或磁盘故障",
    ),
    Rule(
        name="oom",
        pattern=r"out ?of ?memory|oom[-_ ]?kill|cannot allocate memory",
        severity="high",
        description="内存耗尽/被 OOM Killer 击杀",
    ),
    Rule(
        name="hdfs-corrupt",
        pattern=r"is not valid|could not obtain block|blockmissingexception|corrupt|checksum error",
        severity="high",
        description="HDFS 副本/校验和异常，可能丢块",
    ),
    Rule(
        name="zk-quorum",
        pattern=r"notification time out|send worker leaving thread|cannot open channel|leader election",
        severity="medium",
        description="ZooKeeper 选主/集群通信异常",
        min_hits=2,
    ),
)


class RuleDetector:
    """规则检测器。"""

    name = "rules"

    def __init__(self, rules: Sequence[Rule] = KEYWORD_RULES) -> None:
        self.rules = list(rules)

    # -- 关键词规则 --
    def _keyword_anomalies(self, ctx: DetectContext) -> List[Anomaly]:
        frame = ctx.features.frame
        out: List[Anomaly] = []
        if frame is None or frame.empty:
            return out
        msg = frame["message"].astype(str)
        for rule in self.rules:
            mask = msg.str.contains(rule.pattern, case=False, regex=True, na=False)
            hits = frame[mask]
            if hits.empty or len(hits) < rule.min_hits:
                continue
            grouped = hits.groupby("bucket", observed=True).size().sort_values(ascending=False)
            for bucket, count in list(grouped.items())[: ctx.config.top_buckets_per_rule]:
                bucket_ts = pd.Timestamp(bucket)
                ev = collect_evidence(
                    ctx.features,
                    bucket=bucket_ts,
                    limit=ctx.config.evidence_per_anomaly,
                    regex=rule.pattern,
                )
                out.append(
                    Anomaly(
                        detector=self.name,
                        kind="rule:" + rule.name,
                        severity=rule.severity,
                        score=ctx.config.rule_score if rule.severity != "high" else ctx.config.rule_score * 1.5,
                        bucket=bucket_ts,
                        subject=rule.name,
                        detail=(
                            rule.description
                            + "；本桶命中 " + str(int(count)) + " 次（规则阈值 " + str(rule.min_hits)
                            + " 次，全文共 " + str(int(len(hits))) + " 次）"
                        ),
                        observed=float(count),
                        expected=float(rule.min_hits),
                        evidence=ev,
                    )
                )
        return out

    # -- 级别规则 --
    def _level_anomalies(self, ctx: DetectContext) -> List[Anomaly]:
        fs = ctx.features
        frame = fs.frame
        out: List[Anomaly] = []
        if frame is None or frame.empty:
            return out
        err = frame[frame["level"].isin(sorted(ERROR_LEVELS))]
        if err.empty:
            return out
        if fs.n_buckets:
            expected = float(len(err)) / max(1, fs.n_buckets)
        else:
            expected = 0.0
        grouped = err.groupby(["bucket", "level"], observed=True).size()
        for (bucket, level), count in grouped.items():
            bucket_ts = pd.Timestamp(bucket)
            ev = collect_evidence(
                ctx.features, bucket=bucket_ts, limit=ctx.config.evidence_per_anomaly, only_error=True
            )
            out.append(
                Anomaly(
                    detector=self.name,
                    kind="rule:error-level",
                    severity="medium" if count < 3 else "high",
                    score=ctx.config.level_error_score * (1.0 if count < 3 else 1.6),
                    bucket=bucket_ts,
                    subject=str(level),
                    detail="出现 " + str(level) + " 级日志 " + str(int(count)) + " 条，全文件均值 "
                    + str(round(expected, 3)) + " 条/桶",
                    observed=float(count),
                    expected=expected,
                    evidence=ev,
                )
            )
        return out

    def detect(self, ctx: DetectContext) -> List[Anomaly]:
        return self._keyword_anomalies(ctx) + self._level_anomalies(ctx)
