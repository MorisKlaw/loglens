# 数据说明

本目录下的四个文件是清华 LogPAI 团队维护的 **LogHub** 公开日志数据集的 2k 行样例，
未做任何改写，仅用于本实验的解析与异常检测演示。

| 文件 | 行数 | 来源 URL |
| --- | ---: | --- |
| HDFS_2k.log | 2000 | https://raw.githubusercontent.com/logpai/loghub/master/HDFS/HDFS_2k.log |
| Linux_2k.log | 2000 | https://raw.githubusercontent.com/logpai/loghub/master/Linux/Linux_2k.log |
| Apache_2k.log | 2000 | https://raw.githubusercontent.com/logpai/loghub/master/Apache/Apache_2k.log |
| Zookeeper_2k.log | 2000 | https://raw.githubusercontent.com/logpai/loghub/master/Zookeeper/Zookeeper_2k.log |

上游仓库：<https://github.com/logpai/loghub>（完整数据集通过 Zenodo 分发，见官方 README）。

## 引用要求

使用这些数据请注明仓库地址，并引用：

- Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. Loghub: A Large Collection of
  System Log Datasets for AI-driven Log Analytics. ISSRE 2023.
- 使用 HDFS_v1 时另需引用：Wei Xu, Ling Huang, Armando Fox, David Patterson, Michael Jordan.
  Detecting Large-Scale System Problems by Mining Console Logs. SOSP 2009.

## 获取完整数据集

    https://zenodo.org/records/8196385

完整 HDFS_v1 附带 block 级 anomaly_label.csv，可用于有监督评测；本仓库的 2k 样例不含标注，
因此用注入式基准（见 results/bench/bench_report.md）量化检测能力。

各数据集的格式细节、时间范围与坑位见 docs/00-dataset.md。
