"""解析层：把原始日志行解析成结构化 LogRecord。

内置四种真实日志格式解析器（LogHub 数据集格式）+ 通用兜底解析器：

===========  ==========================================================
格式          样例
===========  ==========================================================
hdfs         081109 203615 148 INFO dfs.DataNode$PacketResponder: ...
linux        Jun 14 15:16:01 combo sshd(pam_unix)[19939]: ...
apache       [Sun Dec 04 04:47:44 2005] [notice] child pid 3142 ...
zookeeper    2015-07-29 17:41:44,747 - INFO  [QuorumPeer[myid=1]...] - ...
generic      任意文本（时间/级别可能解析不到）
===========  ==========================================================

设计要点：
1. 解析器只负责头部字段（时间/级别/来源/进程/PID），正文留给模板挖掘，
   避免把变量写死进正则；
2. format=auto 会在文件头部采样，选择命中率最高的解析器；
3. 解析不到的行不会被丢掉，而是标记为 fallback 并沿用上一行时间戳，
   保证统计口径 = 原始行数（可追溯、不静默丢数据）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .schema import LogRecord, infer_level, normalize_level

__all__ = [
    "ParseResult",
    "BaseParser",
    "HDFSParser",
    "LinuxParser",
    "ApacheParser",
    "ZookeeperParser",
    "GenericParser",
    "PARSERS",
    "get_parser",
    "detect_format",
    "read_text",
    "parse_lines",
    "parse_file",
]

ISO_RE = re.compile(
    r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[ T](?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
)
LEVEL_TOKEN_RE = re.compile(
    r"\b(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|SEVERE|CRITICAL|CRIT|FATAL|ALERT|EMERG|PANIC)\b"
)


class BaseParser:
    """解析器基类。"""

    name = "base"
    description = ""

    def match(self, line: str) -> bool:
        raise NotImplementedError

    def parse(self, line: str, line_no: int, ctx: Dict) -> LogRecord:
        raise NotImplementedError

    def match_rate(self, lines: Sequence[str]) -> float:
        """采样命中率，供 auto 识别使用。"""
        if not lines:
            return 0.0
        hit = sum(1 for ln in lines if self.match(ln))
        return hit / len(lines)


class HDFSParser(BaseParser):
    """Hadoop/HDFS 日志：YYMMDD HHMMSS pid LEVEL logger: message。"""

    name = "hdfs"
    description = "HDFS/Hadoop 格式：日期(YYMMDD) 时间(HHMMSS) pid 级别 logger: 正文"
    _re = re.compile(
        r"^(?P<d>\d{6})\s+(?P<t>\d{6})\s+(?P<pid>\d+)\s+"
        r"(?P<lvl>[A-Z]+)\s+(?P<src>[^:\s][^:]*):\s?(?P<msg>.*)$"
    )

    def match(self, line: str) -> bool:
        return bool(self._re.match(line))

    def parse(self, line: str, line_no: int, ctx: Dict) -> LogRecord:
        m = self._re.match(line)
        if not m:
            raise ValueError("not a hdfs line")
        g = m.groupdict()
        ts: Optional[datetime] = None
        try:
            ts = datetime.strptime(g["d"] + " " + g["t"], "%y%m%d %H%M%S")
        except ValueError:
            ts = None
        src = g["src"].strip()
        extra: Dict[str, object] = {}
        # dfs.DataNode$PacketResponder -> 组件 dfs.DataNode，类 PacketResponder
        if "$" in src:
            comp, _, cls = src.partition("$")
        elif "." in src:
            comp, _, cls = src.rpartition(".")
        else:
            comp, cls = src, ""
        extra["component"] = comp
        extra["class"] = cls
        return LogRecord(
            line_no=line_no,
            raw=line,
            message=g["msg"].strip(),
            ts=ts,
            level=normalize_level(g["lvl"]),
            source=src,
            pid=int(g["pid"]),
            parser=self.name,
            extra=extra,
        )


class LinuxParser(BaseParser):
    """Linux syslog：Mon DD HH:MM:SS host proc[pid]: message。

    syslog 不带年份，需要用 syslog_year 指定基准年（默认 2005，与 LogHub
    Linux 数据集年代一致；只影响绝对时间，不影响时序统计）。
    """

    name = "linux"
    description = "Linux syslog 格式：月 日 时:分:秒 主机 进程[pid]: 正文"
    # 真实的 syslog 里有两类「不规整」写法，这里一并兼容，保证解析率 100%：
    #   1) 程序名带版本号：combo syslogd 1.4.1: restart.
    #   2) 程序名前带 -- 标记：combo  -- root[2421]: ROOT LOGIN ON tty2
    _re = re.compile(
        r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
        r"(?P<host>\S+)\s+(?:--\s+)?(?P<proc>[^\s:\[]+)(?:\s+[^\s:]+)?"
        r"(?:\[(?P<pid>\d+)\])?:\s?(?P<msg>.*)$"
    )

    def match(self, line: str) -> bool:
        return bool(self._re.match(line))

    def parse(self, line: str, line_no: int, ctx: Dict) -> LogRecord:
        m = self._re.match(line)
        if not m:
            raise ValueError("not a syslog line")
        g = m.groupdict()
        year = int(ctx.get("syslog_year", 2005))
        ts: Optional[datetime] = None
        try:
            ts = datetime.strptime(
                str(year) + " " + g["mon"] + " " + g["day"] + " " + g["time"],
                "%Y %b %d %H:%M:%S",
            )
        except ValueError:
            ts = None
        return LogRecord(
            line_no=line_no,
            raw=line,
            message=g["msg"].strip(),
            ts=ts,
            level=infer_level(g["msg"]),
            source=g["proc"],
            pid=int(g["pid"]) if g["pid"] else None,
            host=g["host"],
            parser=self.name,
            extra={"program": g["proc"]},
        )


class ApacheParser(BaseParser):
    """Apache error log：[Sun Dec 04 04:47:44 2005] [notice] message。"""

    name = "apache"
    description = "Apache error_log 格式：[时间] [级别] 正文"
    _re = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<lvl>[A-Za-z]+)\]\s+(?P<msg>.*)$")

    def match(self, line: str) -> bool:
        return bool(self._re.match(line))

    def parse(self, line: str, line_no: int, ctx: Dict) -> LogRecord:
        m = self._re.match(line)
        if not m:
            raise ValueError("not an apache line")
        g = m.groupdict()
        ts: Optional[datetime] = None
        raw_ts = g["ts"].strip()
        for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b %d %H:%M:%S.%f %Y", "%Y-%m-%d %H:%M:%S"):
            try:
                ts = datetime.strptime(raw_ts, fmt)
                break
            except ValueError:
                continue
        return LogRecord(
            line_no=line_no,
            raw=line,
            message=g["msg"].strip(),
            ts=ts,
            level=normalize_level(g["lvl"]),
            source="httpd",
            parser=self.name,
        )


class ZookeeperParser(BaseParser):
    """ZooKeeper：2015-07-29 17:41:44,747 - INFO [thread] - message。"""

    name = "zookeeper"
    description = "ZooKeeper 格式：yyyy-MM-dd HH:mm:ss,SSS - 级别 [线程] - 正文"
    # 注意：ZooKeeper 的线程名里会嵌套方括号（例如 [QuorumPeer[myid=1]/...:FastLeaderElection@774]），
    # 因此线程段必须用贪婪匹配，否则会被第一个 ] 提前截断。
    _re = re.compile(
        r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2}),(?P<ms>\d{1,3})\s*-\s*"
        r"(?P<lvl>[A-Z]+)\s*\[(?P<thread>.+)\]\s*-\s*(?P<msg>.*)$"
    )

    def match(self, line: str) -> bool:
        return bool(self._re.match(line))

    def parse(self, line: str, line_no: int, ctx: Dict) -> LogRecord:
        m = self._re.match(line)
        if not m:
            raise ValueError("not a zookeeper line")
        g = m.groupdict()
        ts: Optional[datetime] = None
        try:
            ts = datetime.strptime(
                g["date"] + " " + g["time"] + "." + g["ms"].ljust(3, "0"),
                "%Y-%m-%d %H:%M:%S.%f",
            )
        except ValueError:
            ts = None
        thread = g["thread"].strip()
        src = thread.split(":")[-1] if ":" in thread else thread
        return LogRecord(
            line_no=line_no,
            raw=line,
            message=g["msg"].strip(),
            ts=ts,
            level=normalize_level(g["lvl"]),
            source=src,
            parser=self.name,
            extra={"thread": thread},
        )


class GenericParser(BaseParser):
    """兜底解析器：尽量抽取 ISO 时间与级别关键词，其余整行作为正文。"""

    name = "generic"
    description = "通用兜底：尝试 ISO 时间与级别关键词，正文保留整行"

    def match(self, line: str) -> bool:
        return bool(line.strip())

    def parse(self, line: str, line_no: int, ctx: Dict) -> LogRecord:
        ts: Optional[datetime] = None
        m = ISO_RE.search(line)
        if m:
            try:
                ts = datetime(
                    int(m.group("y")),
                    int(m.group("mo")),
                    int(m.group("d")),
                    int(m.group("h")),
                    int(m.group("mi")),
                    int(m.group("s")),
                )
            except ValueError:
                ts = None
        lm = LEVEL_TOKEN_RE.search(line)
        level = normalize_level(lm.group(1)) if lm else infer_level(line)
        return LogRecord(
            line_no=line_no,
            raw=line,
            message=line.strip(),
            ts=ts,
            level=level,
            source=None,
            parser=self.name,
        )


PARSERS: Dict[str, BaseParser] = {
    p.name: p
    for p in (HDFSParser(), LinuxParser(), ApacheParser(), ZookeeperParser(), GenericParser())
}

#: auto 识别时的探测顺序（generic 只做兜底，不参与竞争）
PARSER_ORDER = ("hdfs", "linux", "apache", "zookeeper")


def get_parser(fmt: str) -> BaseParser:
    key = (fmt or "auto").strip().lower()
    if key in ("auto", ""):
        return PARSERS["generic"]
    if key not in PARSERS:
        raise KeyError("未知格式: " + str(fmt) + "；可选 " + str(sorted(PARSERS)))
    return PARSERS[key]


def detect_format(
    lines: Sequence[str], sample: int = 300, threshold: float = 0.5
) -> Tuple[str, Dict[str, float]]:
    """在文件头部采样，返回 (格式名, 各解析器命中率)。

    命中率最高且不低于 threshold 的解析器胜出；否则回退 generic。
    """
    head = [ln for ln in lines[:sample] if ln.strip()]
    rates = {name: PARSERS[name].match_rate(head) for name in PARSER_ORDER}
    best = max(rates, key=lambda k: rates[k])
    if rates[best] >= threshold:
        return best, rates
    return "generic", rates


def read_text(path: str | Path) -> Tuple[str, str]:
    """读取文本，返回 (内容, 实际使用的编码)。

    依次尝试 UTF-8 / GB18030 / Latin-1，避免因编码问题中断实验。
    """
    data = Path(path).read_bytes()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(replace)"


@dataclass
class ParseResult:
    """解析结果与统计口径。"""

    records: List[LogRecord] = field(default_factory=list)
    fmt: str = "generic"
    match_rate: float = 0.0
    encoding: str = "utf-8"
    total_lines: int = 0
    blank_lines: int = 0
    fallback_lines: int = 0
    with_ts: int = 0
    formats_probed: Dict[str, float] = field(default_factory=dict)

    @property
    def parse_rate(self) -> float:
        """结构化解析成功率（未走 fallback 的行占比）。"""
        if not self.records:
            return 0.0
        return 1.0 - self.fallback_lines / len(self.records)

    def summary(self) -> Dict[str, object]:
        return {
            "format": self.fmt,
            "encoding": self.encoding,
            "total_lines": self.total_lines,
            "parsed_records": len(self.records),
            "blank_lines": self.blank_lines,
            "fallback_lines": self.fallback_lines,
            "with_timestamp": self.with_ts,
            "parse_rate": round(self.parse_rate, 4),
            "match_rate": round(self.match_rate, 4),
        }


def parse_lines(
    lines: Iterable[str],
    fmt: str = "auto",
    syslog_year: int = 2005,
    forward_fill_ts: bool = True,
    blank_policy: str = "skip",
) -> ParseResult:
    """解析日志行序列。"""
    raw_lines = list(lines)
    result = ParseResult(total_lines=len(raw_lines))
    non_blank = [ln for ln in raw_lines if ln.strip()]
    result.blank_lines = len(raw_lines) - len(non_blank)

    if (fmt or "auto").lower() == "auto":
        chosen, rates = detect_format(non_blank)
        result.formats_probed = {k: round(v, 4) for k, v in rates.items()}
    else:
        chosen = fmt.lower()
    parser = PARSERS[chosen]
    result.fmt = parser.name
    result.match_rate = parser.match_rate(non_blank[:300]) if non_blank else 0.0

    ctx: Dict[str, object] = {"syslog_year": syslog_year}
    prev_ts: Optional[datetime] = None
    for idx, line in enumerate(raw_lines, start=1):
        if not line.strip() and blank_policy == "skip":
            continue
        rec: Optional[LogRecord] = None
        if parser.name != "generic" and parser.match(line):
            try:
                rec = parser.parse(line, idx, ctx)
            except ValueError:
                rec = None
        if rec is None:
            rec = GenericParser().parse(line, idx, ctx)
            rec.parser = "fallback" if parser.name != "generic" else "generic"
            result.fallback_lines += 1
        if rec.ts is None and forward_fill_ts and prev_ts is not None:
            rec.ts = prev_ts
            rec.extra["ts_forward_filled"] = True
        if rec.ts is not None:
            prev_ts = rec.ts
            result.with_ts += 1
        result.records.append(rec)
    return result


def parse_file(
    path: str | Path,
    fmt: str = "auto",
    syslog_year: int = 2005,
    forward_fill_ts: bool = True,
) -> ParseResult:
    """读取并解析一个日志文件。"""
    text, encoding = read_text(path)
    res = parse_lines(
        text.splitlines(), fmt=fmt, syslog_year=syslog_year, forward_fill_ts=forward_fill_ts
    )
    res.encoding = encoding
    return res
