# 数据集说明

本文说明《智能运维：让日志开口说话》实验所用日志数据的来源、构成与使用约束。本仓库 `data/` 下的四个文件均为 LogHub 官方提供的 2k 行样例，未做任何改写。

## 1 数据来源与获取方式

**上游仓库**：LogHub，由清华大学 LogPAI 团队维护，地址 <https://github.com/logpai/loghub>。该仓库汇集面向 AI 驱动日志分析的系统日志，官方声明数据尽可能不做脱敏、匿名或改写。

**完整数据集获取途径**：仓库 README 的 Download 列给出 Zenodo 归档链接（record [8196385](https://zenodo.org/records/8196385)），本实验相关的四个包为：

- HDFS_v1：<https://zenodo.org/records/8196385/files/HDFS_v1.zip?download=1>
- Linux：<https://zenodo.org/records/8196385/files/Linux.tar.gz?download=1>
- Apache：<https://zenodo.org/records/8196385/files/Apache.tar.gz?download=1>
- Zookeeper：<https://zenodo.org/records/8196385/files/Zookeeper.tar.gz?download=1>

**本仓库内 4 个 2k 样例**（行数、字节数均为本地文件实测；来源 URL 已发请求核对，返回 HTTP 200 且与本地文件逐行一致）：

| 文件 | 行数 | 字节数 | 来源 URL |
| --- | ---: | ---: | --- |
| `data/HDFS_2k.log` | 2000 | 287848 | <https://raw.githubusercontent.com/logpai/loghub/master/HDFS/HDFS_2k.log> |
| `data/Linux_2k.log` | 2000 | 216485 | <https://raw.githubusercontent.com/logpai/loghub/master/Linux/Linux_2k.log> |
| `data/Apache_2k.log` | 2000 | 171239 | <https://raw.githubusercontent.com/logpai/loghub/master/Apache/Apache_2k.log> |
| `data/Zookeeper_2k.log` | 2000 | 279891 | <https://raw.githubusercontent.com/logpai/loghub/master/Zookeeper/Zookeeper_2k.log> |

**许可与引用要求**：数据集可免费用于研究或学术用途；任何使用或分发都须注明仓库地址 <https://github.com/logpai/loghub>，并引用 LogHub 论文：

- Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics.* ISSRE 2023（BibTeX key：`Loghub`）。
- 若涉及 LogHub-2.0 的解析评测结论，另引 Jiang et al., *A Large-scale Evaluation for Log Parsing Techniques: How Far are We?*, ISSTA 2024（key：`Loghub2`）。
- 使用 HDFS_v1 时官方还要求引用 Wei Xu et al., *Detecting Large-Scale System Problems by Mining Console Logs*, SOSP 2009。

## 2 四个数据集简介

| 数据集 | 来源系统 | 样例条数 | 样例覆盖时间段 | 格式特点 | 标注情况 | 本实验中的用途 |
| --- | --- | ---: | --- | --- | --- | --- |
| HDFS_2k | Hadoop 分布式文件系统（HDFS_v1） | 2000 | 2008-11-09 20:36:15 至 2008-11-11 10:20:17（约 37.7 小时，3 个自然日） | `YYMMDD HHMMSS PID LEVEL 组件: 内容`，空格分隔、无包裹符 | 样例无标注；仅完整 HDFS_v1 提供 block 级 `anomaly_label` | 块会话切分、模板挖掘、无监督异常检测（完整数据集另可做有监督评测） |
| Linux_2k | Linux 服务器 `/var/log/messages` | 2000 | 6 月 14 日 15:16:01 至 7 月 27 日 14:42:00（44 个自然日，日志内无年份） | 经典 syslog：`Mmm dd hh:mm:ss 主机 进程[PID]: 内容` | 无 | 模板解析基线、登录失败与内核事件统计 |
| Apache_2k | Apache HTTP Server 错误日志 | 2000 | 2005-12-04 04:47:44 至 2005-12-05 19:15:57（2 个自然日） | `[Www Mmm dd hh:mm:ss yyyy] [级别] 内容`，时间与级别均用方括号 | 无 | 级别分布、错误突发检测、时间窗聚合 |
| Zookeeper_2k | ZooKeeper 服务（CUHK 实验集群） | 2000 | 2015-07-29 17:41:44,747 至 2015-08-25 11:26:28,145（10 个自然日，不连续） | `yyyy-MM-dd HH:mm:ss,SSS - LEVEL [线程] - 内容`，含毫秒与线程名 | 无 | 会话/选举异常识别、时序与线程维度分析 |

级别与组件分布（实测）：HDFS 为 INFO 1920 行、WARN 80 行；Apache 为 notice 1405 行、error 595 行；Zookeeper 为 WARN 1318 行、INFO 669 行、ERROR 13 行；Linux 以 `ftpd`（916 行）与 `sshd(pam_unix)`（677 行）为主。可作日志级别占比的对照基线。

## 3 日志格式样例

### 3.1 HDFS

```text
081109 203615 148 INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_38865049064139660 terminating
081109 204005 35 INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: blockMap updated: 10.251.73.220:50010 is added to blk_7128370237687728475 size 67108864
081111 102017 26347 INFO dfs.DataNode$DataXceiver: Receiving block blk_4343207286455274569 src: /10.250.9.207:59759 dest: /10.250.9.207:50010
```

字段：`081109` 为日期 YYMMDD（即 2008-11-09，无世纪信息）；`203615` 为时间 HHMMSS；`148` 为进程号（PID，LogPAI 解析约定）；`INFO` 为级别；`dfs.DataNode$PacketResponder` 为组件/类名；冒号后为消息体，其中 `blk_xxx` 是块 ID，`size 67108864` 为块大小（字节）。

### 3.2 Linux

```text
Jun 14 15:16:01 combo sshd(pam_unix)[19939]: authentication failure; logname= uid=0 euid=0 tty=NODEVssh ruser= rhost=218.188.2.4 
Jun 14 15:16:02 combo sshd(pam_unix)[19937]: check pass; user unknown
Jul 27 14:42:00 combo kernel: Linux agpgart interface v0.100 (c) Dave Jones
```

字段：`Jun 14 15:16:01` 为月、日、时:分:秒（无年份）；`combo` 为主机名；`sshd(pam_unix)` 为进程与模块；`[19939]` 为 PID；冒号后为消息体，多用 `key=value`（`uid=`、`rhost=` 等），注意 `tty=NODEVssh` 与 `rhost=` 之后可能紧跟空格，行尾存在多余空格。

### 3.3 Apache

```text
[Sun Dec 04 04:47:44 2005] [notice] workerEnv.init() ok /etc/httpd/conf/workers2.properties
[Sun Dec 04 04:47:44 2005] [error] mod_jk child workerEnv in error state 6
[Mon Dec 05 19:15:57 2005] [error] mod_jk child workerEnv in error state 6
```

字段：第一个方括号内是 `星期 月 日 时:分:秒 年`（星期在最前、日补零两位）；第二个方括号是日志级别（notice、error 等，非 Log4j 风格）；其后为自由文本消息。

### 3.4 Zookeeper

```text
2015-07-29 17:41:44,747 - INFO  [QuorumPeer[myid=1]/0:0:0:0:0:0:0:0:2181:FastLeaderElection@774] - Notification time out: 3200
2015-07-29 19:04:29,071 - WARN  [SendWorker:188978561024:QuorumCnxManager$SendWorker@688] - Send worker leaving thread
2015-08-10 18:12:34,004 - INFO  [ProcessThread(sid:3 cport:-1)::PrepRequestProcessor@476] - Processed session termination for sessionid: 0x24f0557806a0010
```

字段：`2015-07-29 17:41:44,747` 为 ISO 风格日期时间加逗号毫秒（毫秒固定 3 位）；第一个 ` - ` 为分隔符；`INFO`/`WARN` 为级别（与后随的 `[` 之间空格数不固定）；方括号内为线程名，常带类名与源码行号（`@774`）；第二个 ` - ` 之后为消息体。

## 4 使用方法与注意事项

1. **HDFS 时间戳无年份**：首字段为 6 位 `YYMMDD`，故 `080109` 表示 2008-01-09；本样例覆盖 `081109` 至 `081111`（2008-11-09 至 2008-11-11），不可按 `MMDDYY` 解析。
2. **Linux syslog 无年份**：只有 `Mmm dd hh:mm:ss`，必须指定基准年；本样例跨 6 月 14 日至 7 月 27 日共 44 天，而完整数据集官方标注跨度为 263.9 天，切分时还要处理跨月与跨年回绕。
3. **Apache 时间形如 `[Sun Dec 04 04:47:44 2005]`**：字段顺序为"星期 月 日 时:分:秒 年"，星期需丢弃；建议用 `strptime('%a %b %d %H:%M:%S %Y')` 解析并固定 C locale，避免本地化月名导致失败。
4. **Zookeeper 带毫秒与线程名**：毫秒以逗号而非点号分隔（`44,747`），级别与线程名之间空格数不固定；线程名内的 `@行号` 是定位源码的线索，但会让粗粒度的空格切分失效。
5. **2k 样例不含完整标注**：四个样例都没有标签列；只有完整 HDFS_v1 数据集才提供 block 级 `anomaly_label`（normal/anomaly）以及 `HDFS_templates.csv`、`Event_traces.csv` 等预处理文件。因此监督式评测必须下载完整数据集。
6. **多行堆栈与续行**：本仓库四个样例实测均可按"行首是否为时间戳"切分（2000/2000 行全部匹配，无空行、无缩进续行）；但完整数据集中的 Java 异常堆栈会产生续行（待核实），解析器仍应实现"无法匹配时间戳则并入上一条"的合并逻辑。
7. **编码与换行**：实测四个样例均为纯 ASCII（非 ASCII 字节数为 0），无 BOM、无制表符，行尾为 Windows 风格 CRLF。`HDFS_2k.log` 的 2000 行全部以 CRLF 结束；Linux、Apache、Zookeeper 三个文件各有 1999 个 CRLF，末行不带换行符。读取时应统一 `encoding='utf-8'` 并显式处理 `\r\n`，否则易出现末行丢失或字段尾部残留 `\r`。
8. **原始日志并非严格有序**：实测时间戳回退次数为 Apache 33 次、Linux 3 次、Zookeeper 2 次、HDFS 0 次；而 Zookeeper 末行时间为 2015-08-10，最大时间却为 2015-08-25。构造滑动窗口、事件速率等时序特征前必须先按时间戳排序。
