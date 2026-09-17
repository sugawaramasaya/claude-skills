# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy"]
# ///
"""design-check: 理論値が既知の合成画像フィクスチャを生成する。

生成物と対応する理論値は tests/expected.md に記載している。
このスクリプトと expected.md の数値は同じ幾何パラメータから導出しているため、
数値を変更する場合は両方を必ず一緒に更新すること。

使い方:
    uv run make_fixtures.py --out-dir fixtures
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common as c  # noqa: E402

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def _new_canvas(w: int, h: int, color=WHITE) -> np.ndarray:
    arr = np.empty((h, w, 3), dtype=np.uint8)
    arr[:, :] = color
    return arr


def _fill_rect(arr: np.ndarray, x0: int, y0: int, x1: int, y1: int, color) -> None:
    arr[y0:y1, x0:x1] = color


def _save(arr: np.ndarray, path: Path) -> None:
    Image.fromarray(arr, mode="RGB").save(path)


# ---------------------------------------------------------------------------
# fixture 1: gravity（重心ズレ）
# 幾何パラメータ・理論値の導出は expected.md 「① 重心フィクスチャ」を参照。
# ---------------------------------------------------------------------------

GRAVITY_CANVAS = (340, 240)  # (w, h)
GRAVITY_OFFSET = (20, 20)  # シェイプ全体の平行移動量（境界を canvas 中央付近に収めるため）
# R1（主要ブロック）: 幅 W1=39, 高さ H1=100、shape 内ローカル座標で x:[0,39) y:[100,200)
GRAVITY_R1_LOCAL = (0, 100, 39, 200)
# R2（右上の小ブロック）: 幅 W2=50, 高さ H2=95、shape 内ローカル座標で x:[250,300) y:[0,95)
GRAVITY_R2_LOCAL = (250, 0, 300, 95)
GRAVITY_ROI_LOOSE_BBOX = [10, 10, 330, 230]  # 目視ラフ ROI（refine_bbox で自動タイト化される）


def make_gravity_fixture(out_dir: Path) -> dict:
    w, h = GRAVITY_CANVAS
    ox, oy = GRAVITY_OFFSET
    arr = _new_canvas(w, h, WHITE)
    x0, y0, x1, y1 = GRAVITY_R1_LOCAL
    _fill_rect(arr, x0 + ox, y0 + oy, x1 + ox, y1 + oy, BLACK)
    x0, y0, x1, y1 = GRAVITY_R2_LOCAL
    _fill_rect(arr, x0 + ox, y0 + oy, x1 + ox, y1 + oy, BLACK)

    path = out_dir / "gravity_rect.png"
    _save(arr, path)

    rois = [
        {
            "id": "gravity_shape",
            "label": "重心ズレ検証シェイプ",
            "type": "container",
            "bbox": GRAVITY_ROI_LOOSE_BBOX,
            # 自己参照の relative_to: 「このシェイプが元々収まっていた枠」を
            # 固定参照点として simulate.py offset の補正効果を検証するために使う。
            # （before 計算時に一度だけ確定するため、要素を平行移動しても参照点は動かない）
            "relative_to": "gravity_shape",
        }
    ]
    c.save_json(rois, out_dir / "gravity_rois.json")
    return {"image": str(path), "rois": str(out_dir / "gravity_rois.json")}


# ---------------------------------------------------------------------------
# fixture 2: kerning（文字間ギャップ）
# 6個のグリフブロックを配置。gap3（3-4間）だけ 25px と異常値、
# glyph5-glyph6 は 1px の微小ギャップで結合される想定。
# ---------------------------------------------------------------------------

KERNING_GLYPH_W = 20
KERNING_GLYPH_H = 60
KERNING_MARGIN = 20
# (開始x, 終了x) のローカル座標（オフセット前）
KERNING_GLYPHS_LOCAL = [
    (0, 20),      # glyph1
    (30, 50),     # glyph2  (gap before = 10)
    (60, 80),     # glyph3  (gap before = 10)
    (105, 125),   # glyph4  (gap before = 25 <- 異常値)
    (135, 155),   # glyph5  (gap before = 10)
    (156, 176),   # glyph6  (gap before = 1  <- 微小、glyph5 と結合される)
]
KERNING_EXPECTED_GAPS = [10, 10, 25, 10]  # 結合後のブロブ間ギャップ
KERNING_ROI_LOOSE_BBOX_LOCAL = [-5, -5, 181, 65]


def make_kerning_fixture(out_dir: Path) -> dict:
    m = KERNING_MARGIN
    max_x1 = max(e for _, e in KERNING_GLYPHS_LOCAL)
    w, h = max_x1 + 2 * m, KERNING_GLYPH_H + 2 * m
    arr = _new_canvas(w, h, WHITE)
    for s, e in KERNING_GLYPHS_LOCAL:
        _fill_rect(arr, s + m, m, e + m, KERNING_GLYPH_H + m, BLACK)

    path = out_dir / "kerning_string.png"
    _save(arr, path)

    loose = KERNING_ROI_LOOSE_BBOX_LOCAL
    roi_bbox = [loose[0] + m, loose[1] + m, loose[2] + m, loose[3] + m]
    rois = [
        {
            "id": "kerning_line",
            "label": "カーニング検証文字列",
            "type": "text_line",
            "bbox": roi_bbox,
        }
    ]
    c.save_json(rois, out_dir / "kerning_rois.json")
    return {"image": str(path), "rois": str(out_dir / "kerning_rois.json")}


# ---------------------------------------------------------------------------
# fixture 2b: kerning（濁点様の微小ギャップ結合）
# 実データ（名刺タグライン）で「通常ギャップ20px前後に対し濁点ギャップが2〜3px」という
# ケースの結合漏れが見つかった回帰テスト用フィクスチャ。
# c2a・c2b の間だけ3pxの微小ギャップとし、結合されて1ブロブとして数えられることを検証する。
# ---------------------------------------------------------------------------

DAKUTEN_GLYPH_H = 60
DAKUTEN_MARGIN = 20
# (開始x, 終了x) のローカル座標（オフセット前）
DAKUTEN_GLYPHS_LOCAL = [
    (0, 25),      # c1
    (47, 67),     # c2a (base)      gap before = 22
    (70, 78),     # c2b (濁点相当)  gap before = 3  <- 微小、c2a と結合される
    (100, 125),   # c3              gap before = 22
    (147, 172),   # c4              gap before = 22
]
DAKUTEN_EXPECTED_GAPS = [22, 22, 22]  # 結合後（4ブロブ・3ギャップ、すべて均一）
DAKUTEN_ROI_LOOSE_BBOX_LOCAL = [-5, -5, 177, 65]


def make_kerning_dakuten_fixture(out_dir: Path) -> dict:
    m = DAKUTEN_MARGIN
    max_x1 = max(e for _, e in DAKUTEN_GLYPHS_LOCAL)
    w, h = max_x1 + 2 * m, DAKUTEN_GLYPH_H + 2 * m
    arr = _new_canvas(w, h, WHITE)
    for s, e in DAKUTEN_GLYPHS_LOCAL:
        _fill_rect(arr, s + m, m, e + m, DAKUTEN_GLYPH_H + m, BLACK)

    path = out_dir / "kerning_dakuten.png"
    _save(arr, path)

    loose = DAKUTEN_ROI_LOOSE_BBOX_LOCAL
    roi_bbox = [loose[0] + m, loose[1] + m, loose[2] + m, loose[3] + m]
    rois = [
        {
            "id": "dakuten_line",
            "label": "濁点結合検証文字列",
            "type": "text_line",
            "bbox": roi_bbox,
        }
    ]
    c.save_json(rois, out_dir / "kerning_dakuten_rois.json")
    return {"image": str(path), "rois": str(out_dir / "kerning_dakuten_rois.json")}


# ---------------------------------------------------------------------------
# fixture 3: size（視覚的大きさのばらつき）
# 側辺 40/42/38/60 の正方形（60 が明確な外れ値）
# ---------------------------------------------------------------------------

SIZE_SQUARES = [
    ("sq1", 40),
    ("sq2", 42),
    ("sq3", 38),
    ("sq4", 60),
]
SIZE_MARGIN = 20
SIZE_GAP = 20


def make_size_fixture(out_dir: Path) -> dict:
    m, gap = SIZE_MARGIN, SIZE_GAP
    max_h = max(side for _, side in SIZE_SQUARES)
    x_cursor = m
    positions = []
    for name, side in SIZE_SQUARES:
        x0, y0 = x_cursor, m
        x1, y1 = x0 + side, y0 + side
        positions.append((name, side, x0, y0, x1, y1))
        x_cursor = x1 + gap
    w = x_cursor - gap + m
    h = max_h + 2 * m

    arr = _new_canvas(w, h, WHITE)
    for _, _, x0, y0, x1, y1 in positions:
        _fill_rect(arr, x0, y0, x1, y1, BLACK)

    path = out_dir / "size_squares.png"
    _save(arr, path)

    rois = []
    for name, side, x0, y0, x1, y1 in positions:
        loose = [x0 - 5, y0 - 5, x1 + 5, y1 + 5]
        rois.append({"id": name, "label": f"サイズ検証要素 {name}", "type": "element",
                     "group": "size_demo", "bbox": loose})
    c.save_json(rois, out_dir / "size_rois.json")
    return {"image": str(path), "rois": str(out_dir / "size_rois.json")}


# ---------------------------------------------------------------------------
# fixture 4: color（ブランド6色 + ΔE≈5 ずらしスウォッチ）
# 各ブランド色の Lab 値を軸1本だけ ±5 シフトし、丸め後の実測 ΔE を expected.md に記載
# ---------------------------------------------------------------------------

# (name, brand_hex, shifted_rgb) -- shifted_rgb は Lab シフト後に sRGB へ丸めた値
COLOR_SWATCHES = [
    ("Brand1", "#3B82F6", (33, 118, 231)),
    ("Brand2", "#10B981", (39, 185, 120)),
    ("Brand3", "#F59E0B", (244, 158, 35)),
    ("Brand4", "#EC4899", (234, 73, 161)),
    ("Brand5", "#1E3A5F", (11, 59, 103)),
    ("Brand6", "#EDE4DA", (227, 231, 218)),
]
COLOR_PATCH = 60
COLOR_GAP = 12
COLOR_MARGIN = 20


def make_color_fixture(out_dir: Path) -> dict:
    m, gap, patch = COLOR_MARGIN, COLOR_GAP, COLOR_PATCH
    n = len(COLOR_SWATCHES)
    w = m * 2 + n * patch + (n - 1) * gap
    h = m * 2 + 2 * patch + gap

    arr = _new_canvas(w, h, WHITE)
    for i, (name, hexcode, shifted_rgb) in enumerate(COLOR_SWATCHES):
        x0 = m + i * (patch + gap)
        x1 = x0 + patch
        y0 = m
        y1 = y0 + patch
        _fill_rect(arr, x0, y0, x1, y1, c.hex_to_rgb(hexcode))
        y0b = y1 + gap
        y1b = y0b + patch
        _fill_rect(arr, x0, y0b, x1, y1b, shifted_rgb)

    path = out_dir / "color_swatches.png"
    _save(arr, path)
    return {"image": str(path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="design-check: 合成フィクスチャを生成する")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "gravity": make_gravity_fixture(out_dir),
        "kerning": make_kerning_fixture(out_dir),
        "kerning_dakuten": make_kerning_dakuten_fixture(out_dir),
        "size": make_size_fixture(out_dir),
        "color": make_color_fixture(out_dir),
    }
    c.print_json(manifest)


if __name__ == "__main__":
    main()
