# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy"]
# ///
"""design-check スキル共通ユーティリティ.

- 色空間変換（sRGB <-> XYZ <-> Lab）と ΔE76
- WCAG コントラスト比
- 背景推定・インクマップ（背景からの色差で「インク量」を数値化）
- ROI（Region of Interest）操作: refine_bbox / 重心 / 面積 / 視覚直径
- オーバーレイ描画（破線十字・矢印）
- ブランド6色 / システムカラー12色の定数・判定閾値

analyze.py / simulate.py / build_report.py / svg_charts.py から import して使う。
このファイル単体では何も実行しない。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# ブランドカラー（★★★ 利用者が自分のブランド色に差し替える ★★★）
#
# ここに入っているのは値の入れかたを示すための**中立なプレースホルダ**で、
# どこかの組織のブランド色ではない。自分のブランドガイドの値に置き換えて使う。
# 差し替えないまま走らせると、色の観点だけが無意味な結果を返す
# （重心・大きさ・カーニングの3観点は影響を受けない）。
# ---------------------------------------------------------------------------

BRAND_COLORS = {
    "Brand1": "#3B82F6",
    "Brand2": "#10B981",
    "Brand3": "#F59E0B",
    "Brand4": "#EC4899",
    "Brand5": "#1E3A5F",
    "Brand6": "#EDE4DA",
}

# ---------------------------------------------------------------------------
# システムカラー（同上・利用者が差し替える）
#
# ブランドカラーと重ならない補助色。アプリ内のカテゴリ分けやグラフに用いる想定。
# 自分のガイドに系統の規定が無ければ、空の dict にしてよい。
# ---------------------------------------------------------------------------

SYSTEM_COLORS = {
    "Red": "#EF4444",
    "Purple": "#A855F7",
    "Orange": "#F97316",
    "Lime": "#84CC16",
    "Aqua": "#22D3EE",
    "Brown": "#A16207",
    "Green": "#22C55E",
    "Orchid": "#D946EF",
    "Indigo": "#6366F1",
    "Grass": "#4ADE80",
    "Gold": "#CA8A04",
    "Gray": "#9CA3AF",
}

# 照合に使う全パレット: name -> (hex, category)
# category は "brand"（ブランド6色）/ "system"（システムカラー12色）
PALETTE_COLORS = {
    **{n: (h, "brand") for n, h in BRAND_COLORS.items()},
    **{n: (h, "system") for n, h in SYSTEM_COLORS.items()},
}

# ---------------------------------------------------------------------------
# 判定閾値（SKILL.md の閾値表と一致させること）
# ---------------------------------------------------------------------------

# ① 重心: 要素幅に対する Δpx の比率(%)
GRAVITY_THRESHOLDS = {"green_max": 0.5, "yellow_max": 1.5}

# ② 大きさ: グループ内 visual_diameter の CV(%)
SIZE_CV_THRESHOLDS = {"green_max": 3.0, "yellow_max": 8.0}

# ③ カーニング: 中央値ギャップからの偏差(%)
KERNING_GAP_THRESHOLDS = {"green_max": 10.0, "yellow_max": 25.0}

# ④ 色: ΔE76 の「統合候補 / ブランド色ズレ疑い」レンジ
COLOR_DELTAE_MERGE_RANGE = (2.3, 10.0)

FLAG_EMOJI = {"green": "\U0001F7E2", "yellow": "\U0001F7E1", "red": "\U0001F534"}
# レポート/オーバーレイの ●(凡例ドット)描画用 RGB（FLAG_EMOJI の色相に対応させている）
FLAG_RGB = {"green": (0, 176, 96), "yellow": (224, 168, 0), "red": (224, 60, 90)}


def score_flag(value: float, green_max: float, yellow_max: float) -> str:
    """値が小さいほど良い指標を green/yellow/red に分類する。"""
    if value < green_max:
        return "green"
    if value < yellow_max:
        return "yellow"
    return "red"


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------


def load_json(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(obj, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def print_json(obj) -> None:
    """analyze.py / simulate.py の唯一の標準出力チャネル。base64・生画素は含めない。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# ROI 解決（typo等でサイレント失敗しないための共通ガード）
