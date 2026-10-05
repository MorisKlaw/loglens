# 一页分析报告：日志解析、统计与异常检测

> 数据：LogHub **HDFS_2k.log**（2000 行，2008-11-09 20:36:15 ~ 2008-11-11 10:20:17），另在 Linux / Apache / Zookeeper 三个样例上交叉验证。
> 方法：Drain 模板挖掘（自研）+ 三层统计检验（稳健 z-score / 泊松尾检验+BH-FDR / EWMA 控制图）+ 关键词规则补充。
> 复现：`python -m loglens run -i data/HDFS_2k.log -o results/HDFS`（完整产物见 results/HDFS/report.md）。

## 1 解析与统计

**解析率 100%（2000/2000），零兜底行**，格式自动识别为 hdfs。抽取字段：时间、级别、来源（logger）、PID、正文。
级别分布 INFO 1920 / WARN 80；来源 Top3 为 dfs.FSNamesystem 659、dfs.DataNode$PacketResponder 603、dfs.DataNode$DataXceiver 454。

掩码（块 ID / IP:Port / 数字 / 路径 / UUID / 十六进制）后做 Drain 聚类：**2000 行压成 16 个模板**，其中单例模板 3 个。
统计口径为「10 分钟一桶 × 228 桶 × 16 模板」的计数矩阵，后续所有检验都在这张矩阵上做。

| 模板 | 形态（已掩码） | 次数 | 主要级别 |
| --- | --- | ---: | --- |
| T1 | BLOCK\* NameSystem.addStoredBlock: blockMap updated: <IP> is added to <BLK> size <NUM> | 314 | INFO |
| T0 | PacketResponder <NUM> for block <BLK> terminating | 311 | INFO |
| T6 | Deleting block <BLK> file <PATH><BLK> | 263 | INFO |
| T8 | <IP>:Got exception while serving <BLK> to /<IP>: | 80 | WARN |

## 2 识别到的异常与判断依据

HDFS 样例共检出 **35 个异常窗口**（high 5 / medium 14 / low 16），下面列出 3 个典型样本，另附 1 个跨数据集样本。

### 样本 A（量变型，统计判据）：块删除事件突增

- 窗口：2008-11-10 10:30:00（10 分钟桶），分数 3.60，检测器 robust_z + poisson + ewma
- 判据：模板 T6「Deleting block …」本桶出现 **65 次**，历史中位数 0 次、泊松基线速率 **0.872 次/桶**（放大约 74.5 倍），泊松上尾 p = 1.11e-16，在 **284 次检验**中经 BH-FDR（q=0.05）校正后仍显著；同时该桶总日志量越出 EWMA 控制限（L=4）。
- 原始证据（行首 L 编号为源文件行号，可直接回查 data/HDFS_2k.log）：
```text
L426 | 081110 103201 19 INFO dfs.FSDataset: Deleting block blk_8483848473254499625 file /mnt/hadoop/dfs/data/current/subdir51/blk_8483848473254499625
L428 | 081110 103257 19 INFO dfs.FSDataset: Deleting block blk_-8898274302731129139 file /mnt/hadoop/dfs/data/current/subdir18/blk_-8898274302731129139
```

### 样本 B（语义型，规则+统计）：DataNode 服务块异常

- 窗口：2008-11-09 21:40 ~ 21:50，分数 3.10，检测器 rules + poisson + ewma
- 判据：T8「Got exception while serving <BLK>」本桶 4 次 vs 基线 0.326 次（泊松 p = 1.26e-06，放大 18.4 倍）；规则 exception-traceback 命中；WARN 量越出控制限。模板 T8 的 80 条全部是 WARN，是该数据集里唯一的非 INFO 模板。
- 原始证据：
```text
L78 | 081109 214043 2561 WARN dfs.DataNode$DataXceiver: 10.251.30.85:50010:Got exception while serving blk_-2918118818249673980 to /10.251.90.64:
```

### 样本 C（跨数据集：Linux 认证失败聚集）

