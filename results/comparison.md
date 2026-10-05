# 全数据集实验对比

> 生成时间 2026-10-06 00:44:49；命令 python scripts/run_all.py

| 数据集 | 行数 | 解析率 | 模板数 | 单点判定 | 异常窗口 | 首要窗口摘要 |
|---|---|---|---|---|---|---|
| HDFS | 2000 | 100.00% | 16 | 215 | 35 | 窗口 11-10 10:30:00 ~ 10:30:00（约 10 分钟）；模板 T6 计数异常（观测 65 次 / 基线 0.87 次）；量级突变 volume-up；检测器 ewma+poisson+robust_z |
| Linux | 2000 | 100.00% | 115 | 381 | 42 | 窗口 06-30 12:00:00 ~ 06:00:00（约 1.0 天）；模板 T3 计数异常（观测 10 次 / 基线 0.66 次）；出现 3 个观察期后的新模板；规则命中 auth-failure/error-level/unknown-user；量级突变 error-volume-up/volume-up；检测器 ewma+novelty+poisson+robust_z+rules |
| Apache | 2000 | 100.00% | 6 | 362 | 14 | 窗口 12-04 16:30:00 ~ 17:40:00（约 1.3 小时）；模板 T2 计数异常（观测 17 次 / 基线 3.55 次）；出现 2 个观察期后的新模板；规则命中 error-level；量级突变 error-volume-up/volume-up；检测器 ewma+novelty+poisson+robust_z+rules |
| Zookeeper | 2000 | 100.00% | 46 | 153 | 8 | 窗口 08-20 15:00:00 ~ 15:00:00（约 3.0 小时）；模板 T16 计数异常（观测 5 次 / 基线 0.16 次）；出现 3 个观察期后的新模板；规则命中 exception-traceback/io-error；量级突变 volume-up/warn-volume-up；检测器 ewma+novelty+poisson+robust_z+rules |

各数据集细节见 results/<数据集>/report.md。
