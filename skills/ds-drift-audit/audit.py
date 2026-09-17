#!/usr/bin/env python3
"""
ds-drift-audit — ACME デザインシステム ドリフト監査ツール（プリミティブ照合監査）

== 方針転換の背景 ==
Figma のセマンティック変数（ds/semantic/*）の解決値は Figma API pro tier では
MCP 経由で取得できないことが確定した。そのため本ツールは以下に方針転換する:

    「バンドルの色トークンの値が、Figma のライブ・プリミティブ色
     （ds/primitive/color/scale/<Scale>/<n> ※原文ママでタイポ含む）の
     いずれかの値と一致するか」を照合する。

セマンティック変数のリネーム・再エイリアス検出は対象外（できない）。
プリミティブの被覆範囲は取得元ノード（例: fileKey/nodeId で1回取得したスナップショット）に
限定される。ブランド固有のプリミティブが別ノードに存在する場合、
「プリミティブ非在」判定に偽陽性が出る可能性がある。この前提はHTMLレポート冒頭に明記する。

デザイントークンのバンドル（コード側コピー: colors_and_type.css + _ds_manifest.json）を
パースし、Figma live primitives（figma_primitives.json = {primitive_name: hexvalue}）と
色値レベルで突合する。

CLI:
    python3 audit.py --bundle-dir <dir> --figma-primitives <figma_primitives.json> --out-dir <dir>

出力:
    <out-dir>/ds-drift-report.json  … 機械可読レポート
    <out-dir>/ds-drift-report.html  … 人間向け自己完結HTMLレポート
    stdout                          … ターミナル要約
"""

import argparse
import html
import json
import re
import os
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 既定パス
# ---------------------------------------------------------------------------
DEFAULT_SCRATCHPAD = Path(
    os.environ.get("DS_DRIFT_SCRATCHPAD")
    or (Path(tempfile.gettempdir()) / "ds-drift-audit")
)
DEFAULT_BUNDLE_DIR = DEFAULT_SCRATCHPAD / "bundle"
DEFAULT_FIGMA_PRIMITIVES = DEFAULT_SCRATCHPAD / "figma_primitives.json"
DEFAULT_OUT_DIR = Path.home() / "dev" / "digests"

CSS_FILENAME = "colors_and_type.css"
MANIFEST_FILENAME = "_ds_manifest.json"

TAG_RE = re.compile(r"\[([^\]]+)\]")
# Figma プリミティブ/セマンティック風の "大文字始まり + 末尾数字" トークン
# (例: ScaleA9, ScaleC12, ScaleAAlpha8, ScaleD9)。カラーの hint 抽出に使う。
HINT_RE = re.compile(r"\b([A-Z][A-Za-z]*\d+)\b")
DIFF_DETAIL_RE = re.compile(r"Figma=([^\s]+)\s+Flutter=([^\s]+)")
# bundle の figma_hint (例 "ScaleA9") を (スケール名, 段数) に分解
PRIMITIVE_SCALE_RE = re.compile(r"^([A-Za-z]+)(\d+)$")
# Figma primitive 変数名 (例 "ds/primitive/color/scale/Blue/9") の末尾から
# (スケール名, 段数) を抽出
PRIMITIVE_NAME_TAIL_RE = re.compile(r"/([A-Za-z0-9 _-]+)/(\d+)$")

HEX3_RE = re.compile(r"^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$")
HEX6_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
HEX8_RE = re.compile(r"^#[0-9a-fA-F]{8}$")
RGBA_RE = re.compile(
    r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$",
    re.I,
)


