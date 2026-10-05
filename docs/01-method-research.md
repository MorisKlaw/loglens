# 日志异常检测方法调研

> 面向《智能运维：让日志开口说话》技术作品的方法选型说明。数据为清华 LogPAI 维护的 LogHub 公开日志，仓库内置 HDFS、Linux、Apache、Zookeeper 四个 2k 行样例。

## 1 问题定义

日志是系统状态最完整也最廉价的一手记录：进程启停、块读写、会话超时、异常重试都会留下痕迹。日志异常检测要在**没有人工标注**的前提下，从持续产生的日志流中定位与历史常态显著偏离的时间片段，并说清偏离发生在哪个组件、哪类事件、幅度多大。

它可拆成三问：**解析**（自由文本变成模板加参数）、**建模**（刻画"正常"）、**判定**（偏离度量与判异规则）。

人工逐条看不现实。**规模**：LogHub 中 HDFS_v1 完整版有 1117 万行、1.47 GiB，Zookeeper 有 7.4 万行，仓库样例仅 2k 行。**时延**：故障定位以分钟计，人工排查跟不上。**认知**：人对低频但致命的事件（如偶发的块校验失败）不敏感，却容易被高频噪声耗尽注意力，最终退化成"只看 ERROR 关键字"，恰好漏掉级别正常、但频次或顺序异常的故障前兆。

所以可交付的不是异常分数，而是**一条可复现的证据链**：判据所用的统计量、触发的模板、时间窗口与原始日志行。

## 2 方法族梳理

### 2.1 日志解析 / 模板挖掘类

**原理**：日志等于常量骨架加变量槽位。解析器把每条消息映射为模板 ID 与参数，后续分析都建立在模板序列上。Drain 用固定深度解析树：按 token 数分层、按首 token 分桶，叶节点内以"相同 token 比例"匹配，超阈值则归入、否则新建；树深固定使单条处理代价与模板规模近似无关。

**代表工作**：Drain（He 等，ICWS 2017）；工程化后继 Drain3，支持流式处理、状态持久化与掩码预处理。

**优点**：无监督、可在线增量更新；复杂度低；模板本身人可读。

**局限**：解析质量是链路上限，模板切错则下游再精确也无意义；相似度阈值、树深、掩码需按格式调参；它只做压缩，不产生判定。

### 2.2 统计与不变量类

**原理**：把"模板乘时间窗"的计数向量视作时间序列或矩阵，用显式统计模型刻画正常基线并据此判异。

**代表工作**：Xu 等（SOSP 2009）把消息计数投影到 PCA 主子空间，用重构残差 SPE 衡量偏离；Lou 等（USENIX ATC 2010）挖掘模板计数间满足稳定线性关系的"不变量"，关系被破坏即判异常。

**优点**：无需标注，训练代价低；判据是具体统计量。

**局限**：需一段干净历史作基线，冷启动误报偏多；不建模周期性与突发流量则误报上升；只看计数会丢顺序语义，A→B→C 与 C→B→A 计数相同。

### 2.3 深度学习类

**原理**：在模板 ID 序列上训练序列模型，学习"正常的下一条模板"或正常序列的表示，以预测概率低、重构误差大作为异常信号。

**代表工作**：DeepLog（Du 等，CCS 2017）用 LSTM 预测下一条模板，辅以参数值与工作流检测；LogAnomaly（Meng 等，IJCAI 2019）用 template2vec 处理语义相近的模板；LogBERT（Guo 等，IJCNN 2021）用 BERT 掩码语言建模学习正常序列表示。

**优点**：能建模长程依赖与语义相似，在带标注数据集上通常取得更高 F1。

**局限**：无标注时只能单类建模，对训练数据纯净度极敏感，混入的异常会被当作正常学进去；Le 与 Zhang（ICSE 2022）的复现表明它相对传统方法并非稳定胜出，且高度依赖解析器选择；另有 GPU 与调参成本，输出难对应到具体日志行。

### 2.4 大模型 / LLM 类

**原理**：把日志或模板序列放进上下文，用预训练模型的语义理解与推理能力做零样本判定、根因叙述或模板生成。

**代表工作**：LILAC（Jiang 等，FSE 2024）用 LLM 解析日志并维护自适应缓存；Liu 等（ICPC 2024）以提示词策略做在线日志分析与解释；LogGPT（Han 等，2023）用 GPT 类模型做序列异常检测。

