"""轻量绘图：不依赖 matplotlib，直接产出 SVG，可选同时产出 PNG。

为什么自己画：本项目的约束是「轻量、少依赖」。matplotlib 体积大、部署成本
高，而且在容器/CI 里容易踩字体坑。这里用一个极小的 Canvas 抽象，
同一份绘图代码同时输出两种格式：

* SVG —— 矢量、体积小，可直接嵌入 Markdown / README（GitHub 原生渲染）；
* PNG —— 便于离线预览与贴进文档（依赖 Pillow，缺失时自动跳过，不影响主流程）。

注意：图表文字统一用英文。原因是 PNG 走 Pillow 默认位图字体（无中文字形），
统一英文可以保证两种格式都不出现方块字。
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .features import FeatureSet
from .templates import LogTemplate

__all__ = ["Canvas", "timeline_chart", "template_bar_chart", "level_bar_chart", "write_chart"]

FONT = "Segoe UI, Helvetica, Arial, sans-serif"
COLOR_AXIS = "#5b6b7c"
COLOR_GRID = "#e6ebf0"
COLOR_NORMAL = "#9fb8cc"
COLOR_ALERT = "#e74c3c"
COLOR_HIGH = "#c0392b"
COLOR_TEXT = "#22313f"
COLOR_ACCENT = "#2d7dd2"


@dataclass
class Canvas:
    """极简画布：记录绘图指令，导出 SVG / PNG。"""

    width: int = 1000
    height: int = 320
    background: str = "#ffffff"
    ops: List[Tuple] = field(default_factory=list)

    def rect(self, x, y, w, h, fill=None, stroke=None, stroke_width=1, opacity=None) -> None:
        self.ops.append(("rect", float(x), float(y), float(w), float(h), fill, stroke, stroke_width, opacity))

    def line(self, x1, y1, x2, y2, stroke=COLOR_AXIS, width=1, dash=None) -> None:
        self.ops.append(("line", float(x1), float(y1), float(x2), float(y2), stroke, width, dash))

    def polyline(self, points: Sequence[Tuple[float, float]], stroke=COLOR_ACCENT, width=2) -> None:
        self.ops.append(("polyline", [(float(a), float(b)) for a, b in points], stroke, width))

    def text(self, x, y, s, size=12, fill=COLOR_TEXT, anchor="start", weight="normal") -> None:
        self.ops.append(("text", float(x), float(y), str(s), size, fill, anchor, weight))

    # ---------- 导出 ----------

    def to_svg(self) -> str:
        parts = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="' + str(self.width) + '" height="'
            + str(self.height) + '" viewBox="0 0 ' + str(self.width) + " " + str(self.height) + '">',
            '<rect width="100%" height="100%" fill="' + self.background + '"/>',
        ]
        for op in self.ops:
            kind = op[0]
            if kind == "rect":
                _, x, y, ww, hh, fill, stroke, sw, opacity = op
                attrs = 'x="%.1f" y="%.1f" width="%.1f" height="%.1f"' % (x, y, max(0.0, ww), max(0.0, hh))
                attrs += ' fill="' + (fill if fill else "none") + '"'
                if stroke:
                    attrs += ' stroke="' + stroke + '" stroke-width="' + str(sw) + '"'
                if opacity is not None:
                    attrs += ' opacity="' + str(opacity) + '"'
                parts.append("<rect " + attrs + "/>")
            elif kind == "line":
                _, x1, y1, x2, y2, stroke, sw, dash = op
                attrs = 'x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%s"' % (
                    x1, y1, x2, y2, stroke, sw,
                )
                if dash:
                    attrs += ' stroke-dasharray="' + dash + '"'
                parts.append("<line " + attrs + "/>")
            elif kind == "polyline":
                _, pts, stroke, sw = op
                coords = " ".join("%.1f,%.1f" % (a, b) for a, b in pts)
                parts.append(
                    '<polyline points="' + coords + '" fill="none" stroke="' + stroke
                    + '" stroke-width="' + str(sw) + '"/>'
                )
            elif kind == "text":
                _, x, y, s, size, fill, anchor, weight = op
                family = FONT if anchor != "middle" or True else FONT
                parts.append(
                    '<text x="%.1f" y="%.1f" font-family="%s" font-size="%s" fill="%s" '
                    'text-anchor="%s" font-weight="%s">%s</text>'
                    % (x, y, family, size, fill, anchor, weight, html.escape(s))
                )
        parts.append("</svg>")
        return "\n".join(parts)

    def to_png(self, path: str | Path) -> bool:
        """用 Pillow 导出 PNG；未安装 Pillow 时返回 False（不抛异常）。"""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception:
            return False
        img = Image.new("RGB", (int(self.width), int(self.height)), self.background)
        draw = ImageDraw.Draw(img)
        cache = {}

        def font(size):
            if size not in cache:
                try:
                    cache[size] = ImageFont.load_default(size=int(size))
                except TypeError:
                    cache[size] = ImageFont.load_default()
            return cache[size]

        anchor_map = {"start": "ls", "middle": "ms", "end": "rs"}
        for op in self.ops:
            kind = op[0]
            if kind == "rect":
                _, x, y, ww, hh, fill, stroke, sw, opacity = op
                box = [x, y, x + max(0.0, ww), y + max(0.0, hh)]
                draw.rectangle(
                    box,
                    fill=fill if fill else None,
                    outline=stroke if stroke else None,
                    width=int(max(1, sw)) if stroke else 1,
                )
            elif kind == "line":
                _, x1, y1, x2, y2, stroke, sw, dash = op
                draw.line([x1, y1, x2, y2], fill=stroke, width=int(max(1, sw)))
            elif kind == "polyline":
                _, pts, stroke, sw = op
                if len(pts) >= 2:
                    draw.line([(a, b) for a, b in pts], fill=stroke, width=int(max(1, sw)))
            elif kind == "text":
                _, x, y, s, size, fill, anchor, weight = op
                draw.text((x, y), s, fill=fill, font=font(size), anchor=anchor_map.get(anchor, "ls"))
        img.save(str(path))
        return True


def write_chart(canvas: Canvas, base_path: str | Path, png: bool = True) -> List[str]:
    """写出 SVG（必需）与 PNG（可选），返回写出的文件路径列表。"""
    base = Path(base_path)
    base.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    svg_path = base.with_suffix(".svg")
    svg_path.write_text(canvas.to_svg(), encoding="utf-8")
    paths.append(str(svg_path))
    if png:
        png_path = base.with_suffix(".png")
        if canvas.to_png(png_path):
            paths.append(str(png_path))
    return paths


def timeline_chart(
    features: FeatureSet,
    table: pd.DataFrame,
    incidents: Sequence,
    title: str = "Log volume timeline with detected anomaly windows",
    width: int = 1000,
    height: int = 340,
) -> Canvas:
    """日志量时间线：柱=每桶条数，红柱=被判为异常的桶。"""
    canvas = Canvas(width=width, height=height)
    left, right, top, bottom = 66, width - 24, 46, height - 46
    plot_w = max(10, right - left)
    plot_h = max(10, bottom - top)

    volume = features.volume.to_numpy(dtype=float)
    n = max(1, volume.size)
    vmax = float(max(1.0, volume.max()))
    buckets = features.buckets

    canvas.text(left, 24, title, size=15, weight="bold")
    canvas.text(
        left, 40,
        "bucket=" + str(features.bucket_seconds) + "s   buckets=" + str(n)
        + "   total=" + str(int(volume.sum())) + " lines   alerts=" + str(len(incidents)),
        size=11, fill="#6b7c8d",
    )

    # 网格与 y 轴
    for i in range(5):
        y = bottom - plot_h * i / 4.0
        canvas.line(left, y, right, y, stroke=COLOR_GRID, width=1)
        canvas.text(left - 8, y + 4, str(int(round(vmax * i / 4.0))), size=10, fill=COLOR_AXIS, anchor="end")
    canvas.line(left, bottom, right, bottom, stroke=COLOR_AXIS, width=1)
    canvas.line(left, top, left, bottom, stroke=COLOR_AXIS, width=1)

    flagged = table["flagged"].to_numpy() if "flagged" in table.columns else np.zeros(n, dtype=bool)
    bar_w = max(1.0, plot_w / n)
    for i in range(n):
        h = plot_h * float(volume[i]) / vmax
        x = left + plot_w * i / n
        is_alert = bool(flagged[i]) if i < len(flagged) else False
        canvas.rect(x, bottom - h, max(1.0, bar_w - 0.4), h, fill=COLOR_ALERT if is_alert else COLOR_NORMAL)

    # x 轴时间标签
    if n > 0 and len(buckets) == n:
        ticks = 6
        for i in range(ticks + 1):
            idx = min(n - 1, int(round((n - 1) * i / ticks)))
            x = left + plot_w * idx / n
            canvas.line(x, bottom, x, bottom + 4, stroke=COLOR_AXIS, width=1)
            label = pd.Timestamp(buckets[idx]).strftime("%m-%d %H:%M")
            canvas.text(x, bottom + 18, label, size=10, fill=COLOR_AXIS, anchor="middle")

    # 图例
    lx = right - 250
    ly = top + 6
    canvas.rect(lx, ly - 9, 11, 11, fill=COLOR_NORMAL)
    canvas.text(lx + 16, ly, "normal bucket", size=11, fill=COLOR_TEXT)
    canvas.rect(lx + 120, ly - 9, 11, 11, fill=COLOR_ALERT)
    canvas.text(lx + 136, ly, "anomalous bucket", size=11, fill=COLOR_TEXT)
    return canvas


def template_bar_chart(
    templates: Sequence[LogTemplate],
    top: int = 15,
    title: str = "Top log templates by frequency",
    width: int = 1000,
    bar_h: int = 22,
) -> Canvas:
    """模板频次横向条形图。"""
    items = [t for t in templates if t.count > 0]
    items = sorted(items, key=lambda t: (-t.count, t.template_id))[:top]
    height = 74 + bar_h * max(1, len(items)) + 16
    canvas = Canvas(width=width, height=height)
    left, right, top0 = 250, width - 90, 56
    plot_w = max(10, right - left)
    canvas.text(20, 24, title, size=15, weight="bold")
    canvas.text(20, 42, "bar length = occurrence count; label = masked template pattern", size=11, fill="#6b7c8d")
    vmax = max([t.count for t in items] + [1])
    for i, tmpl in enumerate(items):
        y = top0 + i * bar_h
        wpx = plot_w * tmpl.count / vmax
        canvas.rect(left, y, wpx, bar_h - 6, fill=COLOR_ACCENT if not tmpl.is_constant else "#7f8fa6")
        canvas.text(left - 8, y + bar_h - 11, "T" + str(tmpl.template_id), size=10, fill=COLOR_AXIS, anchor="end")
        canvas.text(left + wpx + 6, y + bar_h - 11, str(tmpl.count), size=10, fill=COLOR_TEXT)
        label = tmpl.pattern
        if len(label) > 46:
            label = label[:43] + "..."
        canvas.text(left + 6, y + bar_h - 11, label, size=10, fill="#ffffff" if wpx > 260 else COLOR_TEXT)
    return canvas


def grouped_bar_chart(
    group_labels: Sequence[str],
    series: Dict[str, Sequence[float]],
    title: str = "Metrics by method",
    y_max: float = 1.0,
    width: int = 980,
    height: int = 360,
) -> Canvas:
    """分组柱状图：每个 group 一组柱子（用于 P/R/F1 对比）。"""
    canvas = Canvas(width=width, height=height)
    left, right, top, bottom = 60, width - 20, 54, height - 76
    plot_w, plot_h = max(10, right - left), max(10, bottom - top)
    canvas.text(20, 24, title, size=15, weight="bold")
    canvas.text(20, 42, "grouped bars; y = score", size=11, fill="#6b7c8d")

    colors = ["#2d7dd2", "#e67e22", "#27ae60", "#8e44ad", "#c0392b", "#16a085"]
    for i in range(5):
        y = bottom - plot_h * i / 4.0
        canvas.line(left, y, right, y, stroke=COLOR_GRID, width=1)
        canvas.text(left - 8, y + 4, "%.2f" % (y_max * i / 4.0), size=10, fill=COLOR_AXIS, anchor="end")
    canvas.line(left, bottom, right, bottom, stroke=COLOR_AXIS, width=1)
    canvas.line(left, top, left, bottom, stroke=COLOR_AXIS, width=1)

    n = max(1, len(group_labels))
    names = list(series.keys())
    slot = plot_w / n
    bar_w = max(2.0, (slot - 14) / max(1, len(names)))
    for gi, label in enumerate(group_labels):
        x0 = left + gi * slot + 7
        for si, name in enumerate(names):
            values = series[name]
            value = float(values[gi]) if gi < len(values) else 0.0
            h = plot_h * max(0.0, min(y_max, value)) / y_max
            canvas.rect(x0 + si * bar_w, bottom - h, max(1.0, bar_w - 2), h, fill=colors[si % len(colors)])
        canvas.text(x0 + slot / 2 - 7, bottom + 18, str(label), size=10, fill=COLOR_AXIS, anchor="middle")
    # 图例
    lx = right - 40 - 120 * len(names)
    for si, name in enumerate(names):
        canvas.rect(lx + 120 * si, top + 2, 11, 11, fill=colors[si % len(colors)])
        canvas.text(lx + 120 * si + 16, top + 11, name, size=11, fill=COLOR_TEXT)
    return canvas


def level_bar_chart(
    level_counts: pd.DataFrame,
    title: str = "Log level distribution",
    width: int = 520,
    height: int = 300,
) -> Canvas:
    """级别分布柱状图。"""
    canvas = Canvas(width=width, height=height)
    totals = level_counts.sum(axis=0).sort_values(ascending=False)
    left, right, top, bottom = 56, width - 20, 50, height - 40
    plot_w, plot_h = right - left, bottom - top
    canvas.text(20, 24, title, size=14, weight="bold")
    if totals.empty:
        return canvas
    vmax = float(max(1, totals.max()))
    n = len(totals)
    bar_w = plot_w / max(1, n)
    for i, (name, value) in enumerate(totals.items()):
        h = plot_h * float(value) / vmax
        x = left + i * bar_w
        color = COLOR_ALERT if str(name).upper() in ("ERROR", "FATAL", "WARN") else COLOR_NORMAL
        canvas.rect(x + 4, bottom - h, max(2.0, bar_w - 8), h, fill=color)
        canvas.text(x + bar_w / 2, bottom + 16, str(name), size=10, fill=COLOR_AXIS, anchor="middle")
        canvas.text(x + bar_w / 2, bottom - h - 5, str(int(value)), size=10, fill=COLOR_TEXT, anchor="middle")
    canvas.line(left, bottom, right, bottom, stroke=COLOR_AXIS, width=1)
    canvas.line(left, top, left, bottom, stroke=COLOR_AXIS, width=1)
    return canvas
