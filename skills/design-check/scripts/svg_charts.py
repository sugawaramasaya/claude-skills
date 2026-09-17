# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy"]
# ///
"""design-check: build_report.py が呼び出すインライン SVG チャート生成モジュール。

すべて外部リソース・外部URLに依存しない自己完結 SVG 文字列を返す。
build_report.py から import して使う。単体 CLI としても動作確認できる。
"""

from __future__ import annotations

import argparse
import html
import json

import common as c

FONT_FAMILY = "-apple-system, BlinkMacSystemFont, 'Hiragino Sans', 'Noto Sans JP', sans-serif"


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def bar_chart(items: list[dict], width: int = 480, title: str | None = None) -> str:
    """items: [{"label": str, "value": float, "color": "#RRGGBB"?}] の横棒グラフ。"""
    if not items:
        return "<svg></svg>"
    row_h = 28
    pad_top = 28 if title else 8
    height = pad_top + row_h * len(items) + 8
    max_val = max((it["value"] for it in items), default=1) or 1
    bar_area_x = 140
    bar_max_w = width - bar_area_x - 60

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'style="font-family:{FONT_FAMILY}">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="transparent"/>')
    if title:
        parts.append(f'<text x="0" y="18" font-size="13" font-weight="600" '
                      f'fill="var(--dc-fg, #1a1a2e)">{_esc(title)}</text>')
    for i, it in enumerate(items):
        y = pad_top + i * row_h
        color = it.get("color", "#3B82F6")
        w = max(2.0, it["value"] / max_val * bar_max_w)
        parts.append(f'<text x="0" y="{y + row_h * 0.65:.1f}" font-size="12" '
                      f'fill="var(--dc-fg, #1a1a2e)">{_esc(it["label"])}</text>')
        parts.append(f'<rect x="{bar_area_x}" y="{y + 4}" width="{w:.1f}" height="{row_h - 10}" '
                      f'rx="3" fill="{_esc(color)}"/>')
        value_label = it.get("value_label", f'{it["value"]:.1f}')
        parts.append(f'<text x="{bar_area_x + w + 8:.1f}" y="{y + row_h * 0.65:.1f}" font-size="12" '
                      f'fill="var(--dc-fg-muted, #6b7280)">{_esc(value_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def comparison_bar_chart(label_before: str, value_before: float, label_after: str, value_after: float,
                          unit: str = "px", width: int = 420, title: str | None = None) -> str:
    """「現状 Δ46px -> 案A Δ2px」のような Before/After 比較の横棒グラフ。"""
    items = [
        {"label": label_before, "value": value_before, "color": "#EC4899",
         "value_label": f"{value_before:.1f}{unit}"},
        {"label": label_after, "value": value_after, "color": "#10B981",
         "value_label": f"{value_after:.1f}{unit}"},
    ]
    return bar_chart(items, width=width, title=title)


def projection_profile_chart(gaps_px: list[float], median_gap_px: float, width: int = 480, height: int = 140,
                              title: str | None = None) -> str:
    """カーニングのギャップ列を棒グラフで示し、中央値からの乖離が大きいものを赤で強調する。"""
    pad_top = 28 if title else 8
    chart_h = height - pad_top - 24
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'style="font-family:{FONT_FAMILY}">']
    if title:
        parts.append(f'<text x="0" y="18" font-size="13" font-weight="600" '
                      f'fill="var(--dc-fg, #1a1a2e)">{_esc(title)}</text>')
    if not gaps_px:
        parts.append('<text x="0" y="40" font-size="12" fill="var(--dc-fg-muted, #6b7280)">'
                      'ギャップが検出されませんでした</text></svg>')
        return "".join(parts)

    max_val = max(max(gaps_px), median_gap_px, 1)
    n = len(gaps_px)
    bar_w = max(8.0, (width - 16) / n * 0.6)
    slot_w = (width - 16) / n

    # 中央値ライン
    median_y = pad_top + chart_h - (median_gap_px / max_val * chart_h)
    parts.append(f'<line x1="0" y1="{median_y:.1f}" x2="{width}" y2="{median_y:.1f}" '
                 f'stroke="var(--dc-fg-muted, #9ca3af)" stroke-width="1" stroke-dasharray="4,3"/>')
    parts.append(f'<text x="{width - 4}" y="{median_y - 4:.1f}" font-size="10" text-anchor="end" '
                 f'fill="var(--dc-fg-muted, #6b7280)">中央値 {median_gap_px:.1f}px</text>')

    for i, g in enumerate(gaps_px):
        dev = abs(g - median_gap_px) / median_gap_px * 100 if median_gap_px else 0
        color = "#EC4899" if dev >= 25 else ("#F59E0B" if dev >= 10 else "#10B981")
        bar_h = (g / max_val) * chart_h
        x = 8 + i * slot_w
        y = pad_top + chart_h - bar_h
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
                     f'rx="2" fill="{color}"/>')
        parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{pad_top + chart_h + 14}" font-size="10" '
                     f'text-anchor="middle" fill="var(--dc-fg-muted, #6b7280)">{g:.0f}</text>')
    parts.append("</svg>")
    return "".join(parts)


