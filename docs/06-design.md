# 系统设计与实现说明

本文说明 loglens 的整体结构、关键数据结构、检测器公式与参数默认值，便于评委快速读懂代码，也便于后续扩展。

## 1 设计目标与约束

| 约束 | 具体内容 | 设计对策 |
| --- | --- | --- |
| 无标注数据 | LogHub 2k 样例没有人工标签 | 全部检测器均为无监督；另外用「注入式基准」自造标签做量化评测 |
| 轻量可复现 | 评委机器上一条命令要能跑通 | 运行期只依赖 numpy 与 pandas；绘图自研（SVG 必出，PNG 可选）；单元测试用标准库 unittest |
| 结果可解释 | 每条告警要能落到原始日志行 | 所有判定都携带统计量、模板 ID、时间桶与原始行号证据 |
| 小团队可维护 | 5~8 人、预算有限 | 模块职责单一、参数集中在一处、无外部服务依赖，单机即可跑 |

## 2 总体架构

    原始日志文件
        |
        v
    [1] 解析层 loglens/parsers.py      正则抽取 时间/级别/来源/进程/PID，auto 自动识别格式
        |
        v
    [2] 模板层 loglens/templates.py   掩码(IP/块ID/数字/路径/UUID/十六进制) + Drain 定深解析树聚类
        |
        v
    [3] 特征层 loglens/features.py     自动分桶(60~400 桶) -> 模板计数矩阵 / 级别矩阵 / 来源矩阵
        |
        v
    [4] 检测层 loglens/detectors/      规则 | 稳健z | 泊松FDR | EWMA控制图 | 新模板
        |
        v
    [5] 融合层 ensemble.py             桶级加权打分 -> 连续异常桶合并为事件(Incident)
        |
        v
    [6] 展示层 report.py / visualize.py  Markdown 报告 + CSV + SVG/PNG 图表
        |
        v
    [7] 编排层 pipeline.py / cli.py     一次 run 产出全部产物；bench.py 做量化评测

## 3 模块清单

| 文件 | 职责 | 关键对象 |
| --- | --- | --- |
| loglens/schema.py | 数据模型与级别定义 | LogRecord、LEVEL_RANK、infer_level |
| loglens/parsers.py | 四种真实格式 + 通用兜底 + 自动识别 | HDFSParser、LinuxParser、ApacheParser、ZookeeperParser、ParseResult |
| loglens/templates.py | 掩码与 Drain 模板挖掘 | Drain、LogTemplate、TemplateMiner |
| loglens/features.py | 时间分桶与计数矩阵 | FeatureSet、build_features、choose_bucket_seconds |
| loglens/detectors/base.py | 检测器契约与证据抽取 | Anomaly、DetectConfig、DetectContext、Evidence |
| loglens/detectors/rules.py | 级别规则 + 关键词规则 | Rule、KEYWORD_RULES |
| loglens/detectors/robust_z.py | 模板计数突增(MAD) | RobustZDetector |
| loglens/detectors/poisson.py | 泊松上尾检验 + BH-FDR | PoissonDetector、poisson_sf、benjamini_hochberg |
| loglens/detectors/ewma.py | 量级/错误量控制图 | EwmaDetector、ewma_flags |
| loglens/detectors/novelty.py | 观察期后首次出现的新模板 | NoveltyDetector |
| loglens/detectors/ensemble.py | 融合打分与事件合并 | fuse、Incident |
| loglens/visualize.py | 自研绘图(Canvas -> SVG/PNG) | Canvas、timeline_chart、template_bar_chart |
| loglens/report.py | Markdown 报告渲染 | render_report、table_md |
| loglens/pipeline.py | 流水线编排与产物落盘 | RunConfig、RunResult、run、analyze_records |
| loglens/bench.py | 注入式基准评测 | build_injected_log、evaluate_incidents、benchmark |
| loglens/cli.py | 命令行入口 | run / parse / bench / selftest |

## 4 关键数据结构

- LogRecord：一条解析后的日志（行号、原始行、正文、时间、级别、来源、PID、主机、模板 ID、模板、附加字段）。
- LogTemplate：一个模板（模板 ID、掩码后的 token 序列、计数、首末出现时间、来源分布、级别分布、样本行）。
- Anomaly：单点判定（检测器、类型、严重度、分数、时间桶、对象、判定依据、观测值/期望值/p 值、原始日志证据）。
- Incident：一段连续异常窗口（起止、最高分、严重度、涉及检测器、涉及模板、摘要、判定依据列表、原始日志证据）。