**优点**：零样本、免训练，输出即自然语言解释。

**局限**：按条调用成本与延迟可观，上下文长度决定它覆盖不了全量日志；输出不稳定、有幻觉，同一输入未必同一结论；日志出域还牵涉合规。这与可复现证据链冲突。

## 3 方法对比表

| 方法族 | 代表工作 | 是否需要标注 | 可解释性 | 资源/运维成本 | 冷启动难度 | 与本场景匹配度 |
| --- | --- | --- | --- | --- | --- | --- |
| 解析 / 模板挖掘 | Drain、Drain3 | 不需要 | 高，模板即解释 | 极低，纯 CPU 流式 | 低，可在线构建 | 高，全链路地基 |
| 统计与不变量 | Xu（SOSP 2009）、Lou（ATC 2010） | 不需要 | 高，统计量可回溯 | 低，numpy 与 pandas | 中，需干净历史 | 高，主线判异层 |
| 深度学习 | DeepLog、LogAnomaly、LogBERT | 训练不要，评测要 | 低，只有分数 | 高，需 GPU 与调参 | 高，2k 行无法训练 | 低，本次不采用 |
| 大模型 / LLM | LILAC、LogPrompt、LogGPT | 不需要 | 中，自然语言但不稳定 | 中高，计费且有延迟 | 低 | 中，后续解释增强 |

## 4 选型理由

**主线为何是"模板挖掘加统计检验"。** 无标注意味着任何"学习"都要依赖紧凑、可复现的表示。模板把每条日志降维成（时间、级别、来源、模板 ID）四元组，既压缩数据，又保留回溯到原始行的能力，这是"给证据"的前提。判定层不拍阈值，而让三层统计量各管一类偏差：

- **鲁棒 z-score（中位数加 MAD）**：日志计数重尾，均值与标准差会被异常本身带偏，MAD 给出不被离群点污染的散布估计（Leys 等，2013），回答"是否离群"。
- **泊松尾检验加 BH 校正**：把每分钟计数视为泊松计数，算"至少出现这么多次"的尾部概率；由于要对模板乘时间桶做成千上万次检验，必须做多重比较校正，否则按 5% 显著性水平几乎每分钟都会凭空检出假异常（Benjamini 与 Hochberg，1995）。
- **EWMA 方差控制图**：对内存泄漏、连接数爬升这类缓慢漂移敏感，弥补泊松检验只盯单点、对逐步恶化不敏感的缺陷（Roberts，1959）。

三层都是闭式或线性代价公式，纯 numpy 可算；每条告警都能落到具体的模板、分钟、偏离量与 p 值。

**规则与新奇检测为何只做补充。** 级别聚集、已知故障关键词与首次出现的新模板都是低成本先验，召回好但精确率不稳，故不参与显著性判定，只作为独立条目与统计信号并列进入证据列表，由融合层合成异常窗口：既保留领域知识，又不污染统计口径。

**深度学习为何不做主线。** 四条约束同时成立：无标注只能单类建模，而每个数据集仅 2k 行样例，不足以训练与验证序列模型；黑盒分数无法对应证据行；仓库只声明 numpy 与 pandas 两个运行依赖，引入 PyTorch 会让评委机器上的复现成本陡增；5 至 8 人小队在考核周期内跑完训练与调参的闭环并不现实。

**LLM 为何也不做主线。** 除成本与延迟，更关键的是不可复现：同样输入在不同时刻可能得到不同判断，而考核与复盘都需要可重复的判据。

**后续增强路径。** 基线稳定后，可用 LLM 离线为模板生成中文语义标签、对已检出窗口做根因叙述、扩展故障关键词同义表达；若日后积累人工复盘标签，再引入序列模型作为第四路信号。这些增强都在判定之后，不破坏主线的可复现性。

## 5 参考实现与先例

- **LogPAI benchmark（loglizer / logparser）**：学术界统一的解析与检测评测口径，提供 Drain、PCA、不变量挖掘等参考实现，是本项目的对照基线。
- **Drain3**：Drain 的流式工程化实现，带状态持久化与掩码，是本项目自研模板挖掘的算法参照。
- **ELK（Elastic Stack）**：以采集、存储、检索、可视化为中心的日志平台，告警靠自编查询与阈值规则，不内置无监督评分。
- **Grafana Loki**：以标签索引和低成本存储为卖点的日志聚合系统，异常判定交给下游告警或外部算法。