# ---------------------------------------------------------------------------
# 色値正規化
# ---------------------------------------------------------------------------
def normalize_color(value: str) -> str:
    """色値を小文字hexに正規化。#RGB→#RRGGBB展開。rgba()等はそのまま(空白正規化のみ)。"""
    v = value.strip()
    m = HEX3_RE.match(v)
    if m:
        r, g, b = m.groups()
        return f"#{r * 2}{g * 2}{b * 2}".lower()
    if HEX6_RE.match(v) or HEX8_RE.match(v):
        return v.lower()
    if v.lower().startswith(("rgba(", "rgb(")):
        inner = v[v.index("(") + 1 : v.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        prefix = "rgba" if v.lower().startswith("rgba") else "rgb"
        return f"{prefix}({', '.join(parts)})"
    return v


def is_color_like(value) -> bool:
    if value is None:
        return False
    v = value.strip()
    return bool(
        HEX3_RE.match(v)
        or HEX6_RE.match(v.lower())
        or HEX8_RE.match(v.lower())
        or v.lower().startswith(("rgba(", "rgb("))
    )


def normalize_hex8(value):
    """
    色値を「8桁hex（alpha込み・小文字）」に正規化する共通関数。
    #rrggbb → #rrggbbff / #rgb → 展開して #rrggbbff / #rrggbbaa → そのまま(小文字化)
    rgba(r,g,b,a) → #rrggbb+round(a*255)を16進2桁 / rgb(r,g,b) → #rrggbb+ff
    パース不能なら None。
    """
    if value is None:
        return None
    v = value.strip()

    m3 = HEX3_RE.match(v)
    if m3:
        r, g, b = m3.groups()
        v = f"#{r * 2}{g * 2}{b * 2}"

    if HEX6_RE.match(v):
        return (v + "ff").lower()
    if HEX8_RE.match(v):
        return v.lower()

    m = RGBA_RE.match(v)
    if m:
        r_s, g_s, b_s, a_s = m.groups()
        r, g, b = int(float(r_s)), int(float(g_s)), int(float(b_s))
        alpha = float(a_s) if a_s is not None else 1.0
        a255 = max(0, min(255, round(alpha * 255)))
        return f"#{r:02x}{g:02x}{b:02x}{a255:02x}".lower()

    return None


# ---------------------------------------------------------------------------
# 1. バンドルのパース（従来ロジックを維持）
# ---------------------------------------------------------------------------
def parse_comment(comment: str):
    """トークン付随コメントから (source_tags, figma_hint, has_diff_flag, diff_detail) を抽出。"""
    if comment is None:
        return [], None, False, None

    raw_tags = TAG_RE.findall(comment)
    source_tags = [t.strip() for t in raw_tags]

    has_diff_flag = "⚠diff" in comment

    diff_detail = None
    dm = DIFF_DETAIL_RE.search(comment)
    if dm:
        diff_detail = {"figma": dm.group(1), "flutter": dm.group(2)}

    # em dash (—) 以降は診断ノート（diff注記等）。hint 抽出は本体部分のみで行う。
    main_part = comment.split("—")[0]
    main_part_no_tags = TAG_RE.sub(" ", main_part)
    hm = HINT_RE.search(main_part_no_tags)
    figma_hint = hm.group(1) if hm else None

    return source_tags, figma_hint, has_diff_flag, diff_detail


# バンドルのトークン接頭辞。環境によって違うので定数にしておく（CLI の --token-prefix で上書き可）。
# 接頭辞で絞るのは、DS 以外の CSS 変数を拾わないため。
TOKEN_PREFIX = "ds-"


def build_token_decl_re(prefix: str):
    return re.compile(
        r"--(" + re.escape(prefix) + r"[\w-]+):\s*(.*?);\s*(?:/\*(.*?)\*/)?\s*(?=\n|$)",
        re.S,
    )


TOKEN_DECL_RE = build_token_decl_re(TOKEN_PREFIX)


def parse_block(block_text: str):
    """:root {...} や [data-theme="dark"] {...} の中身から {name: (value, comment)} を得る。"""
    result = {}
    for m in TOKEN_DECL_RE.finditer(block_text):
        name = "--" + m.group(1)
        value = m.group(2).strip()
        value = re.sub(r"\s*\n\s*", " ", value).strip()
        comment = m.group(3)
        if comment is not None:
            comment = comment.strip()
        result[name] = (value, comment)
    return result


def parse_bundle_css(css_text: str):
    """
    colors_and_type.css をパースし、
    {name: {value_light, value_dark, comment_light, comment_dark}} を返す。
    """
    root_m = re.search(r":root\s*\{(.*?)\n\}", css_text, re.S)
    dark_m = re.search(r'\[data-theme="dark"\]\s*\{(.*?)\n\}', css_text, re.S)
    if not root_m:
        raise ValueError(":root {...} ブロックが見つかりません")
    if not dark_m:
        raise ValueError('[data-theme="dark"] {...} ブロックが見つかりません')

    light = parse_block(root_m.group(1))
    dark = parse_block(dark_m.group(1))

    tokens = {}
    for name, (value, comment) in light.items():
        dark_value, dark_comment = dark.get(name, (None, None))
        tokens[name] = {
            "value_light": value,
            "value_dark": dark_value,
            "comment_light": comment,
            "comment_dark": dark_comment,
        }
    return tokens


def load_manifest_kinds(manifest_path: Path):
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    kinds = {}
    for tok in data.get("tokens", []):
        kinds[tok["name"]] = tok.get("kind")
    return kinds


def build_bundle_records(bundle_dir: Path):
    css_path = bundle_dir / CSS_FILENAME
    manifest_path = bundle_dir / MANIFEST_FILENAME
    css_text = css_path.read_text(encoding="utf-8")
    manifest_kinds = load_manifest_kinds(manifest_path)

    raw_tokens = parse_bundle_css(css_text)

    records = []
    for name, t in raw_tokens.items():
        kind = manifest_kinds.get(name, "unknown")

        vl = t["value_light"]
        vd = t["value_dark"]
        if is_color_like(vl):
            vl = normalize_color(vl)
        if vd is not None and is_color_like(vd):
            vd = normalize_color(vd)

        tags_l, hint_l, diff_l, detail_l = parse_comment(t["comment_light"])
        tags_d, hint_d, diff_d, detail_d = parse_comment(t["comment_dark"])

        source_tags = tags_l if tags_l else tags_d
        figma_hint = hint_l if hint_l else hint_d
        has_diff_flag = diff_l or diff_d
        diff_detail = detail_l or detail_d

        records.append(
            {
                "name": name,
                "kind": kind,
                "value_light": vl,
                "value_dark": vd,
                "figma_hint": figma_hint,
                "source_tags": source_tags,
                "has_diff_flag": has_diff_flag,
                "diff_detail": diff_detail,
                "comment_light": t["comment_light"],
                "comment_dark": t["comment_dark"],
            }
        )
    return records


# ---------------------------------------------------------------------------
# 2. Figma ライブ・プリミティブのロード & 逆引きインデックス
# ---------------------------------------------------------------------------
def load_figma_primitives(path: Path):
    """
    figma_primitives.json ({primitive_name: hex値(6桁/8桁)}) をロードし、
    (raw_map, normalized_map, reverse_index, unparsed_names) を返す。
    reverse_index: 正規化済み8桁hex値 -> [primitive_name, ...]
    """
    if not path.exists():
        return {}, {}, {}, []

    raw_map = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_map, dict):
        raise ValueError("figma_primitives.json はフラットな {name: hex値} の辞書である必要があります")

    normalized_map = {}
    reverse_index = {}
    unparsed = []
    for name, raw_value in raw_map.items():
        norm = normalize_hex8(raw_value)
        if norm is None:
            unparsed.append(name)
            continue
        normalized_map[name] = norm
        reverse_index.setdefault(norm, []).append(name)

    return raw_map, normalized_map, reverse_index, unparsed


