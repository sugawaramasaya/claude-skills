# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""design-check スキル: ai_bridge.py dump が出力した dump.json を analyze.py 用の
rois.json / cmyk_table.json に変換する。

サブコマンド:
    convert  dump.json -> rois.json + cmyk_table.json（analyze.py all にそのまま渡せる）
    centers  analyze.py の出力（px座標の光学重心等）を Illustrator ドキュメント座標(pt)に
             逆変換する。ai_bridge.py guides の --centers 入力を作るために使う。

標準出力は JSON のみ（analyze.py / simulate.py と同じ規約）。

使い方:
    uv run ai_to_rois.py convert --dump work/dump.json --out-dir work
    uv run ai_to_rois.py centers --dump work/dump.json --points work/points.json \
        --out work/centers.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MM_TO_PT = 2.834645669

# ai_apply.jsx guides が描く注釈レイヤーは常に除外する
EXCLUDE_LAYERS = {"design-check"}


def load_json(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(obj, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def print_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")


def sanitize_id(name: str, idx: int) -> str:
    """ROI id は CLI (--roi-id 等) で扱いやすいよう ASCII 中心にする。元の名前は label に残す。"""
    if name and name.isascii() and re.match(r"^[\w\-]+$", name):
        base = re.sub(r"[^0-9a-zA-Z_\-]", "_", name)
        return f"{base}_{idx}"
    return f"item_{idx}"


def pt_to_px(bbox_pt, rect_pt, px_per_pt: float, px_per_pt_v: float):
    """AIドキュメント座標(pt, y上向き正)の bbox を PNG px座標(y下向き正)に換算する。"""
    l, t, r, b = bbox_pt
    rl, rt, _rr, _rb = rect_pt
    x0 = (l - rl) * px_per_pt
    x1 = (r - rl) * px_per_pt
    y0 = (rt - t) * px_per_pt_v
    y1 = (rt - b) * px_per_pt_v
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return [x0, y0, x1, y1]


def cmd_convert(args) -> None:
    dump = load_json(args.dump)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rect_pt = dump["artboard"]["rectPt"]
    l, t, r, b = rect_pt
    w_pt = r - l
    h_pt = t - b
    png = dump["png"]
    px_per_pt = png["pxPerPt"]
    px_per_pt_v = png.get("pxPerPtVertical", px_per_pt)
    png_w = png["widthPx"]
    png_h = png["heightPx"]
    px_per_mm = px_per_pt * MM_TO_PT

    container_id = "artboard"
    rois = [{
        "id": container_id,
        "label": dump.get("artboard", {}).get("name") or "アートボード",
        "type": "container",
        "bbox": [0, 0, png_w, png_h],
    }]
    cmyk_table = []

    for idx, item in enumerate(dump.get("items", [])):
        if item.get("layer") in EXCLUDE_LAYERS:
            continue
        if item.get("hidden") or item.get("locked") or item.get("guides"):
            continue
        if "error" in item:
            continue
        bbox_pt = item.get("visibleBoundsPt")
        if not bbox_pt:
            continue
        bbox_px = pt_to_px(bbox_pt, rect_pt, px_per_pt, px_per_pt_v)
        if (bbox_px[2] - bbox_px[0]) < 0.5 or (bbox_px[3] - bbox_px[1]) < 0.5:
            continue  # 面積ほぼゼロ（線状の残骸等）は ROI 化しない

        name = item.get("name") or f"item_{idx}"
        roi_id = sanitize_id(name, idx)
        roi_type = "text_line" if item.get("typename") == "TextFrame" else "element"
        rois.append({
            "id": roi_id,
            "label": name,
            "type": roi_type,
            "bbox": [round(v, 2) for v in bbox_px],
            "relative_to": container_id,
        })

        fill = item.get("fill")
        if fill and fill.get("kind") == "CMYK":
            cmyk_table.append({
                "id": roi_id,
                "label": name,
                "c": fill.get("c"), "m": fill.get("m"), "y": fill.get("y"), "k": fill.get("k"),
            })

    rois_path = out_dir / "rois.json"
    cmyk_path = out_dir / "cmyk_table.json"
    save_json(rois, rois_path)
    save_json(cmyk_table, cmyk_path)

    print_json({
        "rois_path": str(rois_path),
        "cmyk_table_path": str(cmyk_path),
        "image_path": png.get("path"),
        "image_size_px": [png_w, png_h],
        "artboard_rect_pt": rect_pt,
        "artboard_size_pt": [w_pt, h_pt],
        "px_per_pt": px_per_pt,
        "px_per_pt_vertical": px_per_pt_v,
        "px_per_mm": px_per_mm,
        "roi_count": len(rois),
        "cmyk_count": len(cmyk_table),
    })


def cmd_centers(args) -> None:
    dump = load_json(args.dump)
    points = load_json(args.points)
    rect_pt = dump["artboard"]["rectPt"]
    l, t, _r, _b = rect_pt
    png = dump["png"]
    px_per_pt = png["pxPerPt"]
    px_per_pt_v = png.get("pxPerPtVertical", px_per_pt)

    out = []
    for p in points:
        x_pt = l + p["x"] / px_per_pt
        y_pt = t - p["y"] / px_per_pt_v
        out.append({"label": p.get("label", ""), "xPt": x_pt, "yPt": y_pt})

    if args.out:
        save_json(out, args.out)
        print_json({"centers_path": args.out, "count": len(out)})
    else:
        print_json(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="design-check: Illustrator dump.json を rois.json 等に変換する")
    sub = p.add_subparsers(dest="cmd", required=True)

    cv = sub.add_parser("convert", help="dump.json -> rois.json + cmyk_table.json")
    cv.add_argument("--dump", required=True)
    cv.add_argument("--out-dir", required=True)
    cv.set_defaults(func=cmd_convert)

    ce = sub.add_parser("centers", help="px座標の点群を Illustrator ドキュメント座標(pt)に逆変換する")
    ce.add_argument("--dump", required=True)
    ce.add_argument("--points", required=True, help="[{label,x,y}] (px) のJSONファイルパス")
    ce.add_argument("--out", default=None, help="省略時は標準出力にJSON配列を書く")
    ce.set_defaults(func=cmd_centers)

    return p


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
