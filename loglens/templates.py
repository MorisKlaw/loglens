"""模板挖掘：自研 Drain 实现，把海量日志压成少量模板。

Drain 的核心思想（He et al., ICWS 2017 "Drain: An Online Log Parsing Approach
with Fixed Depth Parse Tree"）：

1. 按 token 数量分层 —— 只有长度相同的日志才可能属于同一模板；
2. 逐层用前缀 token 建立定深解析树，把海量日志快速压缩到有限叶子节点；
3. 叶子节点内用「序列相似度」与已有模板比较：相似则合并，不相似则新建；
4. 合并时把不一致的 token 位置替换成通配符 <*>，得到可读的模板。

本实现在标准 Drain 上做了三点工程增强：

* 掩码（masking）：先把 block id / IP:Port / 数字 / 路径 / UUID / 十六进制
  替换成占位符，避免变量干扰聚类，同时让模板更易读；
* 掩码占位符在相似度计算中按通配符处理，提升召回；
* 记录模板的首末出现时间、来源分布、级别分布与样本原行，便于报告取证。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .schema import LogRecord

__all__ = [
    "WILDCARD",
    "DEFAULT_MASKS",
    "mask_line",
    "is_wildcard",
    "LogTemplate",
    "Drain",
    "TemplateMiner",
]

WILDCARD = "<*>"

#: 默认掩码规则：(正则, 占位符)。顺序有讲究：先长/具体，后短/笼统。
DEFAULT_MASKS: Tuple[Tuple[str, str], ...] = (
    (r"\bblk_-?\d+\b", "<BLK>"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "<IP>"),
    (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "<UUID>"),
    (r"\b0x[0-9a-fA-F]+\b", "<HEX>"),
    (r"(?:/[\w.\-+@]+){2,}/?", "<PATH>"),
    (r"\b\d+(?:\.\d+)?(?:ms|us|ns|s|MB|KB|GB|TB|m|h|d)?\b", "<NUM>"),
)

_COMPILED_MASKS: List[Tuple[re.Pattern, str]] = [
    (re.compile(p), r) for p, r in DEFAULT_MASKS
]

_WILDCARD_RE = re.compile(r"^<[A-Z_]+>$")


def mask_line(line: str, masks: Optional[Sequence[Tuple[re.Pattern, str]]] = None) -> str:
    """把变量替换成占位符。"""
    out = line
    for pat, rep in masks if masks is not None else _COMPILED_MASKS:
        out = pat.sub(rep, out)
    return out


def is_wildcard(token: str) -> bool:
    """<*> 或掩码占位符（<NUM>/<IP>/...）都视为通配符。"""
    return token == WILDCARD or bool(_WILDCARD_RE.match(token))


@dataclass
class LogTemplate:
    """一个日志模板及其统计画像。"""

    template_id: int
    tokens: List[str]
    count: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    first_line_no: int = 0
    examples: List[str] = field(default_factory=list)
    sources: Counter = field(default_factory=Counter)
    levels: Counter = field(default_factory=Counter)
    max_examples: int = 3

    @property
    def pattern(self) -> str:
        return " ".join(self.tokens)

    @property
    def wildcard_count(self) -> int:
        return sum(1 for t in self.tokens if is_wildcard(t))

    @property
    def is_constant(self) -> bool:
        """整条日志没有任何变量（例如固定的启动横幅）。"""
        return self.wildcard_count == 0

    @property
    def main_level(self) -> Optional[str]:
        return self.levels.most_common(1)[0][0] if self.levels else None

    def observe(
        self,
        ts: Optional[datetime],
        raw: str,
        line_no: int,
        level: Optional[str],
        source: Optional[str],
    ) -> None:
        self.count += 1
        if ts is not None:
            if self.first_seen is None or ts < self.first_seen:
                self.first_seen = ts
            if self.last_seen is None or ts > self.last_seen:
                self.last_seen = ts
        if not self.first_line_no:
            self.first_line_no = line_no
        if level:
            self.levels[level] += 1
        if source:
            self.sources[source] += 1
        if raw and len(self.examples) < self.max_examples and raw not in self.examples:
            self.examples.append(raw)

    def merge_tokens(self, tokens: Sequence[str]) -> None:
        """按 Drain 规则合并：不一致的位置变成通配符。"""
        for i, tok in enumerate(tokens):
            if i >= len(self.tokens):
                break
            cur = self.tokens[i]
            if cur == tok:
                continue
            if is_wildcard(cur):
                continue
            self.tokens[i] = WILDCARD


class _Node:
    """解析树节点。"""

    __slots__ = ("children", "templates")

    def __init__(self) -> None:
        self.children: Dict[str, "_Node"] = {}
        self.templates: List[LogTemplate] = []


class Drain:
    """定深解析树模板挖掘器（在线增量式）。"""

    def __init__(
        self,
        depth: int = 4,
        sim_th: float = 0.4,
        max_children: int = 100,
        max_templates: int = 20000,
        masks: Optional[Sequence[Tuple[re.Pattern, str]]] = None,
    ) -> None:
        self.depth = max(2, int(depth))
        self.sim_th = float(sim_th)
        self.max_children = int(max_children)
        self.max_templates = int(max_templates)
        self.masks = list(masks) if masks is not None else list(_COMPILED_MASKS)
        self.root = _Node()
        self.templates: List[LogTemplate] = []

    # ---------- 内部工具 ----------

    @staticmethod
    def tokenize(masked_line: str) -> List[str]:
        """按空白分词，并去掉首尾空白。"""
        return masked_line.strip().split()

    @staticmethod
    def similarity(tokens: Sequence[str], template_tokens: Sequence[str]) -> float:
        """序列相似度 = 1 - 差异位置数 / 长度；通配符位置不计差异。"""
        if len(tokens) != len(template_tokens) or not tokens:
            return 0.0
        diff = 0
        for a, b in zip(tokens, template_tokens):
            if a != b and not is_wildcard(a) and not is_wildcard(b):
                diff += 1
        return 1.0 - diff / len(tokens)

    def _leaf(self, tokens: Sequence[str]) -> _Node:
        """按 token 长度 + 前 depth-1 个 token 走到叶子节点。"""
        node = self.root
        key0 = str(len(tokens))
        node = node.children.setdefault(key0, _Node())
        for level in range(1, min(self.depth, len(tokens))):
            tok = tokens[level - 1]
            key = tok if not is_wildcard(tok) else WILDCARD
            child = node.children.get(key)
            if child is None:
                if len(node.children) >= self.max_children:
                    key = WILDCARD
                    child = node.children.get(key)
                if child is None:
                    child = _Node()
                    node.children[key] = child
            node = child
        return node

    # ---------- 对外接口 ----------

    def add(
        self,
        line: str,
        ts: Optional[datetime] = None,
        raw: str = "",
        line_no: int = 0,
        level: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        """加入一条日志（可以是原始行，内部会自动掩码），返回其模板 id。"""
        tokens = self.tokenize(mask_line(line, self.masks))
        if not tokens:
            tokens = ["<EMPTY>"]
        leaf = self._leaf(tokens)

        best: Optional[LogTemplate] = None
        best_sim = -1.0
        for tmpl in leaf.templates:
            sim = self.similarity(tokens, tmpl.tokens)
            if sim > best_sim:
                best_sim, best = sim, tmpl
        if best is None or best_sim < self.sim_th:
            if len(self.templates) >= self.max_templates:
                # 模板数上限保护：并入最相似的叶子模板，避免内存膨胀
                if best is None:
                    return -1
            else:
                tmpl = LogTemplate(template_id=len(self.templates), tokens=list(tokens))
                self.templates.append(tmpl)
                leaf.templates.append(tmpl)
                tmpl.observe(ts, raw, line_no, level, source)
                return tmpl.template_id
        best.merge_tokens(tokens)
        best.observe(ts, raw, line_no, level, source)
        return best.template_id

    def add_record(self, record: LogRecord) -> int:
        """加入一条 LogRecord，并回写 template_id / template。"""
        tid = self.add(
            record.message,
            ts=record.ts,
            raw=record.raw,
            line_no=record.line_no,
            level=record.level,
            source=record.source,
        )
        record.template_id = tid
        if 0 <= tid < len(self.templates):
            record.template = self.templates[tid].pattern
        return tid

    def template_of(self, line: str) -> Optional[LogTemplate]:
        tokens = self.tokenize(mask_line(line, self.masks))
        leaf = self._leaf(tokens)
        best, best_sim = None, -1.0
        for tmpl in leaf.templates:
            sim = self.similarity(tokens, tmpl.tokens)
            if sim > best_sim:
                best_sim, best = sim, tmpl
        if best is not None and best_sim >= self.sim_th:
            return best
        return None

    def n_templates(self) -> int:
        return len(self.templates)

    def sorted_templates(self) -> List[LogTemplate]:
        return sorted(self.templates, key=lambda t: (-t.count, t.template_id))


class TemplateMiner:
    """模板挖掘门面：批量挖掘并回填 LogRecord。"""

    def __init__(self, **kwargs) -> None:
        self.drain = Drain(**kwargs)

    def mine(self, records: Sequence[LogRecord]) -> Drain:
        for rec in records:
            self.drain.add_record(rec)
        return self.drain

    @property
    def templates(self) -> List[LogTemplate]:
        return self.drain.templates