def parse_primitive_name_tail(name: str):
    """'ds/primitive/color/scale/Blue/9' -> ('Blue', '9')。末尾が scale/n 形式でなければ (None, None)。"""
    m = PRIMITIVE_NAME_TAIL_RE.search(name)
    if m:
        return m.group(1), m.group(2)
    return None, None


# ---------------------------------------------------------------------------
# 3・4. プリミティブ照合分類 & 自己タグ整合検証
# ---------------------------------------------------------------------------
DARK_UNCHECKED = "未照合(ダークprimitive未取得)"


def _match_mode(raw_value, reverse_index):
    """1モードの値をプリミティブ集合で照合。戻り値 (status, normalized, matched_names)。
    status: "一致" / "非在" / "対象外(非色)"。"""
    if raw_value is None or not is_color_like(raw_value):
        return "対象外(非色)", None, []
    norm = normalize_hex8(raw_value)
    matched = reverse_index.get(norm, []) if norm else []
    return ("一致" if matched else "非在"), norm, matched


def classify_token_against_primitives(rec, reverse_index, reverse_index_dark=None):
    """
    1トークンを light / dark 独立にプリミティブ照合する。

    - light_status: light値を（light）プリミティブ集合で照合 → 一致/非在/対象外(非色)
    - dark_status : dark値を dark プリミティブ集合で照合。dark集合が未提供なら
                    DARK_UNCHECKED（＝今回スコープ外・偽陽性を出さない）。
    - primitive_status（総合）は **light を基準**にする（一致/非在/対象外(非色)）。
      dark の未照合は総合判定にも重大アラートにも影響させない。
    """
    dark_provided = bool(reverse_index_dark)

    light_status, light_norm, light_matched = _match_mode(rec["value_light"], reverse_index)

    # dark
    if rec["value_dark"] is None or not is_color_like(rec["value_dark"]):
        dark_status, dark_norm, dark_matched = "対象外(非色)", None, []
    elif dark_provided:
        dark_status, dark_norm, dark_matched = _match_mode(rec["value_dark"], reverse_index_dark)
    else:
        dark_status, dark_norm, dark_matched = DARK_UNCHECKED, normalize_hex8(rec["value_dark"]), []

    mode_detail = [
        {"mode": "light", "status": light_status, "normalized": light_norm, "matched_names": light_matched},
        {"mode": "dark", "status": dark_status, "normalized": dark_norm, "matched_names": dark_matched},
    ]

    # 総合分類は light 基準
    if light_status == "対象外(非色)":
        primitive_status = "対象外(非色)"
    elif light_status == "一致":
        primitive_status = "プリミティブ一致"
    else:
        primitive_status = "プリミティブ非在"

    matched_primitive_names = []
    seen = set()
    for n in light_matched + dark_matched:
        if n not in seen:
            seen.add(n)
            matched_primitive_names.append(n)

    # 命名差チェック: バンドルの figma_hint（バンドル側の命名スケール, 例 ScaleA9）と
    # light で値一致した Figma primitive 名（Radix命名スケール, 例 Blue/9）を比較。
    naming_diff = None
    figma_hint = rec.get("figma_hint")
    if figma_hint and light_matched:
        hm = PRIMITIVE_SCALE_RE.match(figma_hint)
        if hm:
            bundle_scale, bundle_step = hm.groups()
            for prim_name in light_matched:
                fig_scale, fig_step = parse_primitive_name_tail(prim_name)
                if fig_scale is None:
                    continue
                if fig_scale.lower() != bundle_scale.lower():
                    naming_diff = {
                        "bundle_hint": figma_hint,
                        "bundle_scale": bundle_scale,
                        "bundle_step": bundle_step,
                        "figma_primitive": prim_name,
                        "figma_scale": fig_scale,
                        "figma_step": fig_step,
                    }
                    break

    return {
        **rec,
        "primitive_status": primitive_status,
        "light_status": light_status,
        "dark_status": dark_status,
        "mode_detail": mode_detail,
        "matched_primitive_names": matched_primitive_names,
        "naming_diff": naming_diff,
    }


