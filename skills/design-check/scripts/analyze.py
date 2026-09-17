# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy"]
# ///
"""design-check: 画像を解析して重心・大きさ・カーニング・色の定量データを出す CLI。

標準出力は JSON のみ。画像アセット（グリッド画像・オーバーレイ・比較画像）は
--out-dir 配下にファイルとして書き出し、JSON にはそのパスだけを載せる。

サブコマンド:
    grid         100px 間隔のグリッドを焼き込んだ画像を出力する（ROI 座標を目視するため）
    gravity      ROI のインク密度重み付き重心と幾何中心のズレを計測する
    size         同グループ ROI 群の視覚直径の CV(%) と外れ値を計測する
    kerning      テキスト行 ROI の文字間ギャップを計測する
    color        画像/ROI からパレットを抽出し ΔE 統合候補とブランド色ズレを判定する
    composition  紙面全体（基準コンテナ）に対する各要素の重心ズレを1枚のビジュアルで示す
    all          rois.json の内容から関連解析を自動で組み合わせて実行する
                 （container ROI があれば composition.png も自動生成する）

使い方の例:
    uv run analyze.py grid --image card.png --out-dir work
    uv run analyze.py all --image card.png --rois work/rois.json --out-dir work
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import common as c


# ---------------------------------------------------------------------------
# grid
# ---------------------------------------------------------------------------


def cmd_grid(args) -> dict:
    img, _ = c.load_image_rgb(args.image)
    out = img.copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size
    spacing = args.spacing
    line_color = (255, 0, 0)
    label_color = (255, 0, 0)
    for x in range(0, w, spacing):
        draw.line([(x, 0), (x, h)], fill=line_color, width=1)
        draw.text((x + 2, 2), str(x), fill=label_color)
    for y in range(0, h, spacing):
        draw.line([(0, y), (w, y)], fill=line_color, width=1)
        draw.text((2, y + 2), str(y), fill=label_color)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "grid.png"
    out.save(out_path)
    return {
        "command": "grid",
        "image": str(args.image),
        "size": [w, h],
        "spacing": spacing,
        "grid_image": str(out_path),
    }


# ---------------------------------------------------------------------------
# gravity
# ---------------------------------------------------------------------------


def _rois_by_id(rois: list[dict]) -> dict:
    return {r["id"]: r for r in rois}


def analyze_gravity_one(ink_map: np.ndarray, roi: dict, rois_index: dict, px_per_mm: float | None):
    raw_bbox = roi["bbox"]
    bbox = c.refine_bbox(ink_map, raw_bbox)
    cx, cy, mass = c.ink_centroid(ink_map, bbox)
    gx, gy = c.bbox_center(bbox)
    dx, dy = cx - gx, cy - gy
    delta_px = math.hypot(dx, dy)
    width = bbox[2] - bbox[0]
    delta_pct = (delta_px / width * 100) if width else 0.0
    flag = c.score_flag(delta_pct, c.GRAVITY_THRESHOLDS["green_max"], c.GRAVITY_THRESHOLDS["yellow_max"])

    result = {
        "id": roi["id"],
        "label": roi.get("label", roi["id"]),
        "raw_bbox": raw_bbox,
        "refined_bbox": bbox,
        "geometric_center": [gx, gy],
        "optical_centroid": [cx, cy],
        "ink_mass": mass,
        "delta_px": {"x": dx, "y": dy, "magnitude": delta_px},
        "delta_pct_of_width": delta_pct,
        "flag": flag,
    }
    if px_per_mm:
        result["delta_mm"] = c.px_to_mm(delta_px, px_per_mm)

    relative_to = roi.get("relative_to")
    if relative_to and relative_to in rois_index:
        parent = rois_index[relative_to]
        if parent.get("type") == "container":
            # container は紙面実寸（raw bbox）の中心・幅を基準にする。
            # refine_bbox でインク範囲にタイト化すると、余白込みの紙面物理中心から
            # ズレてしまい（例: 印刷内容が紙面中央より片側に寄っている場合）、
            # composition の基準（常に raw bbox 中心）と値が食い違うため統一する。
            p_bbox = [float(v) for v in parent["bbox"]]
        else:
            p_bbox = c.refine_bbox(ink_map, parent["bbox"])
        pgx, pgy = c.bbox_center(p_bbox)
        rdx, rdy = cx - pgx, cy - pgy
        r_delta_px = math.hypot(rdx, rdy)
        p_width = p_bbox[2] - p_bbox[0]
        r_delta_pct = (r_delta_px / p_width * 100) if p_width else 0.0
        result["relative_to"] = {
            "id": relative_to,
            "container_bbox": p_bbox,
            "container_center": [pgx, pgy],
            "delta_px": {"x": rdx, "y": rdy, "magnitude": r_delta_px},
            "delta_pct_of_width": r_delta_pct,
            "flag": c.score_flag(r_delta_pct, c.GRAVITY_THRESHOLDS["green_max"], c.GRAVITY_THRESHOLDS["yellow_max"]),
        }
    return result


def render_gravity_overlay(img: Image.Image, result: dict, out_path: Path, pad: int = 24) -> None:
    bbox = result["refined_bbox"]
    x0, y0, x1, y1 = bbox
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(img.width, x1 + pad), min(img.height, y1 + pad)
    crop = img.crop((cx0, cy0, cx1, cy1)).convert("RGB")
    draw = ImageDraw.Draw(crop)

    def to_local(pt):
        return pt[0] - cx0, pt[1] - cy0

    # ROI 枠
    draw.rectangle([x0 - cx0, y0 - cy0, x1 - cx0 - 1, y1 - cy0 - 1], outline=(120, 120, 120), width=1)

    gpt = to_local(result["geometric_center"])
    opt = to_local(result["optical_centroid"])
    c.draw_cross(draw, gpt, (140, 140, 140), size=14, width=2, dashed=True)
    c.draw_cross(draw, opt, (220, 30, 30), size=14, width=2, dashed=False)
    if math.hypot(opt[0] - gpt[0], opt[1] - gpt[1]) > 1.0:
        c.draw_arrow(draw, gpt, opt, (220, 30, 30), width=2, head=7)

    if "relative_to" in result:
        rel = result["relative_to"]
        pcx, pcy = rel["container_center"]
        if cx0 <= pcx <= cx1 and cy0 <= pcy <= cy1:
            c.draw_cross(draw, to_local((pcx, pcy)), (59, 130, 246), size=18, width=2, dashed=True)

    crop.save(out_path)


def cmd_gravity(args) -> dict:
    img, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)
    rois = c.load_json(args.rois)
    rois_index = _rois_by_id(rois)
    if args.roi_id:
        # 明示指定された roi-id が存在しない場合、別要素にフォールバックせず exit(2) する
        targets = [c.require_roi(rois, args.roi_id)]
    else:
        targets = [r for r in rois if r.get("type") in ("text_line", "element", "container")]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for roi in targets:
        res = analyze_gravity_one(ink, roi, rois_index, args.px_per_mm)
        overlay_path = out_dir / f"gravity_{roi['id']}.png"
        render_gravity_overlay(img, res, overlay_path)
        res["overlay_image"] = str(overlay_path)
        results.append(res)

    return {"command": "gravity", "image": str(args.image), "results": results}


# ---------------------------------------------------------------------------
# size
# ---------------------------------------------------------------------------


def cmd_size(args) -> dict:
    _, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)
    rois = c.load_json(args.rois)
    members = c.require_group(rois, args.group)

    items = []
    for r in members:
        bbox = c.refine_bbox(ink, r["bbox"])
        diam = c.visual_diameter(ink, bbox)
        fr = c.fill_ratio(ink, bbox)
        items.append({"id": r["id"], "label": r.get("label", r["id"]), "refined_bbox": bbox,
                      "visual_diameter": diam, "fill_ratio": fr})

    diam_values = np.array([it["visual_diameter"] for it in items], dtype=np.float64)
    mean = float(diam_values.mean())
    std = float(diam_values.std(ddof=0))
    cv = (std / mean * 100) if mean else 0.0
    flag = c.score_flag(cv, c.SIZE_CV_THRESHOLDS["green_max"], c.SIZE_CV_THRESHOLDS["yellow_max"])

    outliers = []
    if std > 0:
        for it in items:
            z = abs(it["visual_diameter"] - mean) / std
            it["z_score"] = z
            if z > 1.0:
                outliers.append(it["id"])
    else:
        for it in items:
            it["z_score"] = 0.0

    return {
        "command": "size",
        "image": str(args.image),
        "group": args.group,
        "items": items,
        "mean_diameter": mean,
        "std_diameter": std,
        "cv_pct": cv,
        "outliers": outliers,
        "flag": flag,
    }


# ---------------------------------------------------------------------------
# kerning
# ---------------------------------------------------------------------------


def analyze_kerning_bbox(ink: np.ndarray, bbox, merge_gap_ratio: float = 0.15, min_gap_px: float = 1.0,
                          col_threshold_ratio: float = 0.05):
    """テキスト行 ROI の文字間ギャップを計測する。

    merge_gap_ratio: 濁点等の微小ギャップを結合する閾値を「結合前の生ブロブ間ギャップの
        中央値」に対する比率で決める（デフォルト15%）。実データ（4800px幅の名刺画像等）では
        通常ギャップが20〜30pxある一方で濁点相当のギャップは2px程度になることがあり、
        画像解像度に依存する絶対px閾値では検出できない。相対閾値にすることで解像度非依存にする。
    min_gap_px: 上記の比率閾値の絶対下限（生ブロブが少なくギャップ中央値が極端に小さい場合の保険）。
    """
    x0, y0, x1, y1 = bbox
    region = ink[y0:y1, x0:x1]
    if region.size == 0:
        return {"chars": [], "gaps_px": [], "median_gap_px": 0, "gap_deviation_pct": [], "max_deviation_pct": 0,
                "flag": "green"}
    col_sum = region.sum(axis=0)
    max_col = float(col_sum.max()) if col_sum.size else 0.0
    threshold = max_col * col_threshold_ratio
    ink_cols = col_sum > threshold

    # 連続したインク列を「文字ブロブ（結合前の生ブロブ）」としてまとめる
    blobs: list[tuple[int, int]] = []
    in_blob = False
    start = 0
    for i, v in enumerate(ink_cols):
        if v and not in_blob:
            start = i
            in_blob = True
        elif not v and in_blob:
            blobs.append((start, i))
            in_blob = False
    if in_blob:
        blobs.append((start, len(ink_cols)))

    # 濁点等の微小ギャップはブロブを結合して1文字として扱う。
    # 閾値は「生ブロブ間ギャップの中央値 × merge_gap_ratio」（下限 min_gap_px）で決める。
    raw_gaps = [blobs[i + 1][0] - blobs[i][1] for i in range(len(blobs) - 1)]
    raw_median_gap = float(np.median(raw_gaps)) if raw_gaps else 0.0
    merge_threshold = max(min_gap_px, raw_median_gap * merge_gap_ratio)

    merged: list[tuple[int, int]] = []
    for b in blobs:
        if merged and (b[0] - merged[-1][1]) < merge_threshold:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)

    total_ink = float(col_sum.sum())
    chars = []
    for (s, e) in merged:
        seg = col_sum[s:e]
        ink_pct = (float(seg.sum()) / total_ink * 100) if total_ink else 0.0
        seg_sum = float(seg.sum())
        if seg_sum > 0:
            centroid_local = float((np.arange(s, e) + 0.5) @ seg / seg_sum)
        else:
            centroid_local = (s + e) / 2
        chars.append({
            "start_px": int(s + x0),
            "end_px": int(e + x0),
            "width_px": int(e - s),
            "ink_pct": ink_pct,
            "centroid_x": centroid_local + x0,
        })

    gaps = [merged[i + 1][0] - merged[i][1] for i in range(len(merged) - 1)]
    median_gap = float(np.median(gaps)) if gaps else 0.0
    deviations = [abs(g - median_gap) / median_gap * 100 if median_gap else 0.0 for g in gaps]
    max_dev = max(deviations) if deviations else 0.0
    flag = c.score_flag(max_dev, c.KERNING_GAP_THRESHOLDS["green_max"], c.KERNING_GAP_THRESHOLDS["yellow_max"])

    # 隣接ブロブ間の重心間距離（食い込みペア検出の補助情報）
    centroid_gaps = []
    for i in range(len(chars) - 1):
        centroid_gaps.append(chars[i + 1]["centroid_x"] - chars[i]["centroid_x"])

    return {
        "chars": chars,
        "gaps_px": gaps,
        "median_gap_px": median_gap,
        "gap_deviation_pct": deviations,
        "max_deviation_pct": max_dev,
        "adjacent_centroid_distance_px": centroid_gaps,
        "flag": flag,
        "profile_width": int(region.shape[1]),
        "merge_threshold_px": merge_threshold,
    }


def cmd_kerning(args) -> dict:
    _, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)
    rois = c.load_json(args.rois)
    if args.roi_id:
        # 明示指定された roi-id が存在しない場合、別要素にフォールバックせず exit(2) する
        targets = [c.require_roi(rois, args.roi_id)]
    else:
        targets = [r for r in rois if r.get("type") == "text_line"]
        if not targets:
            print("error: text_line タイプの ROI が見つかりません（--roi-id を指定してください）", file=sys.stderr)
            sys.exit(2)

    results = []
    for roi in targets:
        bbox = c.refine_bbox(ink, roi["bbox"])
        res = analyze_kerning_bbox(ink, bbox, merge_gap_ratio=args.merge_gap_ratio, min_gap_px=args.min_gap_px)
        res["id"] = roi["id"]
        res["label"] = roi.get("label", roi["id"])
        res["refined_bbox"] = bbox
        results.append(res)

    return {"command": "kerning", "image": str(args.image), "results": results}


# ---------------------------------------------------------------------------
# color
# ---------------------------------------------------------------------------


def extract_palette(img: Image.Image, n_colors: int = 8) -> list[dict]:
    quant = img.convert("RGB").quantize(colors=n_colors, method=Image.MEDIANCUT)
    palette_flat = quant.getpalette() or []
    counts = quant.getcolors() or []
    total = sum(cnt for cnt, _ in counts) or 1
    entries = []
    for cnt, idx in sorted(counts, key=lambda t: -t[0]):
        rgb = tuple(palette_flat[idx * 3 : idx * 3 + 3])
        if len(rgb) < 3:
            continue
        entries.append({"rgb": list(rgb), "hex": c.rgb_to_hex(rgb), "pct": cnt / total * 100})
    return entries


def consolidate_by_deltae(palette: list[dict]) -> list[dict]:
    lo, hi = c.COLOR_DELTAE_MERGE_RANGE
    merges = []
    for i in range(len(palette)):
        for j in range(i + 1, len(palette)):
            de = c.delta_e76_rgb(tuple(palette[i]["rgb"]), tuple(palette[j]["rgb"]))
            if lo <= de <= hi:
                merges.append({"a": palette[i]["hex"], "b": palette[j]["hex"], "delta_e": de})
    return merges


def brand_color_matches(palette: list[dict]) -> list[dict]:
    lo, hi = c.COLOR_DELTAE_MERGE_RANGE
    flags = []
    for entry in palette:
        rgb = tuple(entry["rgb"])
        for name, (hexcode, category) in c.PALETTE_COLORS.items():
            de = c.delta_e76_rgb(rgb, c.hex_to_rgb(hexcode))
            if de <= hi:
                flags.append({
                    "color": entry["hex"],
                    "brand": name,
                    "brand_hex": hexcode,
                    # "brand" = ブランド6色 / "system" = システムカラー12色
                    "category": category,
                    # システムカラーは CMYK / DIC 未規定＝印刷物には指定できない
                    "print_ok": category == "brand",
                    "delta_e": de,
                    "exact_match": de < lo,
                })
    return flags


def analyze_text_contrast(ink: np.ndarray, arr: np.ndarray, bbox, threshold: float = 0.15) -> dict:
    region_ink = c.crop_bbox(ink, bbox)
    region_rgb = c.crop_bbox(arr, bbox)
    fg_mask = region_ink > threshold
    bg_mask = ~fg_mask
    if fg_mask.sum() == 0 or bg_mask.sum() == 0:
        return {"available": False, "reason": "前景/背景いずれかの画素が検出できませんでした"}
    fg_rgb = tuple(np.median(region_rgb[fg_mask], axis=0).tolist())
    bg_rgb = tuple(np.median(region_rgb[bg_mask], axis=0).tolist())
    ratio = c.contrast_ratio(fg_rgb, bg_rgb)
    return {
        "available": True,
        "foreground_rgb": [round(v) for v in fg_rgb],
        "background_rgb": [round(v) for v in bg_rgb],
        "contrast_ratio": ratio,
        "wcag_normal_text": c.wcag_level(ratio, large_text=False),
        "wcag_large_text": c.wcag_level(ratio, large_text=True),
    }


def _load_rois_for_color(args) -> list[dict]:
    if not args.rois:
        print("error: --roi-id/--text-roi を指定する場合は --rois の指定が必要です", file=sys.stderr)
        sys.exit(2)
    return c.load_json(args.rois)


def cmd_color(args) -> dict:
    img, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)

    rois = None
    if args.roi_id or args.text_roi:
        rois = _load_rois_for_color(args)

    if args.roi_id or args.bbox:
        if args.bbox:
            bbox = [int(v) for v in args.bbox.split(",")]
            crop = img.crop(tuple(bbox))
        else:
            roi = c.require_roi(rois, args.roi_id)
            bbox = c.refine_bbox(ink, roi["bbox"])
            crop = img.crop(tuple(bbox))
        target_img = crop
    else:
        target_img = img
        bbox = [0, 0, img.width, img.height]

    palette = extract_palette(target_img, n_colors=args.n_colors)
    merges = consolidate_by_deltae(palette)
    brand_flags = brand_color_matches(palette)

    result = {
        "command": "color",
        "image": str(args.image),
        "bbox": bbox,
        "palette": palette,
        "consolidation_candidates": merges,
        "brand_color_flags": brand_flags,
    }

    if args.text_roi:
        text_roi = c.require_roi(rois, args.text_roi)
        tbbox = c.refine_bbox(ink, text_roi["bbox"])
        result["text_contrast"] = analyze_text_contrast(ink, arr, tbbox)
        result["text_contrast"]["roi_id"] = args.text_roi

    return result


# ---------------------------------------------------------------------------
# composition（紙面全体の重心ズレビジュアル）
# ---------------------------------------------------------------------------


def _roi_bbox_area(roi: dict) -> float:
    x0, y0, x1, y1 = roi["bbox"]
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def compute_composition(img: Image.Image, ink: np.ndarray, rois: list[dict], px_per_mm: float | None):
    """基準コンテナ（最大の container ROI。無ければ画像全体）を基準に、
    非 container ROI（element/text_line）の光学重心のズレを一括計算する。

    個々の要素の `relative_to` 設定に関わらず、composition では常に
    「紙面全体でどこがどれだけズレているか」を同一基準で横並び比較する。
    """
    containers = [r for r in rois if r.get("type") == "container"]
    if containers:
        ref_roi = max(containers, key=_roi_bbox_area)
        ref_bbox = [float(v) for v in ref_roi["bbox"]]
        ref_id = ref_roi["id"]
        ref_label = ref_roi.get("label", ref_roi["id"])
    else:
        ref_bbox = [0.0, 0.0, float(img.width), float(img.height)]
        ref_id = None
        ref_label = "画像全体"

    ref_center = c.bbox_center(ref_bbox)
    ref_width = ref_bbox[2] - ref_bbox[0]

    items = []
    for roi in rois:
        if roi.get("type") == "container":
            continue
        bbox = c.refine_bbox(ink, roi["bbox"])
        cx, cy, mass = c.ink_centroid(ink, bbox)
        dx, dy = cx - ref_center[0], cy - ref_center[1]
        delta_px = math.hypot(dx, dy)
        delta_pct = (delta_px / ref_width * 100) if ref_width else 0.0
        flag = c.score_flag(delta_pct, c.GRAVITY_THRESHOLDS["green_max"], c.GRAVITY_THRESHOLDS["yellow_max"])
        entry = {
            "id": roi["id"],
            "label": roi.get("label", roi["id"]),
            "refined_bbox": bbox,
            "optical_centroid": [cx, cy],
            "ink_mass": mass,
            "delta_px": {"x": dx, "y": dy, "magnitude": delta_px},
            "delta_pct_of_width": delta_pct,
            "flag": flag,
        }
        if px_per_mm:
            entry["delta_mm"] = {
                "x": c.px_to_mm(dx, px_per_mm),
                "y": c.px_to_mm(dy, px_per_mm),
                "magnitude": c.px_to_mm(delta_px, px_per_mm),
            }
        items.append(entry)

    reference = {"id": ref_id, "label": ref_label, "bbox": ref_bbox, "center": list(ref_center)}
    return reference, items


def render_composition(img: Image.Image, reference: dict, items: list[dict], out_path: Path,
                        px_per_mm: float | None = None, max_edge: int = 2400) -> None:
    w, h = img.size
    scale = min(1.0, max_edge / max(w, h)) if max(w, h) else 1.0
    if scale < 1.0:
        canvas = img.convert("RGB").resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    else:
        canvas = img.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)

    def sx(v: float) -> float:
        return v * scale

    label_font_size = max(16, round(min(canvas.size) * 0.022))
    small_font_size = max(13, round(min(canvas.size) * 0.016))
    label_font, jp_ok = c.load_label_font(label_font_size)
    small_font, _ = c.load_label_font(small_font_size)

    def label_of(item: dict) -> str:
        return item["label"] if jp_ok else item["id"]

    # 基準コンテナの幾何中心を通る垂直・水平の灰破線基準線
    rcx, rcy = sx(reference["center"][0]), sx(reference["center"][1])
    c.draw_dashed_line(draw, (rcx, 0, rcx, canvas.height), (140, 140, 140), width=2, dash=12, gap=7)
    c.draw_dashed_line(draw, (0, rcy, canvas.width, rcy), (140, 140, 140), width=2, dash=12, gap=7)

    colors = list(c.BRAND_COLORS.values())
    min_arrow_len = 24  # Δが小さくても矢印が視認できるように確保する最小長(px, 描画スケール後)

    for i, item in enumerate(items):
        color = c.hex_to_rgb(colors[i % len(colors)])

        bx0, by0, bx1, by1 = (sx(v) for v in item["refined_bbox"])
        draw.rectangle([bx0, by0, bx1, by1], outline=color, width=2)

        ocx, ocy = sx(item["optical_centroid"][0]), sx(item["optical_centroid"][1])
        c.draw_cross(draw, (ocx, ocy), color, size=14, width=3, dashed=False)

        # 基準中心線から重心までの水平オフセット矢印（最小矢印長を確保。ラベルは正確な値を表示）
        start_x, end_x = rcx, ocx
        if abs(end_x - start_x) < min_arrow_len:
            direction = 1 if end_x >= start_x else -1
            end_x = start_x + direction * min_arrow_len
        c.draw_arrow(draw, (start_x, ocy), (end_x, ocy), color, width=3, head=8)

        dx, dy = item["delta_px"]["x"], item["delta_px"]["y"]
        if px_per_mm:
            dx_mm = item["delta_mm"]["x"]
            text = f"{label_of(item)} Δx {dx:+.1f}px ({dx_mm:+.2f}mm)"
        else:
            text = f"{label_of(item)} Δx {dx:+.1f}px"
        text_anchor_x = end_x + 8 if end_x >= start_x else end_x - 8
        # 重心位置が近い要素同士でラベルが完全に重ならないよう、インデックスで縦にずらす
        text_y = ocy - label_font_size - 6 - (i % 4) * (label_font_size + 6)
        try:
            tb = draw.textbbox((text_anchor_x, text_y), text, font=label_font)
            text_w = tb[2] - tb[0]
        except Exception:
            text_w = len(text) * label_font_size * 0.6
        if end_x < start_x:
            text_anchor_x -= text_w
        draw.text((text_anchor_x, text_y), text, fill=color, font=label_font)

    # 凡例ブロック（右下）: 要素名・Δx/Δy(px/mm)・flag(●色)
    row_h = int(small_font_size * 1.9)
    padding = 14
    legend_w = min(canvas.width - 32, max(360, round(canvas.width * 0.38)))
    legend_h = padding * 2 + row_h * (len(items) + 1)
    lx1 = canvas.width - 16
    ly1 = canvas.height - 16
    lx0 = lx1 - legend_w
    ly0 = ly1 - legend_h

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rectangle([lx0, ly0, lx1, ly1], fill=(255, 255, 255, 235), outline=(120, 120, 120, 255), width=1)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    ref_title = f"基準: {reference['label']}" if jp_ok else f"ref: {reference['id'] or 'image'}"
    draw.text((lx0 + padding, ly0 + padding), ref_title, fill=(40, 40, 40), font=small_font)

    y = ly0 + padding + row_h
    for i, item in enumerate(items):
        color = c.hex_to_rgb(colors[i % len(colors)])
        swatch_y0 = y + row_h * 0.2
        swatch_y1 = y + row_h * 0.75
        draw.rectangle([lx0 + padding, swatch_y0, lx0 + padding + 14, swatch_y1], fill=color)

        flag_color = c.FLAG_RGB.get(item["flag"], (120, 120, 120))
        draw.ellipse([lx1 - padding - 14, swatch_y0, lx1 - padding, swatch_y1], fill=flag_color)

        dx, dy = item["delta_px"]["x"], item["delta_px"]["y"]
        if px_per_mm:
            dxmm, dymm = item["delta_mm"]["x"], item["delta_mm"]["y"]
            text = f"{label_of(item)}  Δx{dx:+.1f}px Δy{dy:+.1f}px ({dxmm:+.2f}/{dymm:+.2f}mm)"
        else:
            text = f"{label_of(item)}  Δx{dx:+.1f}px Δy{dy:+.1f}px"
        draw.text((lx0 + padding + 22, y), text, fill=(30, 30, 30), font=small_font)
        y += row_h

    canvas.save(out_path)


def cmd_composition(args) -> dict:
    img, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)
    rois = c.load_json(args.rois)

    reference, items = compute_composition(img, ink, rois, args.px_per_mm)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    comp_path = out_dir / "composition.png"
    render_composition(img, reference, items, comp_path, px_per_mm=args.px_per_mm)

    return {
        "command": "composition",
        "image": str(args.image),
        "composition_image": str(comp_path),
        "reference": reference,
        "items": items,
    }


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------


def cmd_all(args) -> dict:
    img, arr = c.load_image_rgb(args.image)
    bg = c.estimate_background(arr)
    ink = c.compute_ink_map(arr, bg)
    rois = c.load_json(args.rois)
    rois_index = _rois_by_id(rois)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    combined: dict = {"command": "all", "image": str(args.image), "background_rgb": list(bg)}

    # gravity: text_line / element / container タイプすべて
    gravity_targets = [r for r in rois if r.get("type") in ("text_line", "element", "container")]
    gravity_results = []
    for roi in gravity_targets:
        res = analyze_gravity_one(ink, roi, rois_index, args.px_per_mm)
        overlay_path = out_dir / f"gravity_{roi['id']}.png"
        render_gravity_overlay(img, res, overlay_path)
        res["overlay_image"] = str(overlay_path)
        gravity_results.append(res)
    combined["gravity"] = gravity_results

    # size: group が振られているものをグループ化
    groups = sorted({r["group"] for r in rois if r.get("group")})
    size_results = []
    for g in groups:
        members = [r for r in rois if r.get("group") == g]
        items = []
        for r in members:
            bbox = c.refine_bbox(ink, r["bbox"])
            diam = c.visual_diameter(ink, bbox)
            items.append({"id": r["id"], "label": r.get("label", r["id"]), "refined_bbox": bbox,
                          "visual_diameter": diam, "fill_ratio": c.fill_ratio(ink, bbox)})
        diam_values = np.array([it["visual_diameter"] for it in items], dtype=np.float64)
        mean = float(diam_values.mean())
        std = float(diam_values.std(ddof=0))
        cv = (std / mean * 100) if mean else 0.0
        outliers = [it["id"] for it in items if std > 0 and abs(it["visual_diameter"] - mean) / std > 1.0]
        size_results.append({
            "group": g, "items": items, "mean_diameter": mean, "std_diameter": std,
            "cv_pct": cv, "outliers": outliers,
            "flag": c.score_flag(cv, c.SIZE_CV_THRESHOLDS["green_max"], c.SIZE_CV_THRESHOLDS["yellow_max"]),
        })
    combined["size"] = size_results

    # kerning: text_line タイプすべて
    kerning_results = []
    for roi in [r for r in rois if r.get("type") == "text_line"]:
        bbox = c.refine_bbox(ink, roi["bbox"])
        res = analyze_kerning_bbox(ink, bbox)
        res["id"] = roi["id"]
        res["label"] = roi.get("label", roi["id"])
        res["refined_bbox"] = bbox
        kerning_results.append(res)
    combined["kerning"] = kerning_results

    # color: 画像全体のパレット + text_line ROI ごとのコントラスト
    palette = extract_palette(img, n_colors=args.n_colors)
    combined["color"] = {
        "palette": palette,
        "consolidation_candidates": consolidate_by_deltae(palette),
        "brand_color_flags": brand_color_matches(palette),
        "text_contrast": [
            {**analyze_text_contrast(ink, arr, c.refine_bbox(ink, r["bbox"])), "roi_id": r["id"]}
            for r in rois if r.get("type") == "text_line"
        ],
    }

    # composition: container ROI が存在する場合のみ紙面全体の重心ズレビジュアルを自動生成する
    if any(r.get("type") == "container" for r in rois):
        reference, comp_items = compute_composition(img, ink, rois, args.px_per_mm)
        comp_path = out_dir / "composition.png"
        render_composition(img, reference, comp_items, comp_path, px_per_mm=args.px_per_mm)
        combined["composition"] = {
            "composition_image": str(comp_path),
            "reference": reference,
            "items": comp_items,
        }

    return combined


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="design-check: 画像の重心・大きさ・カーニング・色を解析する")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grid", help="グリッド焼き込み画像を生成する")
    g.add_argument("--image", required=True)
    g.add_argument("--out-dir", required=True)
    g.add_argument("--spacing", type=int, default=100)
    g.set_defaults(func=cmd_grid)

    gr = sub.add_parser("gravity", help="重心のズレを計測する")
    gr.add_argument("--image", required=True)
    gr.add_argument("--rois", required=True)
    gr.add_argument("--out-dir", required=True)
    gr.add_argument("--roi-id", default=None)
    gr.add_argument("--px-per-mm", type=float, default=None)
    gr.set_defaults(func=cmd_gravity)

    sz = sub.add_parser("size", help="グループ内の視覚的大きさのばらつきを計測する")
    sz.add_argument("--image", required=True)
    sz.add_argument("--rois", required=True)
    sz.add_argument("--group", required=True)
    sz.set_defaults(func=cmd_size)

    kr = sub.add_parser("kerning", help="文字間ギャップを計測する")
    kr.add_argument("--image", required=True)
    kr.add_argument("--rois", required=True)
    kr.add_argument("--roi-id", default=None)
    kr.add_argument("--merge-gap-ratio", type=float, default=0.15,
                     help="濁点等の微小ギャップ結合の閾値（生ブロブ間ギャップ中央値に対する比率、デフォルト0.15）")
    kr.add_argument("--min-gap-px", type=float, default=1.0, help="上記閾値の絶対下限(px)")
    kr.set_defaults(func=cmd_kerning)

    co = sub.add_parser("color", help="パレット抽出・ブランド色照合・コントラスト判定を行う")
    co.add_argument("--image", required=True)
    co.add_argument("--rois", default=None)
    co.add_argument("--roi-id", default=None)
    co.add_argument("--bbox", default=None, help="x0,y0,x1,y1（--rois の代わりに直接指定）")
    co.add_argument("--text-roi", default=None, help="コントラスト判定を行う text_line ROI の id")
    co.add_argument("--n-colors", type=int, default=8)
    co.set_defaults(func=cmd_color)

    cp = sub.add_parser("composition", help="紙面全体の重心ズレをビジュアル化する")
    cp.add_argument("--image", required=True)
    cp.add_argument("--rois", required=True)
    cp.add_argument("--out-dir", required=True)
    cp.add_argument("--px-per-mm", type=float, default=None)
    cp.set_defaults(func=cmd_composition)

    al = sub.add_parser("all", help="rois.json から関連解析をまとめて実行する")
    al.add_argument("--image", required=True)
    al.add_argument("--rois", required=True)
    al.add_argument("--out-dir", required=True)
    al.add_argument("--px-per-mm", type=float, default=None)
    al.add_argument("--n-colors", type=int, default=8)
    al.set_defaults(func=cmd_all)

    return p


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    c.print_json(result)


if __name__ == "__main__":
    main()