def color_swatch_row(colors: list[dict], width: int = 480, swatch: int = 56, title: str | None = None) -> str:
    """colors: [{"hex": "#RRGGBB", "label": str?}] のスウォッチ一覧。"""
    pad_top = 28 if title else 8
    cols = max(1, width // (swatch + 12))
    rows = (len(colors) + cols - 1) // cols if colors else 1
    height = pad_top + rows * (swatch + 28)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'style="font-family:{FONT_FAMILY}">']
    if title:
        parts.append(f'<text x="0" y="18" font-size="13" font-weight="600" '
                      f'fill="var(--dc-fg, #1a1a2e)">{_esc(title)}</text>')
    for i, item in enumerate(colors):
        col, row = i % cols, i // cols
        x = col * (swatch + 12)
        y = pad_top + row * (swatch + 28)
        hexcode = item.get("hex", "#CCCCCC")
        parts.append(f'<rect x="{x}" y="{y}" width="{swatch}" height="{swatch}" rx="6" '
                     f'fill="{_esc(hexcode)}" stroke="var(--dc-border, #d1d5db)" stroke-width="1"/>')
        label = item.get("label", hexcode)
        parts.append(f'<text x="{x + swatch / 2:.1f}" y="{y + swatch + 14}" font-size="10" '
                     f'text-anchor="middle" fill="var(--dc-fg-muted, #6b7280)">{_esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


CHART_BUILDERS = {
    "bar": lambda data: bar_chart(data.get("items", []), width=data.get("width", 480), title=data.get("title")),
    "comparison_bar": lambda data: comparison_bar_chart(
        data.get("label_before", "現状"), data.get("value_before", 0),
        data.get("label_after", "案A"), data.get("value_after", 0),
        unit=data.get("unit", "px"), width=data.get("width", 420), title=data.get("title")),
    "projection_profile": lambda data: projection_profile_chart(
        data.get("gaps_px", []), data.get("median_gap_px", 0),
        width=data.get("width", 480), height=data.get("height", 140), title=data.get("title")),
    "color_swatches": lambda data: color_swatch_row(
        data.get("colors", []), width=data.get("width", 480), swatch=data.get("swatch", 56),
        title=data.get("title")),
}


def render_chart(chart_spec: dict) -> str:
    """report_spec.json の {"type": ..., "data": {...}} から SVG 文字列を生成する。"""
    builder = CHART_BUILDERS.get(chart_spec.get("type"))
    if not builder:
        return f"<!-- 未知のチャートタイプ: {_esc(chart_spec.get('type'))} -->"
    return builder(chart_spec.get("data", {}))


def main(argv=None) -> None:
    """動作確認用の簡易 CLI: chart_spec.json を渡すと SVG を stdout に出す。"""
    parser = argparse.ArgumentParser(description="svg_charts の単体動作確認用 CLI")
    parser.add_argument("chart_spec_json")
    args = parser.parse_args(argv)
    spec = c.load_json(args.chart_spec_json)
    print(render_chart(spec))


if __name__ == "__main__":
    main()