def classify_and_audit(records, reverse_index, reverse_index_dark=None):
    return [
        classify_token_against_primitives(rec, reverse_index, reverse_index_dark)
        for rec in records
    ]


def build_consistency_alerts(result_tokens):
    """自己タグ（[Figma確定] / ⚠diff）と実測（プリミティブ照合）の矛盾を抽出。"""
    alerts = []
    for t in result_tokens:
        is_figma_kakutei = any("Figma確定" in tag for tag in t["source_tags"])

        if is_figma_kakutei and t["primitive_status"] == "プリミティブ非在":
            alerts.append(
                {
                    "severity": "重大",
                    "token": t["name"],
                    "reason": (
                        "[Figma確定]タグ付きだがライブ・プリミティブ値に一致するものが無い"
                        "（写経後にドリフト、またはブランド固有primitiveが取得ノード外にある可能性）"
                    ),
                    "detail": {
                        "primitive_status": t["primitive_status"],
                        "mode_detail": t["mode_detail"],
                    },
                }
            )

        if t["has_diff_flag"]:
            alerts.append(
                {
                    "severity": "情報",
                    "token": t["name"],
                    "reason": (
                        "バンドル側で自己申告済みの⚠diff注記（元はFigmaセマンティック値とFlutter値の差異）。"
                        "本監査はセマンティック値ではなくプリミティブ存在チェックのため直接の再現確認はできない。"
                        "参考情報として現在のプリミティブ照合結果を記録。"
                    ),
                    "detail": {
                        "self_reported_diff": t["diff_detail"],
                        "primitive_status": t["primitive_status"],
                    },
                }
            )

    alerts.sort(key=lambda a: 0 if a["severity"] == "重大" else 1)
    return alerts


# ---------------------------------------------------------------------------
# 5. 出力: JSON
# ---------------------------------------------------------------------------
def build_report(result_tokens, alerts, primitives_meta):
    by_primitive_status = {}
    by_dark_status = {}
    for t in result_tokens:
        by_primitive_status[t["primitive_status"]] = (
            by_primitive_status.get(t["primitive_status"], 0) + 1
        )
        ds = t.get("dark_status", "対象外(非色)")
        by_dark_status[ds] = by_dark_status.get(ds, 0) + 1

    color_tokens = [t for t in result_tokens if t["primitive_status"] != "対象外(非色)"]
    naming_diffs = [t for t in result_tokens if t["naming_diff"]]
    non_color_count = by_primitive_status.get("対象外(非色)", 0)

    non_existent = [t["name"] for t in result_tokens if t["primitive_status"] == "プリミティブ非在"]

    summary = {
        "total_tokens": len(result_tokens),
        "color_token_count": len(color_tokens),
        "non_color_token_count": non_color_count,
        "by_primitive_status": by_primitive_status,
        "by_dark_status": by_dark_status,
        "primitive_non_existent_count": len(non_existent),
        "naming_diff_count": len(naming_diffs),
        "consistency_alert_count": len(alerts),
        "consistency_alert_critical_count": sum(1 for a in alerts if a["severity"] == "重大"),
        "figma_primitives_total_count": primitives_meta["total_count"],
        "figma_primitives_unparsed_count": len(primitives_meta["unparsed"]),
    }

    return {
        "generated_by": "ds-drift-audit",
        "premise": (
            "Figmaセマンティック変数(ds/semantic/*)の解決値はFigma API pro tierではMCP経由で"
            "取得不可のため、本監査は『バンドル色値がライブ・プリミティブ(ds/primitive/color/scale/*)"
            "に存在するか』の照合に限定される。セマンティックの再エイリアス検出は対象外。"
            "また今回取得したプリミティブは**lightモードのみ**で、dark値の照合は未実施"
            "（各トークンのdark_status=『未照合』。dark primitive取得 or プラグインエクスポートで別途対応予定）。"
            "総合判定(プリミティブ一致/非在)と重大アラートは**lightモード基準**。"
            "プリミティブ被覆は取得元ノードが返した集合に限られ、ブランド固有primitiveが別ノードに"
            "ある場合は『プリミティブ非在』判定に偽陽性の可能性がある。"
        ),
        "summary": summary,
        "tokens": result_tokens,
        "primitive_non_existent": non_existent,
        "naming_diffs": [
            {
                "token": t["name"],
                "kind": t["kind"],
                "source_tags": t["source_tags"],
                **t["naming_diff"],
            }
            for t in naming_diffs
        ],
        "consistency_alerts": alerts,
        "figma_primitives_meta": primitives_meta,
    }


