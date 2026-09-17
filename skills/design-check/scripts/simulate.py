# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy"]
# ///
"""design-check: 修正案をシミュレーションし、再計測で改善を証明する CLI。

平坦な背景（単色に近い背景）限定で、要素を動かした穴を周辺リングの中央値で
埋める簡易 inpaint を行い、その上に補正後の要素を合成する。
合成後の画像を再計測（remeasure）し、「現状 Δ46px → 案A Δ2px」のように
補正の効果を数値で示す。

標準出力は JSON のみ。生成画像は --out-dir 配下にファイルとして書き出す。

サブコマンド:
    offset    ROI を平行移動して重心ズレを補正する
    tracking  テキスト行 ROI の文字間ギャップを均等化する
    scale     ROI を拡大縮小してグループ内の視覚的大きさを揃える
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import common as c
from analyze import analyze_gravity_one, analyze_kerning_bbox, render_gravity_overlay


# ---------------------------------------------------------------------------
# 背景推定・平坦判定・簡易 inpaint
# ---------------------------------------------------------------------------


def is_flat_background(arr: np.ndarray, bbox, margin: int = 8, std_threshold: float = 12.0) -> bool:
    """bbox 周辺リングの色ばらつきが小さければ「平坦背景」とみなす。"""
    x0, y0, x1, y1 = bbox
    h, w, _ = arr.shape
    rx0, ry0 = max(0, x0 - margin), max(0, y0 - margin)
    rx1, ry1 = min(w, x1 + margin), min(h, y1 + margin)
    outer = arr[ry0:ry1, rx0:rx1].reshape(-1, 3)
    hole_mask = np.ones((ry1 - ry0, rx1 - rx0), dtype=bool)
    hole_mask[y0 - ry0 : y1 - ry0, x0 - rx0 : x1 - rx0] = False
    ring_pixels = arr[ry0:ry1, rx0:rx1][hole_mask]
    if ring_pixels.size == 0:
        return False
    return float(ring_pixels.std()) < std_threshold


def inpaint_flat(arr: np.ndarray, bbox, margin: int = 8) -> np.ndarray:
    """bbox 領域を周辺リングの中央値で埋めた配列を返す（元の配列は変更しない）。"""
    x0, y0, x1, y1 = bbox
    h, w, _ = arr.shape
    rx0, ry0 = max(0, x0 - margin), max(0, y0 - margin)
    rx1, ry1 = min(w, x1 + margin), min(h, y1 + margin)
    ring = arr[ry0:ry1, rx0:rx1].copy()
    hole_mask = np.zeros((ry1 - ry0, rx1 - rx0), dtype=bool)
    hole_mask[y0 - ry0 : y1 - ry0, x0 - rx0 : x1 - rx0] = True
    ring_pixels = ring[~hole_mask]
    fill_color = np.median(ring_pixels, axis=0)
    out = arr.copy()
    out[y0:y1, x0:x1] = fill_color
    return out


# ---------------------------------------------------------------------------
# offset: 平行移動で重心ズレを補正する
# ---------------------------------------------------------------------------


def cmd_offset(args) -> dict:
    img, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)
    rois = c.load_json(args.rois)
    rois_index = {r["id"]: r for r in rois}
    roi = c.require_roi(rois, args.roi_id)

    before = analyze_gravity_one(ink, roi, rois_index, None)
    bbox = before["refined_bbox"]

    if not is_flat_background(arr, bbox):
        return {
            "command": "offset", "roi_id": args.roi_id, "simulation": "unavailable",
            "reason": "背景が平坦ではないため合成 inpaint の精度が低く、シミュレーションを提供できません",
            "before": before,
        }

    if args.dx is not None and args.dy is not None:
        dx, dy = args.dx, args.dy
    else:
        # 自動補正: 参照先（relative_to があれば親コンテナ、なければ自身の bbox）に
        # 光学重心を合わせるための移動量 = -(ズレ)
        ref = before.get("relative_to", before)
        d = ref["delta_px"]
        dx, dy = -round(d["x"]), -round(d["y"])

    x0, y0, x1, y1 = bbox
    sprite = arr[y0:y1, x0:x1].copy()
    base = inpaint_flat(arr, bbox, margin=args.margin)

    h, w, _ = arr.shape
    nx0, ny0 = int(x0 + dx), int(y0 + dy)
    nx1, ny1 = nx0 + (x1 - x0), ny0 + (y1 - y0)
    cnx0, cny0 = max(0, nx0), max(0, ny0)
    cnx1, cny1 = min(w, nx1), min(h, ny1)
    if cnx1 <= cnx0 or cny1 <= cny0:
        return {"command": "offset", "roi_id": args.roi_id, "simulation": "unavailable",
                "reason": "移動先が画像範囲外になるため合成できません", "before": before}

    composite = base.copy()
    composite[cny0:cny1, cnx0:cnx1] = sprite[cny0 - ny0 : cny1 - ny0, cnx0 - nx0 : cnx1 - nx0]

    new_bbox = [nx0, ny0, nx1, ny1]
    new_ink = c.compute_ink_map(composite, bg)
    ncx, ncy, nmass = c.ink_centroid(new_ink, new_bbox)
    ngx, ngy = c.bbox_center(new_bbox)

    if "relative_to" in before:
        ref_center = before["relative_to"]["container_center"]
        ref_width = before["relative_to"]["container_bbox"][2] - before["relative_to"]["container_bbox"][0]
    else:
        ref_center = (ngx, ngy)
        ref_width = new_bbox[2] - new_bbox[0]
    rdx, rdy = ncx - ref_center[0], ncy - ref_center[1]
    after_delta_px = math.hypot(rdx, rdy)
    after_delta_pct = (after_delta_px / ref_width * 100) if ref_width else 0.0

    composite_img = Image.fromarray(np.clip(composite, 0, 255).astype(np.uint8), mode="RGB")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    after_path = out_dir / f"offset_{args.roi_id}_after.png"
    composite_img.save(after_path)

    strip_path = out_dir / f"offset_{args.roi_id}_compare.png"
    _save_before_after_strip(img, composite_img, bbox, new_bbox, strip_path)

    before_ref = before.get("relative_to", before)
    return {
        "command": "offset",
        "roi_id": args.roi_id,
        "simulation": "ok",
        "applied_offset": {"dx": dx, "dy": dy},
        "before_delta_px": before_ref["delta_px"]["magnitude"],
        "after_delta_px": after_delta_px,
        "after_delta_pct_of_width": after_delta_pct,
        "improvement_px": before_ref["delta_px"]["magnitude"] - after_delta_px,
        "after_image": str(after_path),
        "compare_image": str(strip_path),
    }


def _save_before_after_strip(before_img: Image.Image, after_img: Image.Image, before_bbox, after_bbox, out_path: Path,
                              pad: int = 24) -> None:
    def crop_with_pad(img, bbox):
        x0, y0, x1, y1 = bbox
        cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
        cx1, cy1 = min(img.width, x1 + pad), min(img.height, y1 + pad)
        return img.crop((cx0, cy0, cx1, cy1))

    a = crop_with_pad(before_img.convert("RGB"), before_bbox)
    b = crop_with_pad(after_img.convert("RGB"), after_bbox)
    h = max(a.height, b.height)
    gap = 16
    strip = Image.new("RGB", (a.width + b.width + gap, h), (255, 255, 255))
    strip.paste(a, (0, 0))
    strip.paste(b, (a.width + gap, 0))
    strip.save(out_path)


# ---------------------------------------------------------------------------
# tracking: 文字間ギャップを均等化する
# ---------------------------------------------------------------------------


def cmd_tracking(args) -> dict:
    img, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)
    rois = c.load_json(args.rois)
    roi = c.require_roi(rois, args.roi_id)
    bbox = c.refine_bbox(ink, roi["bbox"])

    if not is_flat_background(arr, bbox):
        return {"command": "tracking", "roi_id": args.roi_id, "simulation": "unavailable",
                "reason": "背景が平坦ではないため合成 inpaint の精度が低く、シミュレーションを提供できません"}

    before = analyze_kerning_bbox(ink, bbox, merge_gap_ratio=args.merge_gap_ratio, min_gap_px=args.min_gap_px)
    chars = before["chars"]
    if len(chars) < 2:
        return {"command": "tracking", "roi_id": args.roi_id, "simulation": "unavailable",
                "reason": "文字ブロブが2個未満のため字送り均等化を計算できません"}

    target_gap = args.target_gap if args.target_gap is not None else before["median_gap_px"]

    x0, y0, x1, y1 = bbox
    base = inpaint_flat(arr, bbox, margin=args.margin)
    canvas = base.copy()

    cursor = chars[0]["start_px"]
    new_positions = []
    for ch in chars:
        w = ch["width_px"]
        sprite = arr[y0:y1, ch["start_px"] : ch["end_px"]]
        dst0 = int(round(cursor))
        dst1 = dst0 + w
        if dst1 > canvas.shape[1]:
            dst1 = canvas.shape[1]
            sprite = sprite[:, : dst1 - dst0]
        canvas[y0:y1, dst0:dst1] = sprite
        new_positions.append({"start_px": dst0, "end_px": dst1})
        cursor = dst1 + target_gap

    new_bbox = [chars[0]["start_px"], y0, int(math.ceil(cursor - target_gap)), y1]
    new_ink = c.compute_ink_map(canvas, bg)
    after = analyze_kerning_bbox(new_ink, new_bbox, merge_gap_ratio=args.merge_gap_ratio, min_gap_px=args.min_gap_px)

    canvas_img = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), mode="RGB")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    after_path = out_dir / f"tracking_{args.roi_id}_after.png"
    canvas_img.save(after_path)
    strip_path = out_dir / f"tracking_{args.roi_id}_compare.png"
    _save_before_after_strip(img, canvas_img, bbox, new_bbox, strip_path)

    return {
        "command": "tracking",
        "roi_id": args.roi_id,
        "simulation": "ok",
        "target_gap_px": target_gap,
        "before_max_deviation_pct": before["max_deviation_pct"],
        "after_max_deviation_pct": after["max_deviation_pct"],
        "new_positions": new_positions,
        "after_image": str(after_path),
        "compare_image": str(strip_path),
    }


# ---------------------------------------------------------------------------
# scale: 拡大縮小してグループ内の大きさを揃える
# ---------------------------------------------------------------------------


def cmd_scale(args) -> dict:
    img, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)
    rois = c.load_json(args.rois)
    roi = c.require_roi(rois, args.roi_id)
    bbox = c.refine_bbox(ink, roi["bbox"])

    if not is_flat_background(arr, bbox):
        return {"command": "scale", "roi_id": args.roi_id, "simulation": "unavailable",
                "reason": "背景が平坦ではないため合成 inpaint の精度が低く、シミュレーションを提供できません"}

    current_diam = c.visual_diameter(ink, bbox)
    if args.target_diameter is not None:
        scale = args.target_diameter / current_diam if current_diam else 1.0
    else:
        scale = args.scale

    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    sprite_img = img.crop((x0, y0, x1, y1))
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    resized = sprite_img.resize((new_w, new_h), Image.LANCZOS)

    base = inpaint_flat(arr, bbox, margin=args.margin)
    base_img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), mode="RGB")

    cx, cy = c.bbox_center(bbox)
    nx0, ny0 = int(round(cx - new_w / 2)), int(round(cy - new_h / 2))
    nx1, ny1 = nx0 + new_w, ny0 + new_h

    composite_img = base_img.copy()
    composite_img.paste(resized, (nx0, ny0))
    new_bbox = [nx0, ny0, nx1, ny1]

    new_arr = np.asarray(composite_img, dtype=np.float64)
    new_ink = c.compute_ink_map(new_arr, bg)
    new_diam = c.visual_diameter(new_ink, new_bbox)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    after_path = out_dir / f"scale_{args.roi_id}_after.png"
    composite_img.save(after_path)
    strip_path = out_dir / f"scale_{args.roi_id}_compare.png"
    _save_before_after_strip(img, composite_img, bbox, new_bbox, strip_path)

    return {
        "command": "scale",
        "roi_id": args.roi_id,
        "simulation": "ok",
        "scale_factor": scale,
        "before_diameter": current_diam,
        "after_diameter": new_diam,
        "after_image": str(after_path),
        "compare_image": str(strip_path),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="design-check: 修正案をシミュレーションし再計測する")
    sub = p.add_subparsers(dest="cmd", required=True)

    off = sub.add_parser("offset", help="平行移動で重心ズレを補正する")
    off.add_argument("--image", required=True)
    off.add_argument("--rois", required=True)
    off.add_argument("--roi-id", required=True)
    off.add_argument("--out-dir", required=True)
    off.add_argument("--dx", type=float, default=None)
    off.add_argument("--dy", type=float, default=None)
    off.add_argument("--margin", type=int, default=8)
    off.set_defaults(func=cmd_offset)

    tr = sub.add_parser("tracking", help="字送りを均等化する")
    tr.add_argument("--image", required=True)
    tr.add_argument("--rois", required=True)
    tr.add_argument("--roi-id", required=True)
    tr.add_argument("--out-dir", required=True)
    tr.add_argument("--target-gap", type=float, default=None)
    tr.add_argument("--merge-gap-ratio", type=float, default=0.15)
    tr.add_argument("--min-gap-px", type=float, default=1.0)
    tr.add_argument("--margin", type=int, default=8)
    tr.set_defaults(func=cmd_tracking)

    sc = sub.add_parser("scale", help="拡大縮小して大きさを揃える")
    sc.add_argument("--image", required=True)
    sc.add_argument("--rois", required=True)
    sc.add_argument("--roi-id", required=True)
    sc.add_argument("--out-dir", required=True)
    sc.add_argument("--scale", type=float, default=1.0)
    sc.add_argument("--target-diameter", type=float, default=None)
    sc.add_argument("--margin", type=int, default=8)
    sc.set_defaults(func=cmd_scale)

    return p


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    c.print_json(result)


if __name__ == "__main__":
    main()
