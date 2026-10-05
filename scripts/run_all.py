#!/usr/bin/env python
"""一键复现：对 data/ 下的全部 LogHub 样例跑完整流水线，并打印横向对比。

用法（在仓库根目录执行）::

    python scripts/run_all.py
    python scripts/run_all.py --bucket 60 --out results

产物：每个数据集一个子目录（report.md / *.csv / charts/），外加 results/comparison.md。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loglens.pipeline import RunConfig, run  # noqa: E402
from loglens.report import table_md  # noqa: E402

DATASETS = (
    ("data/HDFS_2k.log", "HDFS"),
    ("data/Linux_2k.log", "Linux"),
    ("data/Apache_2k.log", "Apache"),
    ("data/Zookeeper_2k.log", "Zookeeper"),
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="对全部样例日志跑一遍 loglens 并汇总对比")
    parser.add_argument("--out", default="results", help="输出根目录，默认 results")
    parser.add_argument("--bucket", type=int, default=None, help="固定时间桶宽（秒），默认自动")
    parser.add_argument("--target-buckets", type=int, default=240)
    parser.add_argument("--no-parsed", action="store_true", help="不写逐行 CSV")
    args = parser.parse_args(argv)

    rows = []
    summaries = {}
    for rel, name in DATASETS:
        src = ROOT / rel
        if not src.exists():
            print("跳过（文件不存在）: " + rel)
            continue
        out_dir = ROOT / args.out / name
        try:
            shown = str(out_dir.relative_to(ROOT))
        except ValueError:
            shown = str(out_dir)  # --out 指向仓库外时按绝对路径显示
        print("=" * 72)
        print("处理 " + rel + " -> " + shown)
        result = run(
            RunConfig(
                input_path=str(src),
                out_dir=str(out_dir),
                fmt="auto",
                bucket_seconds=args.bucket,
                target_buckets=args.target_buckets,
                write_parsed=not args.no_parsed,
            )
        )
        meta = result.parse.summary()
        rows.append(
            [
                name,
                meta["total_lines"],
                "%.2f%%" % (100 * float(meta["parse_rate"])),
                len(result.templates),
                len(result.anomalies),
                len(result.incidents),
                result.incidents[0].summary if result.incidents else "-",
            ]
        )
        summaries[name] = {
            "input": rel,
            "parse": meta,
            "n_templates": len(result.templates),
            "n_anomalies": len(result.anomalies),
            "n_incidents": len(result.incidents),
            "detectors": sorted({a.detector for a in result.anomalies}),
        }
        print("  行数 %s，解析率 %.2f%%，模板 %d，异常窗口 %d"
              % (meta["total_lines"], 100 * float(meta["parse_rate"]), len(result.templates), len(result.incidents)))

    out_root = ROOT / args.out
    out_root.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 全数据集实验对比",
        "",
        "> 生成时间 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "；命令 python scripts/run_all.py",
        "",
        table_md(["数据集", "行数", "解析率", "模板数", "单点判定", "异常窗口", "首要窗口摘要"], rows),
        "",
        "各数据集细节见 results/<数据集>/report.md。",
        "",
    ]
    (out_root / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    (out_root / "comparison.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("=" * 72)
    print("汇总: " + str(out_root / "comparison.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
