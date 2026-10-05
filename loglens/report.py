"""报告生成：把一次实验结果渲染成可直接阅读的 Markdown 分析报告。

报告结构对应考核题的要求：
* 数据概览          -> 「解析与统计」的口径说明
* 模板挖掘结果      -> 「提炼时间/级别/来源等关键字段」的产物
* 异常检测结果      -> 「识别异常 + 说明异常样本与判断依据」
* 方法与参数        -> 「调研并选择一种检测方法，说明理由」的落地
* 漏报/误报小节     -> 「什么情况下会漏报或误报」
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from .detectors.base import Anomaly, DetectConfig
from .detectors.ensemble import Incident
from .features import FeatureSet, humanize_seconds
from .parsers import ParseResult
from .templates import LogTemplate

__all__ = ["render_report", "write_summary_json", "table_md"]

FENCE = chr(96) * 3


def table_md(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """生成 Markdown 表格。"""
    out = ["| " + " | ".join(str(h) for h in headers) + " |"]
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        cells = []
        for cell in row:
            text = "" if cell is None else str(cell)
            text = text.replace("|", "\\|").replace("\n", " ")
            cells.append(text)
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _fmt_ts(ts) -> str:
    if ts is None or isinstance(ts, float):
        return "-"
    try:
        return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _truncate(text: str, width: int = 110) -> str:
    text = str(text)
    return text if len(text) <= width else text[: width - 3] + "..."


def _level_rows(features: FeatureSet) -> List[List[object]]:
    if features.level_counts is None or features.level_counts.empty:
        return []
    totals = features.level_counts.sum(axis=0).sort_values(ascending=False)
    total = float(totals.sum()) or 1.0
    rows: List[List[object]] = []
    for name, count in totals.items():
        rows.append([name, int(count), "%.1f%%" % (100.0 * float(count) / total)])
    return rows


def _source_rows(features: FeatureSet, top: int = 10) -> List[List[object]]:
    if features.source_counts is None or features.source_counts.empty:
        return []
    totals = features.source_counts.sum(axis=0).sort_values(ascending=False).head(top)
    total = float(features.source_counts.sum(axis=0).sum()) or 1.0
    return [[name, int(count), "%.1f%%" % (100.0 * float(count) / total)] for name, count in totals.items()]


def _template_rows(templates: Sequence[LogTemplate], top: int) -> List[List[object]]:
    total = sum(t.count for t in templates) or 1
    rows: List[List[object]] = []
    for tmpl in sorted(templates, key=lambda t: (-t.count, t.template_id))[:top]:
        rows.append(
            [
                "T" + str(tmpl.template_id),
                "[" + _truncate(tmpl.pattern, 90) + "]",
                tmpl.count,
                "%.1f%%" % (100.0 * tmpl.count / total),
                _fmt_ts(tmpl.first_seen),
                _fmt_ts(tmpl.last_seen),
                tmpl.main_level or "-",
            ]
        )
    return rows


def _detector_param_rows(config: DetectConfig) -> List[List[object]]:
    return [
        ["稳健 z-score（模板计数）", "MAD 估计 sigma", "z >= " + str(config.z_threshold) + "，且计数 >= max("
         + str(config.min_observed) + ", 中位数+" + str(config.z_extra_over_median) + ")"],
        ["泊松尾检验", "上尾概率 + BH-FDR", "p <= " + str(config.poisson_alpha) + " 粗筛，FDR q = " + str(config.fdr_q)
         + "，基线速率 >= " + str(config.poisson_min_lambda)],
        ["EWMA 控制图", "指数加权均值/方差", "lambda = " + str(config.ewma_lambda) + "，控制限 L = " + str(config.ewma_L)],
        ["新模板（novelty）", "观察期后首次出现", "观察期 = 前 " + str(int(config.novelty_warmup_frac * 100)) + "% 时间桶"],
        ["规则（关键词/级别）", "运维经验规则表", "命中阈值按规则配置；权重上限 " + str(config.rule_score_cap)],
        ["窗口融合", "加权求和", "桶分数 >= " + str(config.window_threshold) + " 判为异常桶，连续异常桶合并为事件"],
    ]


def render_report(
    *,
    source_path: str,
    title: str,
    parse_result: ParseResult,
    features: FeatureSet,
    templates: Sequence[LogTemplate],
    anomalies: Sequence[Anomaly],
    table: pd.DataFrame,
    incidents: Sequence[Incident],
    config: DetectConfig,
    charts: Optional[Dict[str, List[str]]] = None,
    generated_at: Optional[datetime] = None,
    top_templates: int = 15,
    detail_incidents: int = 5,
    version: str = "0.1.0",
    repro_command: Optional[str] = None,
) -> str:
    """渲染一份完整的 Markdown 报告。"""
    charts = charts or {}
    generated_at = generated_at or datetime.now()
    lines: List[str] = []
    meta = parse_result.summary()
    fs = features

    def chart_md(key: str, alt: str) -> List[str]:
        paths = charts.get(key) or []
        if not paths:
            return []
        rel = Path(paths[0]).name
        return ["", "![%s](charts/%s)" % (alt, rel), ""]

    # ---------------- 头部 ----------------
    lines.append("# " + title)
    lines.append("")
    lines.append(
        "> 自动生成于 " + generated_at.strftime("%Y-%m-%d %H:%M:%S") + " ｜ 工具 loglens v" + version
        + " ｜ 数据文件 " + source_path
    )
    lines.append("")

    # ---------------- 结论速览 ----------------
    n_high = sum(1 for i in incidents if i.severity == "high")
    n_med = sum(1 for i in incidents if i.severity == "medium")
    n_low = sum(1 for i in incidents if i.severity == "low")
    top_incident = incidents[0] if incidents else None
    detector_counts: Dict[str, int] = {}
    for a in anomalies:
        detector_counts[a.detector] = detector_counts.get(a.detector, 0) + 1
    lines.append("## 0 结论速览")
    lines.append("")
    lines.append("- **解析**：" + str(meta["total_lines"]) + " 行原始日志，结构化解析 "
                 + str(meta["parsed_records"]) + " 行，解析率 " + ("%.2f%%" % (100 * float(meta["parse_rate"])))
                 + "（自动识别格式 " + str(meta["format"]) + "，编码 " + str(meta["encoding"]) + "）")
    lines.append("- **模板**：" + str(len(templates)) + " 个模板（单例模板 "
                 + str(sum(1 for t in templates if t.count == 1)) + " 个），时间桶宽 "
                 + humanize_seconds(fs.bucket_seconds) + "，共 " + str(fs.n_buckets) + " 个桶")
    lines.append("- **异常**：检出 " + str(len(incidents)) + " 个异常窗口（high " + str(n_high)
                 + " / medium " + str(n_med) + " / low " + str(n_low) + "），单点判定 "
                 + str(len(anomalies)) + " 条，各检测器："
                 + (", ".join(k + "=" + str(v) for k, v in sorted(detector_counts.items())) or "无"))
    if top_incident is not None:
        lines.append("- **最值得先看**：" + top_incident.summary)
    else:
        lines.append("- **最值得先看**：本次运行未触发任何异常窗口（可降低阈值或检查时间桶宽是否过大）")
    lines.append("")

    # ---------------- 数据概览 ----------------
    lines.append("## 1 数据概览")
    lines.append("")
    ts_min = fs.frame["ts"].min() if not fs.frame.empty else None
    ts_max = fs.frame["ts"].max() if not fs.frame.empty else None
    rows = [
        ["文件", source_path, "格式", str(meta["format"]) + "（匹配率 %.3f）" % float(meta["match_rate"])],
        ["原始行数", meta["total_lines"], "空行", meta["blank_lines"]],
        ["解析记录", meta["parsed_records"], "兜底行", meta["fallback_lines"]],
        ["解析率", "%.2f%%" % (100 * float(meta["parse_rate"])), "编码", str(meta["encoding"])],
        ["时间范围", _fmt_ts(ts_min) + " ~ " + _fmt_ts(ts_max), "跨度", humanize_seconds(int(fs.span_seconds))],
        ["时间桶宽", humanize_seconds(fs.bucket_seconds), "桶数", fs.n_buckets],
    ]
    lines.append(table_md(["指标", "值", "指标", "值"], rows))
    lines.append("")
    if fs.synthetic_time:
        lines.append("> 注意：本文件没有任何可解析的时间戳，时间轴按行号构造（synthetic_time=True），"
                     "时序结论只能解释为「行序」而非真实时间。")
        lines.append("")

    level_rows = _level_rows(fs)
    source_rows = _source_rows(fs)
    if level_rows or source_rows:
        lines.append("### 1.1 级别与来源分布")
        lines.append("")
        if level_rows:
            lines.append("**级别分布**")
            lines.append("")
            lines.append(table_md(["级别", "条数", "占比"], level_rows))
            lines.append("")
        if source_rows:
            lines.append("**来源 Top 10（logger / 进程）**")
            lines.append("")
            lines.append(table_md(["来源", "条数", "占比"], source_rows))
            lines.append("")
    lines += chart_md("level", "log level distribution")

    # ---------------- 模板挖掘 ----------------
    lines.append("## 2 模板挖掘（时间/级别/来源/事件形态）")
    lines.append("")
    lines.append(
        "解析层负责把时间、级别、来源抽成字段；模板层负责把正文里的变量（block id、IP、数字、路径…）"
        "掩码后聚类成有限个「事件形态」，这是后续所有统计检验的坐标系。"
    )
    lines.append("")
    lines.append(table_md(
        ["模板", "形态（已掩码）", "次数", "占比", "首次出现", "末次出现", "主要级别"],
        _template_rows(templates, top_templates),
    ))
    lines.append("")
    lines += chart_md("templates", "top templates by frequency")

    # ---------------- 异常检测 ----------------
    lines.append("## 3 异常检测结果")
    lines.append("")
    lines.append("### 3.1 检测器与参数")
    lines.append("")
    lines.append(table_md(["检测器", "方法", "阈值/规则"], _detector_param_rows(config)))
    lines.append("")
    if detector_counts:
        lines.append(table_md(
            ["检测器", "单点判定数"],
            [[k, v] for k, v in sorted(detector_counts.items(), key=lambda kv: -kv[1])],
        ))
        lines.append("")

    lines.append("### 3.2 异常窗口（事件）列表")
    lines.append("")
    if incidents:
        rows = []
        for idx, inc in enumerate(incidents, start=1):
            rows.append([
                "#" + str(idx),
                _fmt_ts(inc.start),
                _fmt_ts(inc.end),
                humanize_seconds(int(inc.duration_seconds)),
                "%.2f" % inc.score,
                inc.severity,
                ",".join(inc.detectors),
                ",".join("T" + str(t) for t in inc.template_ids),
                _truncate(inc.summary, 160),
            ])
        lines.append(table_md(
            ["序号", "开始", "结束", "时长", "分数", "严重度", "检测器", "涉及模板", "摘要"], rows
        ))
    else:
        lines.append("未检出异常窗口。")
    lines.append("")
    lines += chart_md("timeline", "log volume timeline with anomaly windows")

    lines.append("### 3.3 重点异常窗口证据")
    lines.append("")
    if not incidents:
        lines.append("（无）")
        lines.append("")
    for idx, inc in enumerate(incidents[:detail_incidents], start=1):
        lines.append("#### 事件 #" + str(idx) + " ｜ " + _fmt_ts(inc.start) + " ~ " + _fmt_ts(inc.end)
                     + " ｜ 分数 " + ("%.2f" % inc.score) + " ｜ " + inc.severity)
        lines.append("")
        lines.append("**摘要**：" + inc.summary)
        lines.append("")
        lines.append("**判断依据**")
        lines.append("")
        for detail in inc.details[:12]:
            lines.append("- " + detail)
        lines.append("")
        if inc.template_ids:
            lines.append("**涉及模板**")
            lines.append("")
            for tid in inc.template_ids:
                lines.append("- T" + str(tid) + "：" + _truncate(_template_pattern(templates, tid), 140))
            lines.append("")
        if inc.evidence:
            lines.append("**原始日志证据（可回溯到行号）**")
            lines.append("")
            lines.append(FENCE + "text")
            for ev in inc.evidence:
                lines.append("L" + str(ev.line_no) + " | " + _fmt_ts(ev.ts) + " | " + ev.raw)
            lines.append(FENCE)
            lines.append("")

    # ---------------- 方法与局限 ----------------
    lines.append("## 4 方法选择与局限（漏报 / 误报）")
    lines.append("")
    lines.append(
        "本项目采用「模板挖掘 + 统计检验」为主线、关键词规则为补充的混合方案：无需标注数据、"
        "每个判定都能给出统计量与原始日志证据、只依赖 numpy/pandas，适合 5~8 人小团队快速落地。"
        "方法与选型理由见 docs/01-method-research.md。"
    )
    lines.append("")
    lines.append("**已知漏报场景**："
                 "① 日志格式或模板发生演化（同一事件被掩码/分词成新模板）；"
                 "② 异常以少量单条形式出现（计数未达到 min_observed 与 z 阈值）；"
                 "③ 静默失败（系统直接挂掉、日志中断，错误行根本没写出来）；"
                 "④ 规则表未覆盖的故障语义；⑤ 时间桶过宽把突发摊平。")
    lines.append("")
    lines.append("**已知误报场景**："
                 "① 正常业务突增（促销、批处理窗口、集群扩容）；"
                 "② 重启/版本升级带来大批新模板；"
                 "③ 上游依赖抖动导致的连锁重试；"
                 "④ 日志轮转或采样策略变化引起的量级跳变；"
                 "⑤ 规则表过宽的关键词（例如把普通包含 error 字样的行判为错误）。")
    lines.append("")
    lines.append("完整的失效模式、量化评测与缓解手段见 docs/03-false-alarm-analysis.md。")
    lines.append("")

    # ---------------- 复现 ----------------
    lines.append("## 5 复现命令")
    lines.append("")
    lines.append(FENCE + "bash")
    lines.append(
        repro_command
        or ("python -m loglens run -i " + source_path.replace("\\", "/") + " -o results "
            + "--format " + str(meta["format"]))
    )
    lines.append(FENCE)
    lines.append("")
    return "\n".join(lines)


def _template_pattern(templates: Sequence[LogTemplate], tid: int) -> str:
    for tmpl in templates:
        if tmpl.template_id == tid:
            return tmpl.pattern
    return "?"


def write_summary_json(path: str | Path, payload: Dict) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(p)