# ---------------------------------------------------------------------------
# 5. 出力: HTML
# ---------------------------------------------------------------------------
def esc(s):
    if s is None:
        return ""
    return html.escape(str(s))


def color_swatch_html(value):
    if not value or not is_color_like(value):
        return ""
    safe = esc(value)
    return f'<span class="swatch" style="background:{safe}"></span>'


PRIMITIVE_STATUS_BADGE_CLASS = {
    "プリミティブ一致": "badge-ok",
    "プリミティブ非在": "badge-diff",
    "対象外(非色)": "badge-unknown",
}

DARK_STATUS_BADGE_CLASS = {
    "一致": "badge-ok",
    "非在": "badge-diff",
    DARK_UNCHECKED: "badge-warn",
    "対象外(非色)": "badge-unknown",
}


def render_alerts_html(alerts):
    if not alerts:
        return '<p class="muted">整合性アラートはありません。</p>'
    rows = []
    for a in alerts:
        sev_class = "badge-critical" if a["severity"] == "重大" else "badge-info"
        detail = esc(json.dumps(a["detail"], ensure_ascii=False))
        rows.append(
            f"""
            <div class="alert-item {'alert-critical' if a['severity']=='重大' else 'alert-info'}">
              <span class="badge {sev_class}">{esc(a['severity'])}</span>
              <code class="tok-name">{esc(a['token'])}</code>
              <span class="alert-reason">{esc(a['reason'])}</span>
              <pre class="alert-detail">{detail}</pre>
            </div>"""
        )
    return "\n".join(rows)


def render_naming_diffs_html(naming_diffs):
    if not naming_diffs:
        return '<p class="muted">命名差（値一致・スケール名相違）は検出されませんでした。</p>'
    rows = []
    for nd in naming_diffs:
        rows.append(
            f"""
            <tr>
              <td><code class="tok-name">{esc(nd['token'])}</code></td>
              <td>{esc(nd['bundle_hint'])}</td>
              <td>{esc(nd['figma_primitive'])}</td>
              <td>{esc(nd['bundle_scale'])} (ACME) ⇄ {esc(nd['figma_scale'])} (Figma/Radix)</td>
            </tr>"""
        )
    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr><th>token</th><th>bundle hint</th><th>matched figma primitive</th><th>命名差</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def render_token_rows(tokens):
    rows = []
    for t in tokens:
        status_class = PRIMITIVE_STATUS_BADGE_CLASS.get(t["primitive_status"], "badge-unknown")
        dark_class = DARK_STATUS_BADGE_CLASS.get(t.get("dark_status", ""), "badge-unknown")
        tags_html = " ".join(f'<span class="tag">{esc(tg)}</span>' for tg in t["source_tags"])
        diff_flag_html = ' <span class="tag tag-diff">⚠diff注記</span>' if t["has_diff_flag"] else ""

        matched_html = (
            "<br>".join(esc(n) for n in t["matched_primitive_names"])
            if t["matched_primitive_names"]
            else "—"
        )
        naming_diff_html = "—"
        if t["naming_diff"]:
            nd = t["naming_diff"]
            naming_diff_html = f"{esc(nd['bundle_scale'])}⇄{esc(nd['figma_scale'])}"

        rows.append(
            f"""
            <tr>
              <td><code class="tok-name">{esc(t['name'])}</code></td>
              <td>{esc(t['kind'])}</td>
              <td>{color_swatch_html(t['value_light'])}<code>{esc(t['value_light'])}</code></td>
              <td>{color_swatch_html(t['value_dark'])}<code>{esc(t['value_dark']) if t['value_dark'] else '—'}</code></td>
              <td>{esc(t['figma_hint']) or '—'}</td>
              <td>{tags_html}{diff_flag_html}</td>
              <td><span class="badge {status_class}">{esc(t['primitive_status'])}</span></td>
              <td><span class="badge {dark_class}">{esc(t.get('dark_status',''))}</span></td>
              <td>{matched_html}</td>
              <td>{naming_diff_html}</td>
            </tr>"""
        )
    return "\n".join(rows)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>DS ドリフト監査レポート（プリミティブ照合）</title>