## 5 检测器公式与判据

### 5.1 稳健 z-score（模板计数突增）

    sigma = 1.4826 * MAD(counts)
    z     = (x - median) / sigma
    判定：z >= 5.0 且 x >= max(3, median + 3)
    sigma = 0 时退化为泊松尺度 sigma = sqrt(median)，避免除零放大噪声

### 5.2 泊松上尾检验（低频模板显著性 + 多重比较校正）

    lambda = (该模板总次数 - 本桶次数) / (桶数 - 1)
    p      = P(X >= x),  X ~ Poisson(lambda)     (对数空间累加，避免下溢)
    粗筛 p <= 0.01，再对全部候选做 Benjamini-Hochberg FDR 控制 (q = 0.05)
    忽略 lambda < 0.05 的模板（它们交给新模板检测器，避免重复信号）

### 5.3 EWMA 控制图（量级/错误量突变）

    m_t = lam * x_t + (1 - lam) * m_{t-1}            lam = 0.25
    v_t = lam * (x_t - m_{t-1})^2 + (1 - lam) * v_{t-1}
    越限：|x_t - m_{t-1}| > L * sqrt(v_{t-1})        L = 4.0
    序列：总日志量（允许下降）、错误量、告警量（只报上升）

### 5.4 新模板检测（novelty）

    观察期 = 前 10% 时间桶；观察期之后首次出现的模板判为新模板
    普通新模板权重 0.6（需其它信号印证才成窗）；错误级新模板权重 1.2 且严重度 high

### 5.5 规则检测（补充层）

    关键词规则 9 条（认证失败、未知用户、段错误、mod_jk、异常堆栈、IO 错误、磁盘、OOM、HDFS 副本、ZK 选主）
    + 级别规则（ERROR/FATAL 级日志出现即记录）
    每条规则可配置最小命中次数；同一规则按时间桶分别记录，最多取前 8 个桶

## 6 融合打分规则

| 信号 | 权重 | 封顶 |
| --- | --- | --- |
| 同一模板被 z-score / 泊松同时命中 | 取较高者 1.0 / 0.9，按模板去重 | 每桶 3.0 |
| 新模板 | 0.6（普通）/ 1.2（错误级） | 每桶 3.0 |
| 规则命中 | 0.4 / 0.6 / 0.75（按严重度） | 每桶 1.2 |
| EWMA 越限 | 0.5（量级下降）/ 0.6（量级上升）/ 0.9（错误量上升） | 每序列每桶 1 次 |
| 总分 | 各类相加 | 全局 10.0 |

桶分数 >= 1.0（window_threshold，可调）判为异常桶，连续异常桶合并成一个事件。

## 7 默认参数一览

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| target_buckets | 240 | 自动分桶的目标桶数（实际桶宽从 1s/2s/5s/.../1d 中就近选择） |
| z_threshold | 5.0 | 稳健 z-score 触发阈值 |
| min_observed | 3 | 触发所需最小观测次数 |
| poisson_alpha / fdr_q | 0.01 / 0.05 | 泊松粗筛与 FDR 控制 |
| ewma_lambda / ewma_L | 0.25 / 4.0 | EWMA 平滑系数与控制限倍数 |
| novelty_warmup_frac | 0.10 | 新模板检测的观察期占比 |
| window_threshold | 1.0 | 异常窗口分数阈值 |
| 模板挖掘 | depth=4, sim_th=0.4, max_children=100 | Drain 参数 |

## 8 扩展点

1. 新增日志格式：在 parsers.py 增加一个继承 BaseParser 的类并注册到 PARSERS，auto 识别会自动纳入比较。
2. 新增检测器：实现 detect(ctx) -> List[Anomaly]，在 detectors/__init__.py 的 default_detectors 中登记；融合层无需改动。
3. 接入实时流：Drain 与 EWMA 都是在线增量算法，把 pipeline.analyze_records 换成滑动窗口调用即可。
4. 接大模型做根因叙述：在 report.py 的证据段落之后追加调用，不影响判定链路（保持可复现）。

## 9 工程与复现约定

- 参数全部落在 summary.json（detection.config），配合固定种子即可复现实验。
- 解析口径可追溯：未识别的行标为 fallback 并沿用上一行时间戳，不静默丢数据。
- 图表失败不影响主流程（try/except 包裹），保证报告一定生成。
- 单元测试 45 个用例覆盖解析、模板、检测、融合、注入与评测逻辑，命令：python -m loglens selftest