## 6 参考资料

1. Drain: An Online Log Parsing Approach with Fixed Depth Tree. P. He, J. Zhu, Z. Zheng, M. R. Lyu. ICWS 2017: 33-40. https://doi.org/10.1109/ICWS.2017.13
2. Drain3: A robust streaming log template miner based on the Drain algorithm. LogPAI（原 IBM 开源，仓库已迁至 LogPAI 组织）. 2020. https://github.com/logpai/Drain3
3. Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics. J. Zhu, S. He, P. He, J. Liu, M. R. Lyu. ISSRE 2023. https://arxiv.org/abs/2008.06448
4. LogHub 数据集仓库. LogPAI，清华大学. https://github.com/logpai/loghub
5. Experience Report: System Log Analysis for Anomaly Detection. S. He, J. Zhu, P. He, M. R. Lyu. ISSRE 2016: 207-218. https://doi.org/10.1109/ISSRE.2016.21
6. Tools and Benchmarks for Automated Log Parsing. J. Zhu et al. ICSE-SEIP 2019: 121-130. https://doi.org/10.1109/ICSE-SEIP.2019.00021
7. loglizer: A machine learning toolkit for log-based anomaly detection. LogPAI. https://github.com/logpai/loglizer
8. logparser: A machine learning toolkit for log parsing. LogPAI. https://github.com/logpai/logparser
9. Detecting Large-Scale System Problems by Mining Console Logs. W. Xu, L. Huang, A. Fox, D. A. Patterson, M. I. Jordan. SOSP 2009. https://doi.org/10.1145/1629575.1629587
10. Mining Invariants from Console Logs for System Problem Detection. J.-G. Lou, Q. Fu, S. Yang, Y. Xu, J. Li. USENIX ATC 2010. https://www.usenix.org/conference/usenix-atc-10/mining-invariants-console-logs-system-problem-detection
11. DeepLog: Anomaly Detection and Diagnosis from System Logs through Deep Learning. M. Du, F. Li, G. Zheng, V. Srikumar. CCS 2017: 1285-1298. https://doi.org/10.1145/3133956.3134015
12. LogAnomaly: Unsupervised Detection of Sequential and Quantitative Anomalies in Unstructured Logs. W. Meng, Y. Liu, Y. Zhu, S. Zhang, D. Pei, et al. IJCAI 2019: 4739-4745. https://www.ijcai.org/proceedings/2019/658
13. LogBERT: Log Anomaly Detection via BERT. H. Guo, S. Yuan, X. Wu. IJCNN 2021. https://doi.org/10.1109/IJCNN52387.2021.9534113
14. Log-based Anomaly Detection with Deep Learning: How Far Are We? V.-H. Le, H. Zhang. ICSE 2022. https://arxiv.org/abs/2202.04301
15. LILAC: Log Parsing using LLMs with Adaptive Parsing Cache. Z. Jiang et al. FSE 2024. https://arxiv.org/abs/2310.01796
16. Interpretable Online Log Analysis Using Large Language Models with Prompt Strategies. Y. Liu et al. ICPC 2024. https://arxiv.org/abs/2308.07610
17. LogGPT: Log Anomaly Detection via GPT. X. Han, S. Yuan, M. Trabelsi. arXiv preprint, 2023. https://arxiv.org/abs/2309.14482
18. Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing. Y. Benjamini, Y. Hochberg. JRSS-B 57(1): 289-300, 1995. https://doi.org/10.1111/j.2517-6161.1995.tb02031.x
19. Detecting outliers: Do not use standard deviation around the mean, use absolute deviation around the median. C. Leys et al. JESP 49(4): 764-766, 2013. https://doi.org/10.1016/j.jesp.2013.03.013
20. Control Chart Tests Based on Geometric Moving Averages. S. W. Roberts. Technometrics 1(3): 239-250, 1959. https://doi.org/10.1080/00401706.1959.10489860
21. Grafana Loki OSS: log aggregation system. Grafana Labs. https://grafana.com/oss/loki/
22. Elastic Stack (ELK): Elasticsearch, Logstash, Kibana. Elastic. https://www.elastic.co/elastic-stack