- 窗口：2005-06-22 00:00（6 小时桶），分数 5.70，检测器 rules + poisson + robust_z + ewma
- 判据：模板 T2「authentication failure; logname= uid=<NUM> euid=<NUM> tty=…」本桶 **23 次 vs 基线 2.03 次**；规则 auth-failure 与 unknown-user 同时命中；错误量越限。证据里同一远程主机反复失败、并伴随 user unknown，符合口令爆破特征（工具只负责把证据摆出来，是否定性需人工判断）。
- 原始证据：
```text
L199 | Jun 22 03:17:26 combo sshd(pam_unix)[16207]: authentication failure; logname= uid=0 euid=0 tty=NODEVssh ruser= rhost=n219076184117.netvigator.com  user=root
```

### 样本 D（跨数据集：ZooKeeper 会话与 IO 异常）

- 窗口：2015-08-20 15:00（3 小时桶），分数 7.30，检测器 novelty + rules + poisson + robust_z + ewma
- 判据：出现 3 个观察期之后首次出现的新模板；规则 exception-traceback / io-error 命中；日志量与 WARN 量同时越限。证据显示会话集中超时过期。
- 原始证据：
```text
L1427 | 2015-08-20 17:12:28,002 - INFO  [SessionTracker:ZooKeeperServer@325] - Expiring session 0x24f3fdaf738000a, timeout of 10000ms exceeded
```

## 3 方法在什么情况下会漏报 / 误报

**会漏报**：① 异常以极少量单条出现（计数达不到 min_observed=3 与 z 阈值）；② 故障表现是「日志变少/中断」（如进程直接挂掉）——没有日志可分析；③ 模板演化（同义改写被聚成新模板，计数被打散到两个模板）；④ 缓慢劣化但每桶仍在正常范围内（EWMA 只报越限点）；⑤ 规则表未覆盖的故障语义。

**会误报**：① 正常业务突增（批处理、扩容、备份窗口）；② 重启/升级带来一批新模板；③ 上游抖动引起的连锁重试；④ 日志轮转、采样或级别调整造成的量级跳变；⑤ 周期性任务在同一时刻的固定行为。

量化边界见第 4 节；逐检测器的完整失效分析与缓解清单见 docs/03-false-alarm-analysis.md。

## 4 量化边界（注入式基准，4 个数据集 × 2 个随机种子 × 12 个注入窗口）

由于公开 2k 样例不含标注，用可控注入（burst 模板突增 / novel 新错误模板 / escalation 级别抬升）自造真值：

| 指标 | 融合方案 | 最佳单检测器（稳健 z） |
| --- | ---: | ---: |
| 窗口层面 精确率 P | 0.408 | 0.382 |
| 窗口层面 召回率 R | 0.844 | 0.688 |
| 窗口层面 F1 | **0.528** | 0.464 |
| 单点层面 注入窗口灵敏度 | **0.875** | 0.688 |

- 分类型灵敏度（融合方案）：burst **1.000**、novel **1.000**、escalation 0.625。
- 阈值扫描（window_threshold）：0.5 → P 0.357 / R 0.875 / F1 0.493；**1.0（默认）→ 0.408 / 0.844 / 0.528（F1 峰值）**；2.0 → 0.345 / 0.458 / 0.384。想更灵敏就调到 0.5，想更保守就调到 2.0。
- 注意：窗口层面精确率的上限由数据本身决定——未注入异常时，同一套检测器在原始日志上平均也会给出 24.75 个窗口（都是值得人工看一眼的自然波动），这正是「漏报/误报」讨论必须结合场景的原因。

## 5 结论

在无标注、无 GPU、只依赖 numpy/pandas 的约束下，「模板挖掘 + 多层统计检验 + 规则补充」可以在真实日志上完成可解释、可量化、可复现的异常检测：解析率 100%，对突发型异常灵敏度 100%，每个结论都能回溯到具体模板、统计量与原始日志行。