# ---------------------------------------------------------------------------


def require_roi(rois: list, roi_id: str) -> dict:
    """--roi-id で明示指定された ROI が rois.json に存在することを保証する。

    見つからない場合、別要素の結果を返すサイレント失敗を防ぐため、
    定義済み ROI id 一覧を添えてエラーメッセージを stderr に出し exit(2) する。
    """
    match = next((r for r in rois if r.get("id") == roi_id), None)
    if match is None:
        available = [r.get("id") for r in rois]
        print(f"error: roi-id '{roi_id}' が rois.json に見つかりません。定義済み: {available}", file=sys.stderr)
        sys.exit(2)
    return match


def require_group(rois: list, group: str) -> list:
    """--group で明示指定されたグループに属する ROI が1件以上存在することを保証する。"""
    members = [r for r in rois if r.get("group") == group]
    if not members:
        available = sorted({r.get("group") for r in rois if r.get("group")})
        print(f"error: group '{group}' が rois.json に見つかりません。定義済み: {available}", file=sys.stderr)
        sys.exit(2)
    return members


# ---------------------------------------------------------------------------
# 色空間変換: sRGB <-> XYZ(D65) <-> Lab
# ---------------------------------------------------------------------------


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(round(c)) for c in rgb))


def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


# D65 白色点（2度視野）
_XN, _YN, _ZN = 95.0489, 100.0, 108.8840


def rgb_to_xyz(rgb) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    return x * 100, y * 100, z * 100


def _f(t: float) -> float:
    delta = 6 / 29
    return t ** (1 / 3) if t > delta**3 else t / (3 * delta**2) + 4 / 29


def _f_inv(t: float) -> float:
    delta = 6 / 29
    return t**3 if t > delta else 3 * delta**2 * (t - 4 / 29)


def xyz_to_lab(xyz) -> tuple[float, float, float]:
    x, y, z = xyz
    fx, fy, fz = _f(x / _XN), _f(y / _YN), _f(z / _ZN)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b = 200 * (fy - fz)
    return L, a, b


def lab_to_xyz(lab) -> tuple[float, float, float]:
    L, a, b = lab
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    return _XN * _f_inv(fx), _YN * _f_inv(fy), _ZN * _f_inv(fz)


def xyz_to_rgb(xyz) -> tuple[int, int, int]:
    x, y, z = xyz[0] / 100, xyz[1] / 100, xyz[2] / 100
    r = x * 3.2404542 + y * -1.5371385 + z * -0.4985314
    g = x * -0.9692660 + y * 1.8760108 + z * 0.0415560
    b = x * 0.0556434 + y * -0.2040259 + z * 1.0572252
    r, g, b = (_linear_to_srgb(c) for c in (r, g, b))
    return tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in (r, g, b))  # type: ignore[return-value]


def rgb_to_lab(rgb) -> tuple[float, float, float]:
    return xyz_to_lab(rgb_to_xyz(rgb))


def lab_to_rgb(lab) -> tuple[int, int, int]:
    return xyz_to_rgb(lab_to_xyz(lab))


def delta_e76(lab1, lab2) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(lab1, lab2)))


def delta_e76_rgb(rgb1, rgb2) -> float:
    return delta_e76(rgb_to_lab(rgb1), rgb_to_lab(rgb2))


# ---------------------------------------------------------------------------
# WCAG コントラスト
# ---------------------------------------------------------------------------


def relative_luminance(rgb) -> float:
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb1, rgb2) -> float:
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def wcag_level(ratio: float, large_text: bool = False) -> str:
    """WCAG 2.x 達成基準（AA=4.5:1 / 大文字AA=3:1 / AAA=7:1）に対する判定。"""
    if large_text:
        if ratio >= 4.5:
            return "AAA"
        if ratio >= 3.0:
            return "AA"
        return "fail"
    if ratio >= 7.0:
        return "AAA"
    if ratio >= 4.5:
        return "AA"
    return "fail"


# ---------------------------------------------------------------------------
# 画像 I/O
# ---------------------------------------------------------------------------


