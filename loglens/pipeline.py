"""流水线编排：一次 run 走完 解析 -> 模板 -> 特征 -> 检测 -> 融合 -> 产物。

产物清单（每个输入日志一个目录）::

    results/<name>/
      report.md          # 分析报告（主交付物）
      parsed.csv         # 逐行结构化结果（含模板 id）
      templates.csv      # 模板表
      anomalies.csv      # 单点判定明细
      windows.csv        # 逐时间桶分数
      incidents.csv      # 异常窗口（事件）
      summary.json       # 机器可读汇总
      charts/*.svg|png   # 时间线 / 模板频次 / 级别分布
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from . import __version__
from .detectors import Anomaly, DetectConfig, DetectContext, default_detectors, fuse, Incident
from .features import FeatureSet, build_features, humanize_seconds
from .parsers import ParseResult, parse_file
from .report import render_report, write_summary_json
from .templates import LogTemplate, TemplateMiner
from .visualize import level_bar_chart, template_bar_chart, timeline_chart, write_chart

__all__ = ["RunConfig", "RunResult", "run"]


@dataclass
class RunConfig:
    """一次实验的全部输入参数（可序列化，保证可复现）。"""

    input_path: str
    out_dir: str
    fmt: str = "auto"
    syslog_year: int = 2005
    bucket_seconds: Optional[int] = None
    target_buckets: int = 240
    top_templates: int = 15
    detail_incidents: int = 5
    title: Optional[str] = None
    write_parsed: bool = True
    charts: bool = True
    png: bool = True
    detect: DetectConfig = field(default_factory=DetectConfig)


@dataclass
class RunResult:
    """一次实验的全部结果。"""

    config: RunConfig
    parse: ParseResult
    features: FeatureSet
    templates: List[LogTemplate]
    anomalies: List[Anomaly]
    windows: pd.DataFrame
    incidents: List[Incident]
    artifacts: Dict[str, str] = field(default_factory=dict)


def analyze_records(
    records: Sequence,
    detect: Optional[DetectConfig] = None,
    bucket_seconds: Optional[int] = None,
    target_buckets: int = 240,
):
    """在内存里跑 模板 -> 特征 -> 检测 -> 融合（不落盘），供基准评测复用。

    返回 (templates, features, anomalies, windows, incidents)。
    """
    detect = detect or DetectConfig()
    miner = TemplateMiner(depth=4, sim_th=0.4, max_children=100)
    miner.mine(records)
    templates = list(miner.templates)
    features = build_features(
        records, bucket_seconds=bucket_seconds, target_buckets=target_buckets
    )
    ctx = DetectContext(features=features, templates=templates, config=detect)
    anomalies: List[Anomaly] = []
    for detector in default_detectors(detect):
        anomalies.extend(detector.detect(ctx))
    anomalies.sort(key=lambda a: (-a.score, a.bucket if a.bucket is not None else pd.Timestamp.min))
    windows, incidents = fuse(ctx, anomalies)
    return templates, features, anomalies, windows, incidents


def _template_frame(templates: Sequence[LogTemplate]) -> pd.DataFrame:
    rows = []
    for t in sorted(templates, key=lambda x: (-x.count, x.template_id)):
        rows.append(
            {
                "template_id": t.template_id,
                "count": t.count,
                "wildcards": t.wildcard_count,
                "is_constant": t.is_constant,
                "main_level": t.main_level,
                "first_seen": t.first_seen,
                "last_seen": t.last_seen,
                "top_source": t.sources.most_common(1)[0][0] if t.sources else None,
                "pattern": t.pattern,
                "example": t.examples[0] if t.examples else None,
            }
        )
    return pd.DataFrame(rows)


def _relpath(p: str) -> str:
    """仓库内路径写成相对路径（方便复制粘贴复现），仓库外路径原样返回。"""
    root = Path(__file__).resolve().parent.parent
    try:
        return str(Path(p).resolve().relative_to(root)).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


def run(cfg: RunConfig) -> RunResult:
    """执行完整流水线。"""
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "charts").mkdir(parents=True, exist_ok=True)

    # 1) 解析
    parse_result = parse_file(cfg.input_path, fmt=cfg.fmt, syslog_year=cfg.syslog_year)

    # 2) 模板挖掘
    miner = TemplateMiner(depth=4, sim_th=0.4, max_children=100)
    miner.mine(parse_result.records)
    templates = list(miner.templates)

    # 3) 特征
    features = build_features(
        parse_result.records, bucket_seconds=cfg.bucket_seconds, target_buckets=cfg.target_buckets
    )

    # 4) 检测
    ctx = DetectContext(features=features, templates=templates, config=cfg.detect)
    anomalies: List[Anomaly] = []
    for detector in default_detectors(cfg.detect):
        anomalies.extend(detector.detect(ctx))
    anomalies.sort(key=lambda a: (-a.score, a.bucket if a.bucket is not None else pd.Timestamp.min))

    # 5) 融合
    windows, incidents = fuse(ctx, anomalies)

    result = RunResult(
        config=cfg,
        parse=parse_result,
        features=features,
        templates=templates,
        anomalies=anomalies,
        windows=windows,
        incidents=incidents,
    )

    # 6) 产物落盘
    artifacts: Dict[str, str] = {}
    tmpl_df = _template_frame(templates)
    p = out_dir / "templates.csv"
    tmpl_df.to_csv(p, index=False, encoding="utf-8-sig")
    artifacts["templates"] = str(p)

    if cfg.write_parsed:
        parsed = features.frame.copy()
        parsed["ts"] = parsed["ts"].map(lambda x: None if pd.isna(x) else pd.Timestamp(x).isoformat(sep=" "))
        keep = [c for c in ["line_no", "ts", "level", "source", "pid", "host", "template_id", "template", "message", "raw"] if c in parsed.columns]
        parsed = parsed[keep]
        p = out_dir / "parsed.csv"
        parsed.to_csv(p, index=False, encoding="utf-8-sig")
        artifacts["parsed"] = str(p)

    anom_df = pd.DataFrame([a.to_row() for a in anomalies])
    p = out_dir / "anomalies.csv"
    anom_df.to_csv(p, index=False, encoding="utf-8-sig")
    artifacts["anomalies"] = str(p)

    if not windows.empty:
        win = windows.copy()
        win["bucket"] = win["bucket"].map(lambda x: pd.Timestamp(x).isoformat(sep=" "))
        p = out_dir / "windows.csv"
        win.to_csv(p, index=False, encoding="utf-8-sig")
        artifacts["windows"] = str(p)

    inc_df = pd.DataFrame([i.to_row() for i in incidents])
    p = out_dir / "incidents.csv"
    inc_df.to_csv(p, index=False, encoding="utf-8-sig")
    artifacts["incidents"] = str(p)

    # 7) 图表
    charts: Dict[str, List[str]] = {}
    if cfg.charts and features.n_buckets > 0:
        try:
            base = out_dir / "charts" / "timeline"
            charts["timeline"] = write_chart(
                timeline_chart(features, windows, incidents, title="Log volume timeline (" + Path(cfg.input_path).name + ")"),
                base,
                png=cfg.png,
            )
            charts["templates"] = write_chart(template_bar_chart(templates), out_dir / "charts" / "templates", png=cfg.png)
            charts["level"] = write_chart(level_bar_chart(features.level_counts), out_dir / "charts" / "level", png=cfg.png)
            artifacts["charts"] = ",".join(sum(charts.values(), []))
        except Exception as exc:  # 图表失败不应影响主流程
            artifacts["charts_error"] = repr(exc)

    # 8) 报告
    title = cfg.title or (Path(cfg.input_path).stem + " 日志解析与异常检测报告")
    # 报告里的复现命令用相对路径 + 真实输出目录，换机也能直接照抄
    repro = (
        "python -m loglens run -i " + _relpath(cfg.input_path) + " -o " + _relpath(cfg.out_dir)
        + " --format " + parse_result.fmt
    )
    report = render_report(
        source_path=_relpath(cfg.input_path),
        title=title,
        parse_result=parse_result,
        features=features,
        templates=templates,
        anomalies=anomalies,
        table=windows,
        incidents=incidents,
        config=cfg.detect,
        charts=charts,
        generated_at=datetime.now(),
        top_templates=cfg.top_templates,
        detail_incidents=cfg.detail_incidents,
        version=__version__,
        repro_command=repro,
    )
    p = out_dir / "report.md"
    p.write_text(report, encoding="utf-8")
    artifacts["report"] = str(p)

    summary = {
        "tool": "loglens",
        "version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(cfg.input_path),
        "parse": parse_result.summary(),
        "templates": {
            "n_templates": len(templates),
            "n_singleton": int(sum(1 for t in templates if t.count == 1)),
            "top": [
                {"template_id": t.template_id, "count": t.count, "pattern": t.pattern}
                for t in sorted(templates, key=lambda x: -x.count)[:10]
            ],
        },
        "features": {
            "bucket_seconds": features.bucket_seconds,
            "n_buckets": features.n_buckets,
            "span_seconds": int(features.span_seconds),
            "synthetic_time": features.synthetic_time,
        },
        "detection": {
            "n_anomalies": len(anomalies),
            "per_detector": _count_by(anomalies, lambda a: a.detector),
            "per_kind": _count_by(anomalies, lambda a: a.kind),
            "n_incidents": len(incidents),
            "incidents": [i.to_row() for i in incidents[:20]],
            "config": asdict(cfg.detect),
        },
    }
    artifacts["summary"] = write_summary_json(out_dir / "summary.json", summary)
    result.artifacts = artifacts
    return result


def _count_by(items: Sequence, key) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        k = key(item)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def describe(result: RunResult) -> str:
    """控制台摘要。"""
    meta = result.parse.summary()
    lines = [
        "输入文件      : " + str(result.config.input_path),
        "识别格式      : " + str(meta["format"]) + "（匹配率 " + str(meta["match_rate"]) + "，编码 " + str(meta["encoding"]) + "）",
        "解析          : " + str(meta["parsed_records"]) + "/" + str(meta["total_lines"]) + " 行，解析率 "
        + ("%.2f%%" % (100 * float(meta["parse_rate"]))),
        "模板          : " + str(len(result.templates)) + " 个（单例 "
        + str(sum(1 for t in result.templates if t.count == 1)) + " 个）",
        "时间桶        : " + humanize_seconds(result.features.bucket_seconds) + " x "
        + str(result.features.n_buckets) + " 桶",
        "单点判定      : " + str(len(result.anomalies)) + " 条",
        "异常窗口      : " + str(len(result.incidents)) + " 个"
        + ("（最高分 %.2f）" % result.incidents[0].score if result.incidents else ""),
    ]
    for idx, inc in enumerate(result.incidents[:3], start=1):
        lines.append("  #" + str(idx) + " " + inc.summary)
    lines.append("产物          :")
    for key, value in sorted(result.artifacts.items()):
        lines.append("  " + key.ljust(10) + " " + value)
    return "\n".join(lines)