<style>
  :root {{
    --bg: #fcfcfd;
    --fg: #1c2024;
    --muted: #60646c;
    --card-bg: #ffffff;
    --border: #d9d9e0;
    --accent: #3b82f6;
    --badge-ok-bg: #d3f8ec; --badge-ok-fg: #00775e;
    --badge-diff-bg: #fde6e6; --badge-diff-fg: #d20034;
    --badge-warn-bg: #fff2b5; --badge-warn-fg: #907000;
    --badge-info-bg: #e4f0fb; --badge-info-fg: #0072cd;
    --badge-critical-bg: #ef4444; --badge-critical-fg: #ffffff;
    --badge-unknown-bg: #f0f0f3; --badge-unknown-fg: #60646c;
    --premise-bg: #fff2b5; --premise-fg: #443b1f;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #111113; --fg: #edeef0; --muted: #b0b4ba;
      --card-bg: #18191b; --border: #363a3f; --accent: #61bbff;
      --badge-ok-bg: #112d26; --badge-ok-fg: #23d7b3;
      --badge-diff-bg: #3e0f13; --badge-diff-fg: #ff8e92;
      --badge-warn-bg: #2b2306; --badge-warn-fg: #ffd208;
      --badge-info-bg: #022947; --badge-info-fg: #61bbff;
      --badge-critical-bg: #ef4444; --badge-critical-fg: #111113;
      --badge-unknown-bg: #212225; --badge-unknown-fg: #b0b4ba;
      --premise-bg: #2b2306; --premise-fg: #ffd208;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #111113; --fg: #edeef0; --muted: #b0b4ba;
    --card-bg: #18191b; --border: #363a3f; --accent: #61bbff;
    --badge-ok-bg: #112d26; --badge-ok-fg: #23d7b3;
    --badge-diff-bg: #3e0f13; --badge-diff-fg: #ff8e92;
    --badge-warn-bg: #2b2306; --badge-warn-fg: #ffd208;
    --badge-info-bg: #022947; --badge-info-fg: #61bbff;
    --badge-critical-bg: #ef4444; --badge-critical-fg: #111113;
    --badge-unknown-bg: #212225; --badge-unknown-fg: #b0b4ba;
    --premise-bg: #2b2306; --premise-fg: #ffd208;
  }}
  :root[data-theme="light"] {{
    --bg: #fcfcfd; --fg: #1c2024; --muted: #60646c;
    --card-bg: #ffffff; --border: #d9d9e0; --accent: #3b82f6;
    --badge-ok-bg: #d3f8ec; --badge-ok-fg: #00775e;
    --badge-diff-bg: #fde6e6; --badge-diff-fg: #d20034;
    --badge-warn-bg: #fff2b5; --badge-warn-fg: #907000;
    --badge-info-bg: #e4f0fb; --badge-info-fg: #0072cd;
    --badge-critical-bg: #ef4444; --badge-critical-fg: #ffffff;
    --badge-unknown-bg: #f0f0f3; --badge-unknown-fg: #60646c;
    --premise-bg: #fff2b5; --premise-fg: #443b1f;
  }}

  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0;
    background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Kaku Gothic ProN', 'Yu Gothic', Meiryo, sans-serif;
    overflow-x: hidden;
  }}
  .page {{ max-width: 1400px; margin: 0 auto; padding: 24px 20px 80px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 32px 0 8px; border-left: 4px solid var(--accent); padding-left: 8px; }}
  .subtitle {{ color: var(--muted); font-size: 13px; margin: 0 0 20px; }}
  .toolbar {{ display:flex; gap:8px; align-items:center; margin-bottom: 16px; }}
  .toolbar button {{
    font-size: 12px; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--card-bg); color: var(--fg); cursor: pointer;
  }}
  .premise-box {{
    background: var(--premise-bg); color: var(--premise-fg); border-radius: 10px;
    padding: 14px 16px; font-size: 13px; line-height: 1.6; margin-bottom: 20px;
  }}
  .summary-badges {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }}
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600;
    white-space: nowrap;
  }}
  .badge-ok {{ background: var(--badge-ok-bg); color: var(--badge-ok-fg); }}
  .badge-diff {{ background: var(--badge-diff-bg); color: var(--badge-diff-fg); }}
  .badge-warn {{ background: var(--badge-warn-bg); color: var(--badge-warn-fg); }}
  .badge-info {{ background: var(--badge-info-bg); color: var(--badge-info-fg); }}
  .badge-critical {{ background: var(--badge-critical-bg); color: var(--badge-critical-fg); }}
  .badge-unknown {{ background: var(--badge-unknown-bg); color: var(--badge-unknown-fg); }}

  .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  .alerts-section {{ margin-bottom: 12px; }}
  .alert-item {{
    display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
    padding: 10px; border-radius: 8px; margin-bottom: 8px; border: 1px solid var(--border);
  }}
  .alert-critical {{ border-color: var(--badge-critical-bg); }}
  .alert-detail {{
    width: 100%; margin: 4px 0 0; font-size: 11px; color: var(--muted);
    white-space: pre-wrap; word-break: break-all;
  }}
  .tok-name {{ font-family: 'Roboto Mono', 'Courier New', monospace; font-size: 12px; }}
  .tag {{
    display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 4px;
    background: var(--badge-unknown-bg); color: var(--badge-unknown-fg); margin-right: 4px;
  }}
  .tag-diff {{ background: var(--badge-diff-bg); color: var(--badge-diff-fg); }}
  .muted {{ color: var(--muted); font-size: 13px; }}

  .table-wrap {{ overflow-x: auto; max-width: 100%; border: 1px solid var(--border); border-radius: 10px; }}
  table {{ border-collapse: collapse; width: 100%; min-width: 1100px; font-size: 12px; }}
  thead th {{
    position: sticky; top: 0; background: var(--card-bg); border-bottom: 2px solid var(--border);
    text-align: left; padding: 8px; white-space: nowrap;
  }}
  tbody td {{ padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  tbody tr:hover {{ background: rgba(128,128,128,0.06); }}
  code {{ font-family: 'Roboto Mono', 'Courier New', monospace; font-size: 11px; }}
  .swatch {{
    display: inline-block; width: 12px; height: 12px; border-radius: 3px;
    border: 1px solid var(--border); margin-right: 4px; vertical-align: middle;
  }}
  .kind-group {{ margin-bottom: 28px; }}
  footer {{ margin-top: 40px; color: var(--muted); font-size: 11px; }}
</style>
</head>
<body>
<div class="page">
  <h1>Design System — ドリフト監査レポート（プリミティブ照合）</h1>
  <p class="subtitle">バンドル（コード側コピー）の色値が Figma ライブ・プリミティブ色に存在するかの照合結果。全 {total} トークン。</p>

  <div class="premise-box">
    <strong>前提（必読）:</strong> {premise}
  </div>

  <div class="toolbar">
    <button onclick="document.documentElement.dataset.theme='light'">Light</button>
    <button onclick="document.documentElement.dataset.theme='dark'">Dark</button>
    <button onclick="document.documentElement.removeAttribute('data-theme')">System</button>
  </div>

  <div class="summary-badges">
    {summary_badges}
  </div>

  <h2>整合性アラート（自己タグ ⇔ プリミティブ照合の矛盾）</h2>
  <div class="card alerts-section">
    {alerts_html}
  </div>

  <h2>命名差（値一致・スケール名相違: ACME命名 ⇄ Figma/Radix命名）</h2>
  <div class="card">
    {naming_diffs_html}
  </div>

  {kind_sections}

  <footer>generated by ds-drift-audit / audit.py — 固定ファイル名で上書き生成（日付なし）。取得プリミティブ総数: {primitives_total}（パース不能: {primitives_unparsed}）</footer>
</div>
</body>
</html>
"""

KIND_SECTION_TEMPLATE = """
  <h2>kind = {kind} （{count}件）</h2>
  <div class="kind-group">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>name</th><th>kind</th><th>light値</th><th>dark値</th><th>figma hint</th>
            <th>出所タグ</th><th>プリミティブ照合(light)</th><th>dark照合</th><th>matched primitive</th><th>命名差</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>
  </div>
"""


def render_html(report: dict) -> str:
    summary = report["summary"]

    badges = []
    for label, count in summary["by_primitive_status"].items():
        cls = PRIMITIVE_STATUS_BADGE_CLASS.get(label, "badge-unknown")
        badges.append(f'<span class="badge {cls}">{esc(label)}: {count}</span>')
    badges.append(f'<span class="badge badge-info">命名差: {summary["naming_diff_count"]}</span>')
    dark_unchecked = summary.get("by_dark_status", {}).get(DARK_UNCHECKED, 0)
    badges.append(f'<span class="badge badge-warn">dark未照合: {dark_unchecked}</span>')
    badges.append(
        f'<span class="badge badge-critical">整合性アラート(重大): {summary["consistency_alert_critical_count"]}</span>'
    )
    badges.append(
        f'<span class="badge badge-unknown">取得プリミティブ総数: {summary["figma_primitives_total_count"]}</span>'
    )

    alerts_html = render_alerts_html(report["consistency_alerts"])
    naming_diffs_html = render_naming_diffs_html(report["naming_diffs"])

    kinds_order = ["color", "font", "spacing", "radius", "shadow"]
    tokens_by_kind = {}
    for t in report["tokens"]:
        tokens_by_kind.setdefault(t["kind"], []).append(t)

    kind_sections = []
    for kind in kinds_order + sorted(set(tokens_by_kind) - set(kinds_order)):
        toks = tokens_by_kind.get(kind)
        if not toks:
            continue
        rows = render_token_rows(toks)
        kind_sections.append(
            KIND_SECTION_TEMPLATE.format(kind=esc(kind), count=len(toks), rows=rows)
        )

    return HTML_TEMPLATE.format(
        total=summary["total_tokens"],
        premise=esc(report["premise"]),
        summary_badges="\n".join(badges),
        alerts_html=alerts_html,
        naming_diffs_html=naming_diffs_html,
        kind_sections="\n".join(kind_sections),
        primitives_total=summary["figma_primitives_total_count"],
        primitives_unparsed=summary["figma_primitives_unparsed_count"],
    )


# ---------------------------------------------------------------------------
# ターミナル要約
# ---------------------------------------------------------------------------
def print_terminal_summary(report: dict):
    s = report["summary"]
    print("=" * 60)
    print("DS ドリフト監査（プリミティブ照合） — 要約")
    print("=" * 60)
    print(f"前提: {report['premise']}")
    print(f"\n取得プリミティブ総数: {s['figma_primitives_total_count']}"
          f"（パース不能: {s['figma_primitives_unparsed_count']}）")
    print(f"総トークン数: {s['total_tokens']}"
          f"（色トークン: {s['color_token_count']} / 非色（対象外）: {s['non_color_token_count']}）")
    print("\n[プリミティブ照合分類（light基準）]")
    for k, v in s["by_primitive_status"].items():
        print(f"  {k}: {v}")
    print("\n[dark照合ステータス]")
    for k, v in s.get("by_dark_status", {}).items():
        print(f"  {k}: {v}")
    print(f"\n命名差（値一致・スケール名相違）件数: {s['naming_diff_count']}")
    print(f"\n整合性アラート: 合計{s['consistency_alert_count']}件（重大 {s['consistency_alert_critical_count']}件）")

    critical = [a for a in report["consistency_alerts"] if a["severity"] == "重大"]
    if critical:
        print("\n--- 重大アラート一覧（[Figma確定]なのにプリミティブ非在）---")
        for a in critical:
            print(f"  [{a['token']}] {a['reason']}")

    if report["naming_diffs"]:
        print("\n--- 命名差 代表例 ---")
        for nd in report["naming_diffs"][:10]:
            print(
                f"  [{nd['token']}] bundle={nd['bundle_hint']} ⇄ "
                f"figma={nd['figma_primitive']} ({nd['figma_scale']}/{nd['figma_step']})"
            )
    print("=" * 60)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def run(bundle_dir: Path, figma_primitives_path: Path, out_dir: Path,
        figma_primitives_dark_path: Path = None, expect_token_count: int = None):
    records = build_bundle_records(bundle_dir)

    # 任意の自己チェック: バンドルを取りこぼしなくパースできているかを件数で確かめる。
    # 件数は環境ごとに違うので、--expect-token-count で渡されたときだけ検証する
    # （既定では検証しない。ここを固定値にすると、他のバンドルで必ず落ちる）。
    if expect_token_count is not None and len(records) != expect_token_count:
        raise SystemExit(
            f"パース件数が合いません: 期待 {expect_token_count} / 実際 {len(records)}。"
            "バンドルの取得が途中で切れていないか確認してください。"
        )

    raw_map, normalized_map, reverse_index, unparsed = load_figma_primitives(
        figma_primitives_path
    )

    reverse_index_dark = None
    dark_meta = None
    if figma_primitives_dark_path is not None and Path(figma_primitives_dark_path).exists():
        d_raw, d_norm, reverse_index_dark, d_unparsed = load_figma_primitives(
            Path(figma_primitives_dark_path)
        )
        dark_meta = {
            "source_path": str(figma_primitives_dark_path),
            "total_count": len(d_raw),
            "parsed_count": len(d_norm),
            "unparsed": d_unparsed,
        }

    primitives_meta = {
        "source_path": str(figma_primitives_path),
        "total_count": len(raw_map),
        "parsed_count": len(normalized_map),
        "unparsed": unparsed,
        "dark": dark_meta,
    }

    result_tokens = classify_and_audit(records, reverse_index, reverse_index_dark)
    alerts = build_consistency_alerts(result_tokens)
    report = build_report(result_tokens, alerts, primitives_meta)

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ds-drift-report.json"
    html_path = out_dir / "ds-drift-report.html"

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    html_path.write_text(render_html(report), encoding="utf-8")

    print_terminal_summary(report)
    print(f"\nJSON: {json_path}")
    print(f"HTML: {html_path}")
    print(f"parsed count: {len(records)}")

    return report, json_path, html_path


def main():
    parser = argparse.ArgumentParser(description="DS ドリフト監査ツール（プリミティブ照合）")
    parser.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE_DIR)
    parser.add_argument("--figma-primitives", type=Path, default=DEFAULT_FIGMA_PRIMITIVES)
    parser.add_argument("--figma-primitives-dark", type=Path, default=None,
                        help="任意: darkモードのプリミティブJSON。渡すとdark照合も行う。")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--expect-token-count", type=int, default=None,
                        help="任意: バンドルのトークン件数。渡すと件数が一致するかを検証する"
                             "（取得が途中で切れたことに気づくため）。既定では検証しない。")
    parser.add_argument("--token-prefix", default=TOKEN_PREFIX,
                        help=f"バンドルのトークン接頭辞（既定 {TOKEN_PREFIX}）。"
                             "自分の DS の接頭辞に合わせる。")
    args = parser.parse_args()

    if args.token_prefix != TOKEN_PREFIX:
        globals()["TOKEN_DECL_RE"] = build_token_decl_re(args.token_prefix)

    run(args.bundle_dir, args.figma_primitives, args.out_dir,
        figma_primitives_dark_path=args.figma_primitives_dark,
        expect_token_count=args.expect_token_count)


if __name__ == "__main__":
    main()
