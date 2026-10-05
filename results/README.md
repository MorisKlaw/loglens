# 运行产物说明

本目录保存可复现的实验证据：四个 LogHub 样例的完整运行产物，以及注入式基准评测结果。
所有文件都可以用仓库根目录下的一条命令重新生成：

```bash
python scripts/run_all.py                              # 重新生成 results/<数据集>/
python -m loglens bench -o results/bench --repeat 2    # 重新生成 results/bench/
```

## 1 每个数据集目录（results/HDFS、results/Linux、results/Apache、results/Zookeeper）

| 文件 | 内容 |
| --- | --- |
| report.md | 分析报告：数据概览 / 模板表 / 异常窗口列表 / 重点窗口的判定依据与原始日志证据 |
| parsed.csv | 逐行结构化结果（行号、时间、级别、来源、PID、模板 ID、模板、正文、原始行） |
| templates.csv | 模板表（掩码后的形态、次数、通配符数、首末出现、主要级别、样本行） |
| anomalies.csv | 单点判定明细（检测器、类型、严重度、分数、时间桶、统计量与判定依据、证据行） |

> 说明：CSV 一律以 `utf-8-sig`（带 BOM）写出，方便 Excel 直接双击打开；用 pandas 读取请写 `pd.read_csv(path, encoding="utf-8-sig")`。
> evidence 字段形如 `L426 | 2008-11-10 10:32:01 | <原始日志行>`，其中的 `L426` 就是源文件行号，可直接回溯。
| windows.csv | 逐时间桶分数（用于画时间线、复核阈值） |
| incidents.csv | 异常窗口（事件）汇总：起止、时长、分数、涉及检测器与模板、摘要、证据 |
| summary.json | 机器可读汇总（解析统计、模板 Top10、各检测器命中数、阈值配置） |
| charts/ | 时间线（含异常标记）、模板频次 Top15、级别分布；SVG + PNG 两种格式 |

## 2 基准评测目录（results/bench）

| 文件 | 内容 |
| --- | --- |
| bench_report.md | 评测报告：设置、各方法 P/R/F1、单点灵敏度、阈值扫描、背景检出、典型误报样本 |
| bench_metrics.csv | 明细（文件 × 种子 × 方法）：TP/FP/FN、P/R/F1、分类型召回 |
| bench_detector_metrics.csv | 单点层面的检测器消融指标（灵敏度 / precision-like / 分类型灵敏度） |
| bench_threshold_sweep.csv | 融合阈值扫描明细 |
| bench_background.csv | 未注入异常时的检出数量（噪声地板参考） |
| injected/ | 注入后的日志文件与真值标签（可复现、可人工核对） |
| charts/ | 各方法 P/R/F1 对比图 |

## 3 阅读顺序建议

1. 先看 results/HDFS/report.md 第 0 节结论速览与第 3.3 节证据；
2. 再看 results/bench/bench_report.md 了解检测能力的量化边界；
3. 最后看 docs/02-anomaly-report.md（一页分析报告）与 docs/03-false-alarm-analysis.md（漏报误报分析）。
