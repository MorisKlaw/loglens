# loglens · 让日志开口说话

> 社团考核题《智能运维：让日志开口说话》**技术向**作品：对公开真实系统日志完成
> **解析 → 统计 → 异常检测 → 结果分析**的完整最小实验。
> 数据来自清华 LogPAI 团队维护的 [LogHub](https://github.com/logpai/loghub)（HDFS / Linux / Apache / Zookeeper 四个 2k 行样例）。

复现全部实验（两个命令）：

```bash
python scripts/run_all.py                          # 四个数据集的解析/检测/报告 + 横向对比
python -m loglens bench -o results/bench --repeat 2  # 注入式基准评测（精确率/召回率/F1）
```

---

## 1 这个仓库回答了什么

| 赛题要求 | 本仓库的交付 |
| --- | --- |
| ① 对给定日志完成解析与统计，提炼时间、级别、来源等关键字段 | 四种真实格式解析器 + 格式自动识别；四个数据集解析率均 100%；级别、来源、模板计数全部落成 CSV |
| ② 用规则或统计方法识别至少一类异常，说明异常样本与判断依据 | 5 个检测器（稳健 z-score、泊松尾检验 + BH-FDR、EWMA 控制图、新模板、关键词/级别规则）；每个异常窗口都给出统计量、模板、时间窗与**可回溯行号的原始日志证据** |
| ③ 回答方法在什么情况下会漏报或误报 | docs/03-false-alarm-analysis.md：逐检测器列出失效模式，并附注入式基准的量化结果 |
| ④ 调研至少一种日志异常检测方法并说明选型理由 | docs/01-method-research.md：四类方法族对比表 + 选型理由 + 22 条已核实参考资料 |
| ⑤ 完成最小实验：解析、统计、识别异常并输出一页分析报告 | docs/02-anomaly-report.md（一页分析报告）+ results/&lt;数据集&gt;/report.md（完整可复现报告） |
| ⑥ 提交物：代码、使用说明、开发日志、日程记录、异常检测报告 | 代码 `loglens/` 与 `tests/`；使用说明见本文第 4 节；开发日志 docs/04-dev-log.md；日程记录 docs/05-schedule.md；异常检测报告 docs/02 与 results/ |

## 2 结果速览

### 2.1 四个真实数据集：解析率 100%，检出 99 个异常窗口

| 数据集 | 格式（自动识别） | 行数 | 解析率 | 模板数 | 时间桶 | 异常窗口 | 其中 high |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| HDFS_2k | hdfs | 2000 | 100% | 16 | 10min × 228 | 35 | 5 |
| Linux_2k | linux | 2000 | 100% | 115 | 6h × 173 | 42 | 14 |
| Apache_2k | apache | 2000 | 100% | 6 | 10min × 232 | 14 | 10 |
| Zookeeper_2k | zookeeper | 2000 | 100% | 46 | 3h × 215 | 8 | 6 |

全部窗口都附带「统计量 + 模板 + 时间窗 + 可回溯行号的原始日志证据」。以 HDFS 为例，首个事件是
**2008-11-10 10:30 的块删除风暴**：模板 T6「Deleting block …」在此 10 分钟桶内出现 **65 次**，
历史中位数 0 次、泊松基线 0.87 次/桶（放大约 75 倍），p &lt; 1e-15 且通过 BH-FDR 校正，同时总日志量越出 EWMA 控制限。

![HDFS 日志量时间线与异常窗口](results/HDFS/charts/timeline.svg)

### 2.2 注入式基准：检测能力量化（2k 样例无标注，故自造真值）

对四个数据集各注入 12 个已知异常窗口（burst 模板突增 / novel 新错误模板 / escalation 级别抬升），
2 个随机种子共 8 份注入日志、96 个注入窗口；每份日志用 6 种方法各评测一次（48 次方法评测）。
按「检测窗口与注入窗口时间重叠」判定命中：

| 指标 | 融合方案（默认） | 最佳单检测器 |
| --- | ---: | ---: |
| 窗口层面 精确率 / 召回率 / F1 | 0.408 / **0.844** / **0.528** | 0.382 / 0.688 / 0.464（稳健 z） |
| 单点层面 注入窗口灵敏度 | **0.875** | 0.688（稳健 z） |
| 分类型灵敏度（融合） | burst **1.000** · novel **1.000** · escalation 0.625 | — |

阈值扫描（`--window-threshold`）：0.5 → R 0.875 / F1 0.493；**1.0（默认）→ R 0.844 / F1 0.528（F1 峰值）**；2.0 → R 0.458 / F1 0.384。
想更灵敏就调到 0.5，想更保守就调到 2.0。完整表格与误报样本见 results/bench/bench_report.md。

> 关于"精确率只有 0.41"：未注入任何异常时，同一套检测器在原始日志上平均也会给出 24.75 个窗口——
> 真实日志本身就有大量值得人工看一眼的波动，precision 的分母包含这些自然候选。这不是检测器乱报，
> 而是"候选异常发现器"的固有属性，详见 docs/03-false-alarm-analysis.md 第 3 节。

## 3 方法概览

```text
解析(时间/级别/来源) → Drain 模板挖掘(掩码+定深树) → 时间分桶计数矩阵
  → 稳健 z-score(MAD) ┐
  → 泊松尾检验 + BH-FDR ├→ 桶级加权打分 → 连续异常桶合并为事件 → 带证据的 Markdown 报告
  → EWMA 控制图        │
  → 新模板 / 规则      ┘
```

- **为什么是「模板挖掘 + 统计检验」**：无标注可用、纯 numpy/pandas 即可跑、每个判定都能给出可复核的统计量与原始日志行；深度学习与 LLM 方案的取舍见 docs/01。
- **为什么统计层要三层**：MAD 稳健 z 抗离群点、泊松检验给 p 值并做多重比较校正、EWMA 抓缓慢漂移；三者互补，最后由融合层去重与打分。
- **规则层为什么只做补充**：关键词规则可解释性强但覆盖有限，权重设上限，避免淹没统计信号。
- **为什么有基准评测**：2k 样例没有标注，「报了多少个异常」不能证明准；因此额外做注入式评测，把精确率/召回率/F1 量化出来（见第 2 节与 results/bench）。

## 4 使用说明

### 4.1 环境

- Python 3.10+（已在 3.12 与 3.14 上实测；3.9 未实测，故不声明支持）；运行期只依赖 `numpy` 与 `pandas`，`Pillow` 可选（只影响 PNG 图表，缺失时自动只出 SVG）。
- 从仓库根目录直接运行即可，无需安装；也可以 `pip install -e .` 之后使用 `loglens` 命令。

```bash
python -m pip install -r requirements.txt
```

### 4.2 常用命令

```bash
# 完整流水线：解析 -> 模板 -> 检测 -> 报告（产物在 results/HDFS）
python -m loglens run -i data/HDFS_2k.log -o results/HDFS

# 对 data/ 下四个样例全部跑一遍，并生成横向对比
python scripts/run_all.py

# 只做解析（看解析统计，可导出 CSV）
python -m loglens parse -i data/Zookeeper_2k.log --json
python -m loglens parse -i data/Zookeeper_2k.log --out zk_parsed.csv

# 注入式基准评测：精确率 / 召回率 / F1 + 阈值扫描
python -m loglens bench -o results/bench --repeat 2

# 单元测试（50 个用例）
python -m loglens selftest
```

### 4.3 常用参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| --format | auto | auto / hdfs / linux / apache / zookeeper / generic |
| --bucket | 自动 | 时间桶宽（秒）；自动模式按跨度在 1s~1d 中就近取值，目标桶数 240 |
| --target-buckets | 240 | 自动分桶的目标桶数 |
| --z-threshold | 5.0 | 稳健 z-score 阈值（调小更敏感） |
| --window-threshold | 1.0 | 异常窗口分数阈值（调大更保守，见第 2 节阈值扫描） |
| --fdr-q | 0.05 | 泊松检验的 BH-FDR 控制水平 |
| --no-rules / --no-ewma / --no-novelty / --no-z / --no-poisson | — | 关闭对应检测器（消融实验用） |
| --no-parsed | — | 不写逐行 CSV（大日志省空间） |

### 4.4 产物说明

每个输出目录包含：report.md（分析报告）、parsed.csv（逐行解析结果）、templates.csv（模板表）、
anomalies.csv（单点判定）、windows.csv（逐桶分数）、incidents.csv（异常窗口）、summary.json（机器可读汇总）、
charts/（时间线、模板频次、级别分布；SVG + PNG）。完整说明见 results/README.md。

## 5 目录结构

```text
loglens/
  loglens/            核心包：解析 / 模板 / 特征 / 检测 / 融合 / 报告 / 基准 / CLI
  tests/              50 个单元测试（标准库 unittest，无第三方测试依赖）
  data/               LogHub 四个 2k 样例 + 数据来源与引用说明
  docs/               00 数据集 · 01 方法调研 · 02 一页异常报告 · 03 漏报误报分析
                      04 开发日志 · 05 日程记录 · 06 设计说明
  results/            四个数据集的运行产物 + 注入式基准评测结果（已提交，作为实验证据）
  scripts/run_all.py  一键复现脚本
  Makefile            常用命令封装
```

## 6 复现性说明

- 所有阈值集中在 `loglens/detectors/base.py` 的 `DetectConfig`，并写入每次运行的 `summary.json`，参数可追溯。
- 解析口径可追溯：未识别的行不会被丢弃，而是标记 fallback 并沿用上一行时间戳；解析率写入报告。
- 基准评测固定随机种子，注入后的日志与真值标签一并保存在 `results/bench/injected/`。
- 图表失败不影响主流程（异常被捕获），保证报告一定生成。
- 速度（实测值随机器与解释器浮动）：单个 2000 行样例跑完「解析 → 模板 → 检测 → 图表 → 报告」约 3 秒量级（Python 3.14 实测 2.7~3.9 秒，Python 3.12 约 3.2~3.5 秒）；50 个单元测试约 2~5 秒。

## 7 已知局限（详见 docs/03）

1. 规则表按常见故障语义手工维护，换系统需重新整理；
2. 2k 样例跨度大（Linux 样例覆盖 44 天），桶宽较粗时会钝化短时突发；
3. 有序序列异常（模板顺序错乱但计数正常）当前未覆盖；
4. 静默失败（进程直接挂掉、日志中断）没有日志可分析，属于方法边界之外；
5. 按绝对计数检验，遇到全局流量水平漂移时会把整段时间标红，生产化应改为比较「模板占比」（实测数据见 docs/03 第 3.4 节）。

## 8 数据与引用

日志样例来自 LogHub（清华 LogPAI 团队），仅用于研究与学习，使用请署名并引用：

- Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics.* ISSRE 2023.
- 使用 HDFS_v1 时另需引用 Wei Xu et al., *Detecting Large-Scale System Problems by Mining Console Logs*, SOSP 2009.

方法与工具的完整参考列表见 docs/01-method-research.md 第 6 节。

## 9 许可

代码以 MIT 许可开源（见 LICENSE）；`data/` 下日志版权归原作者，遵循 LogHub 的使用要求。