def load_image_rgb(path) -> tuple[Image.Image, np.ndarray]:
    img = Image.open(path).convert("RGB")
    return img, np.asarray(img, dtype=np.float64)


# ---------------------------------------------------------------------------
# 背景推定・インクマップ
# ---------------------------------------------------------------------------


def estimate_background(arr: np.ndarray, band: int = 2) -> tuple[float, float, float]:
    """外周 band px 帯の中央値を背景色として推定する。"""
    h, w, _ = arr.shape
    band = max(1, min(band, h // 2, w // 2))
    top = arr[:band, :, :].reshape(-1, 3)
    bottom = arr[-band:, :, :].reshape(-1, 3)
    left = arr[:, :band, :].reshape(-1, 3)
    right = arr[:, -band:, :].reshape(-1, 3)
    pixels = np.concatenate([top, bottom, left, right], axis=0)
    bg = np.median(pixels, axis=0)
    return tuple(bg.tolist())  # type: ignore[return-value]


def _srgb_to_linear_vec(c: np.ndarray) -> np.ndarray:
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _rgb_to_lab_vectorized(flat_rgb: np.ndarray) -> np.ndarray:
    lin = _srgb_to_linear_vec(flat_rgb)
    r, g, b = lin[:, 0], lin[:, 1], lin[:, 2]
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    x, y, z = x * 100, y * 100, z * 100

    def f(t: np.ndarray) -> np.ndarray:
        delta = 6 / 29
        return np.where(t > delta**3, np.cbrt(t), t / (3 * delta**2) + 4 / 29)

    fx, fy, fz = f(x / _XN), f(y / _YN), f(z / _ZN)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    bb = 200 * (fy - fz)
    return np.stack([L, a, bb], axis=1)


def compute_ink_map(arr: np.ndarray, bg_rgb) -> np.ndarray:
    """各ピクセルの背景色からの ΔE76 を 0-1 に正規化した「インク量」マップを返す。"""
    h, w, _ = arr.shape
    bg_lab = np.array(rgb_to_lab(bg_rgb))
    flat = arr.reshape(-1, 3)
    labs = _rgb_to_lab_vectorized(flat)
    diff = labs - bg_lab
    de = np.sqrt(np.sum(diff * diff, axis=1))
    de_map = de.reshape(h, w)
    max_de = de_map.max()
    if max_de < 1e-9:
        return np.zeros((h, w))
    return de_map / max_de


# ---------------------------------------------------------------------------
# ROI / BBox 操作
# 座標系: bbox = [x0, y0, x1, y1]（x1, y1 は半開区間の上限＝PIL の crop と同じ）
# ---------------------------------------------------------------------------


def crop_bbox(arr: np.ndarray, bbox) -> np.ndarray:
    x0, y0, x1, y1 = (int(v) for v in bbox)
    return arr[y0:y1, x0:x1]


def refine_bbox(ink_map: np.ndarray, bbox, ink_threshold: float = 0.15, pad: int = 2):
    """目視で与えた粗い ROI をインクが存在する範囲にタイト化する。

    インクが見つからない場合は元の bbox をそのまま返す。
    """
    x0, y0, x1, y1 = (int(v) for v in bbox)
    h, w = ink_map.shape
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return [x0, y0, x1, y1]
    region = ink_map[y0:y1, x0:x1]
    mask = region > ink_threshold
    if not mask.any():
        return [x0, y0, x1, y1]
    ys, xs = np.where(mask)
    rx0 = max(0, x0 + int(xs.min()) - pad)
    ry0 = max(0, y0 + int(ys.min()) - pad)
    rx1 = min(w, x0 + int(xs.max()) + 1 + pad)
    ry1 = min(h, y0 + int(ys.max()) + 1 + pad)
    return [int(rx0), int(ry0), int(rx1), int(ry1)]


def ink_centroid(ink_map: np.ndarray, bbox) -> tuple[float, float, float]:
    """インク密度重み付き重心（画像全体座標系）と総インク量を返す。

    ピクセル中心（i+0.5）を用いることで、一様なインク矩形の重心が
    bbox_center（座標の算術中心）と厳密に一致するようにしている。
    """
    x0, y0, x1, y1 = (int(v) for v in bbox)
    region = ink_map[y0:y1, x0:x1]
    total = float(region.sum())
    if total <= 0:
        cx, cy = bbox_center([x0, y0, x1, y1])
        return cx, cy, 0.0
    ys, xs = np.indices(region.shape)
    cx = float(((xs + 0.5) * region).sum() / total) + x0
    cy = float(((ys + 0.5) * region).sum() / total) + y0
    return cx, cy, total


def bbox_center(bbox) -> tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return (x0 + x1) / 2, (y0 + y1) / 2


def ink_area(ink_map: np.ndarray, bbox, threshold: float = 0.15) -> int:
    region = crop_bbox(ink_map, bbox)
    return int((region > threshold).sum())


def visual_diameter(ink_map: np.ndarray, bbox, threshold: float = 0.15) -> float:
    area = ink_area(ink_map, bbox, threshold)
    return 2 * math.sqrt(area / math.pi)


def fill_ratio(ink_map: np.ndarray, bbox, threshold: float = 0.15) -> float:
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return 0.0
    return ink_area(ink_map, bbox, threshold) / (w * h)


# ---------------------------------------------------------------------------
# 描画（オーバーレイ）
# ---------------------------------------------------------------------------


def draw_dashed_line(draw: ImageDraw.ImageDraw, xy, fill, width: int = 1, dash: int = 6, gap: int = 4) -> None:
    x0, y0, x1, y1 = xy
    length = math.hypot(x1 - x0, y1 - y0)
    if length == 0:
        return
    dx, dy = (x1 - x0) / length, (y1 - y0) / length
    pos = 0.0
    draw_on = True
    while pos < length:
        seg = dash if draw_on else gap
        end = min(pos + seg, length)
        if draw_on:
            sx0, sy0 = x0 + dx * pos, y0 + dy * pos
            sx1, sy1 = x0 + dx * end, y0 + dy * end
            draw.line([sx0, sy0, sx1, sy1], fill=fill, width=width)
        pos = end
        draw_on = not draw_on


def draw_cross(draw: ImageDraw.ImageDraw, point, color, size: int = 14, width: int = 2, dashed: bool = False) -> None:
    cx, cy = point
    if dashed:
        draw_dashed_line(draw, (cx - size, cy, cx + size, cy), color, width)
        draw_dashed_line(draw, (cx, cy - size, cx, cy + size), color, width)
    else:
        draw.line([cx - size, cy, cx + size, cy], fill=color, width=width)
        draw.line([cx, cy - size, cx, cy + size], fill=color, width=width)


def draw_arrow(draw: ImageDraw.ImageDraw, p0, p1, color, width: int = 2, head: int = 6) -> None:
    draw.line([p0[0], p0[1], p1[0], p1[1]], fill=color, width=width)
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    for da in (math.pi * 0.8, -math.pi * 0.8):
        hx = p1[0] + head * math.cos(ang + da)
        hy = p1[1] + head * math.sin(ang + da)
        draw.line([p1[0], p1[1], hx, hy], fill=color, width=width)


# ---------------------------------------------------------------------------
# 日本語ラベル描画用フォント
# ---------------------------------------------------------------------------

# macOS 標準搭載のヒラギノを優先的に試す（存在確認済みのパスを先頭に）
JP_FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


def load_label_font(size: int) -> tuple:
    """日本語ラベル描画用フォントを読み込む。

    候補パスを順に `ImageFont.truetype` で試し、最初に成功したものを返す。
    すべて失敗した場合は PIL のデフォルトフォント（日本語グリフを持たない）に
    フォールバックする。戻り値は `(font, japanese_ok)`。
    `japanese_ok=False` の場合、呼び出し側はラベル文字列を id（英数字）に
    差し替えて tofu（豆腐文字）化を防ぐこと。
    """
    for path in JP_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size), True
        except Exception:
            continue
    return ImageFont.load_default(), False


# ---------------------------------------------------------------------------
# 単位変換
# ---------------------------------------------------------------------------


def px_to_mm(px: float, px_per_mm: float | None):
    if not px_per_mm:
        return None
    return px / px_per_mm
