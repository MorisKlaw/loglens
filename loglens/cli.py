"""命令行入口：run / parse / bench / selftest / version。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .parsers import parse_file
from .pipeline import RunConfig, describe, run
from .detectors.base import DetectConfig


def _add_detect_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("检测阈值（默认值即报告中所用参数）")
    group.add_argument("--z-threshold", type=float, default=5.0, help="稳健 z-score 触发阈值，默认 5.0")
    group.add_argument("--min-observed", type=int, default=3, help="触发所需的最小观测次数，默认 3")
    group.add_argument("--poisson-alpha", type=float, default=0.01, help="泊松检验粗筛显著性，默认 0.01")
    group.add_argument("--fdr-q", type=float, default=0.05, help="BH-FDR 控制的 q 值，默认 0.05")
    group.add_argument("--ewma-L", type=float, default=4.0, help="EWMA 控制限倍数，默认 4.0")
    group.add_argument("--window-threshold", type=float, default=1.0, help="异常窗口分数阈值，默认 1.0")
    group.add_argument("--no-rules", action="store_true", help="关闭关键词/级别规则检测器")
    group.add_argument("--no-novelty", action="store_true", help="关闭新模板检测器")
    group.add_argument("--no-ewma", action="store_true", help="关闭 EWMA 检测器")
    group.add_argument("--no-poisson", action="store_true", help="关闭泊松检测器")
    group.add_argument("--no-z", action="store_true", help="关闭稳健 z-score 检测器")


def _detect_config(args: argparse.Namespace) -> DetectConfig:
    return DetectConfig(
        z_threshold=args.z_threshold,
        min_observed=args.min_observed,
        poisson_alpha=args.poisson_alpha,
        fdr_q=args.fdr_q,
        ewma_L=args.ewma_L,
        window_threshold=args.window_threshold,
        enable_rules=not args.no_rules,
        enable_novelty=not args.no_novelty,
        enable_ewma=not args.no_ewma,
        enable_poisson=not args.no_poisson,
        enable_z=not args.no_z,
    )


def cmd_run(args: argparse.Namespace) -> int:
    cfg = RunConfig(
        input_path=args.input,
        out_dir=args.out,
        fmt=args.format,
        syslog_year=args.syslog_year,
        bucket_seconds=args.bucket,
        target_buckets=args.target_buckets,
        top_templates=args.top,
        detail_incidents=args.detail,
        title=args.title,
        write_parsed=not args.no_parsed,
        charts=not args.no_charts,
        png=not args.no_png,
        detect=_detect_config(args),
    )
    result = run(cfg)
    print(describe(result))
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    result = parse_file(args.input, fmt=args.format, syslog_year=args.syslog_year)
    summary = result.summary()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for key, value in summary.items():
            print(str(key).ljust(16) + ": " + str(value))
        print("")
        print("前 5 条解析结果：")
        for rec in result.records[:5]:
            print(
                "  L" + str(rec.line_no) + " | " + str(rec.ts) + " | " + str(rec.level) + " | "
                + str(rec.source) + " | " + rec.message[:80]
            )
    if args.out:
        import pandas as pd

        df = pd.DataFrame([r.to_row() for r in result.records])
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print("已写出: " + str(args.out) + " (" + str(len(df)) + " 行)")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from .bench import benchmark

    report = benchmark(
        out_dir=args.out,
        base_files=args.base,
        n_windows=args.windows,
        seed=args.seed,
        repeat=args.repeat,
        png=not args.no_png,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("")
    print("基准评测报告: " + report["report_path"])
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    import unittest

    root = Path(__file__).resolve().parent.parent
    tests_dir = root / "tests"
    loader = unittest.TestLoader()
    if tests_dir.is_dir():
        suite = loader.discover(str(tests_dir), top_level_dir=str(root))
    else:
        print("未找到 tests/ 目录")
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loglens",
        description="loglens - 让日志开口说话：轻量级日志解析、模板挖掘与异常检测",
    )
    parser.add_argument("--version", action="version", version="loglens " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="完整流水线：解析 -> 模板 -> 检测 -> 报告")
    p_run.add_argument("-i", "--input", required=True, help="输入日志文件")
    p_run.add_argument("-o", "--out", required=True, help="输出目录")
    p_run.add_argument("--format", default="auto", choices=["auto", "hdfs", "linux", "apache", "zookeeper", "generic"])
    p_run.add_argument("--syslog-year", type=int, default=2005, help="syslog 无年份时的基准年")
    p_run.add_argument("--bucket", type=int, default=None, help="时间桶宽（秒），默认自动")
    p_run.add_argument("--target-buckets", type=int, default=240, help="自动分桶的目标桶数")
    p_run.add_argument("--top", type=int, default=15, help="报告中展示的模板条数")
    p_run.add_argument("--detail", type=int, default=5, help="报告中展开证据的事件数")
    p_run.add_argument("--title", default=None, help="报告标题")
    p_run.add_argument("--no-parsed", action="store_true", help="不写 parsed.csv（大文件时省空间）")
    p_run.add_argument("--no-charts", action="store_true", help="不生成图表")
    p_run.add_argument("--no-png", action="store_true", help="只生成 SVG，不生成 PNG")
    _add_detect_args(p_run)
    p_run.set_defaults(func=cmd_run)

    p_parse = sub.add_parser("parse", help="只做解析与统计")
    p_parse.add_argument("-i", "--input", required=True)
    p_parse.add_argument("--format", default="auto", choices=["auto", "hdfs", "linux", "apache", "zookeeper", "generic"])
    p_parse.add_argument("--syslog-year", type=int, default=2005)
    p_parse.add_argument("--out", default=None, help="把解析结果写到 CSV")
    p_parse.add_argument("--json", action="store_true", help="以 JSON 输出统计")
    p_parse.set_defaults(func=cmd_parse)

    p_bench = sub.add_parser("bench", help="注入式基准评测：精确率 / 召回率 / F1")
    p_bench.add_argument("-o", "--out", default="results/bench")
    p_bench.add_argument("--base", nargs="*", default=None, help="基准日志文件（默认用 data/ 下的四个样例）")
    p_bench.add_argument("--windows", type=int, default=12, help="每个文件注入的异常窗口数")
    p_bench.add_argument("--seed", type=int, default=42)
    p_bench.add_argument("--repeat", type=int, default=1, help="重复次数（多种子平均）")
    p_bench.add_argument("--no-png", action="store_true")
    p_bench.set_defaults(func=cmd_bench)

    p_test = sub.add_parser("selftest", help="运行单元测试")
    p_test.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None) -> int:
    # Windows 控制台默认可能是 GBK，这里统一成 UTF-8，避免中文报告输出乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
