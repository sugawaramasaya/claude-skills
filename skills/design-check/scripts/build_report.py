# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy"]
# ///
"""design-check: report_spec.json を自己完結 HTML レポートへ組版する CLI。

- 画像は長辺 1200px に縮小してから data URI 化する（Artifact 肥大対策）
- グラフは svg_charts.py でインライン SVG として埋め込む（外部リソースなし）
- 生成 HTML に "http://" / "https://" を含まないことを自己検査し、
  違反時は非0で終了する（Artifact の CSP 対策）
- 出力 HTML は <!doctype>/<html>/<head>/<body> を含めない
  （Artifact 側が骨格を提供する前提のページ内容のみを書く）

使い方:
    uv run build_report.py --spec report_spec.json --out report.html

標準出力には {"html_path", "size_bytes", "self_check"} の JSON を書く。
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import sys
from pathlib import Path

from PIL import Image

import common as c
import svg_charts

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "report_template.html"

MAX_EDGE = 1200


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _esc_multiline(s) -> str:
    return _esc(s).replace("\n", "<br/>")


def embed_image_data_uri(path, max_edge: int = MAX_EDGE) -> str:
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h)) if max(w, h) else 1.0
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def score_to_flag(score: float) -> str:
    """figma-ia-checker と同じ 1-5 スコア規約: 5-4=green / 3=yellow / 2-1=red"""
    if score >= 4:
        return "green"
    if score >= 3:
        return "yellow"
    return "red"


def render_assets(assets: list[dict]) -> str:
    if not assets:
        return ""
    figures = []
    for a in assets:
        path = a.get("path")
        if not path or not Path(path).exists():
            continue
        data_uri = embed_image_data_uri(path)
        caption = a.get("caption", "")
        figures.append(f'<figure><img src="{data_uri}" alt="{_esc(caption)}"/>'
                       f'<figcaption>{_esc(caption)}</figcaption></figure>')
    if not figures:
        return ""
    return f'<div class="dc-assets">{"".join(figures)}</div>'


def render_charts(charts: list[dict]) -> str:
    if not charts:
        return ""
    parts = []
    for chart_spec in charts:
        svg = svg_charts.render_chart(chart_spec)
        parts.append(f'<div class="dc-chart">{svg}</div>')
    return "".join(parts)


def render_table(table: dict | None) -> str:
    if not table:
        return ""
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    thead = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    tbody_rows = []
    for row in rows:
        cells = "".join(f"<td>{_esc(cell)}</td>" for cell in row)
        tbody_rows.append(f"<tr>{cells}</tr>")
    return (f'<table class="dc-table"><thead><tr>{thead}</tr></thead>'
            f'<tbody>{"".join(tbody_rows)}</tbody></table>')


def render_category(cat: dict) -> str:
    score = cat.get("score", 3)
    flag = score_to_flag(score)
    emoji = c.FLAG_EMOJI[flag]
    priority = cat.get("priority", "")
    issues = cat.get("issues", [])
    suggestions = cat.get("suggestions", [])

    issues_html = "".join(f"<li>{_esc_multiline(i)}</li>" for i in issues)
    suggestions_html = "".join(f"<li>{_esc_multiline(s)}</li>" for s in suggestions)

    priority_html = f'<span class="dc-priority">優先度: {_esc(priority)}</span>' if priority else ""

    parts = [
        '<div class="dc-category">',
        f'<h2>{emoji} {_esc(cat.get("name", ""))} — {score}/5{priority_html}</h2>',
    ]
    if issues:
        parts.append(f'<h3>課題</h3><ul>{issues_html}</ul>')
    if suggestions:
        parts.append(f'<h3>改善提案</h3><ul>{suggestions_html}</ul>')
    if cat.get("table"):
        parts.append(render_table(cat["table"]))
    charts_html = render_charts(cat.get("charts", []))
    if charts_html:
        parts.append(charts_html)
    assets_html = render_assets(cat.get("assets", []))
    if assets_html:
        parts.append(assets_html)
    parts.append("</div>")
    return "".join(parts)


def render_comparison(comparison: dict | None) -> str:
    if not comparison:
        return ""
    parts = ['<div class="dc-comparison">', "<h2>修正案の比較</h2>"]
    caption = comparison.get("caption")
    if caption:
        parts.append(f"<p>{_esc_multiline(caption)}</p>")
    charts_html = render_charts(comparison.get("charts", []))
    if charts_html:
        parts.append(charts_html)
    image = comparison.get("image")
    if image and Path(image).exists():
        data_uri = embed_image_data_uri(image)
        parts.append(f'<div class="dc-assets"><figure><img src="{data_uri}" alt="修正案比較"/></figure></div>')
    assets_html = render_assets(comparison.get("assets", []))
    if assets_html:
        parts.append(assets_html)
    parts.append("</div>")
    return "".join(parts)


def build_html(spec: dict) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    categories_html = "".join(render_category(cat) for cat in spec.get("categories", []))
    comparison_html = render_comparison(spec.get("comparison"))

    overall_score = spec.get("overall_score", 0)
    overall_flag = score_to_flag(overall_score)
    overall_score_text = f'{c.FLAG_EMOJI[overall_flag]} {overall_score}/5'

    html_out = (
        template
        .replace("{{TITLE}}", _esc(spec.get("title", "design-check レポート")))
        .replace("{{TARGET}}", _esc(spec.get("target", "")))
        .replace("{{OVERALL_SCORE}}", overall_score_text)
        .replace("{{OVERALL_SUMMARY}}", _esc_multiline(spec.get("overall_summary", "")))
        .replace("{{CATEGORIES}}", categories_html)
        .replace("{{COMPARISON}}", comparison_html)
    )
    return html_out


def self_check(html_out: str) -> list[str]:
    violations = []
    if "http://" in html_out:
        violations.append("http:// を含む文字列が検出されました")
    if "https://" in html_out:
        violations.append("https:// を含む文字列が検出されました")
    for forbidden_tag in ("<!doctype", "<html", "<head", "<body"):
        if forbidden_tag in html_out.lower():
            violations.append(f"禁止タグ {forbidden_tag} が含まれています（Artifact が骨格を提供するため不要）")
    return violations


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="design-check: report_spec.json から自己完結 HTML を組版する")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    spec = c.load_json(args.spec)
    html_out = build_html(spec)

    violations = self_check(html_out)
    if violations:
        print(f"error: build_report self-check failed: {violations}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_out, encoding="utf-8")
    size_bytes = out_path.stat().st_size

    c.print_json({
        "html_path": str(out_path),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 3),
        "self_check": "passed",
    })


if __name__ == "__main__":
    main()
