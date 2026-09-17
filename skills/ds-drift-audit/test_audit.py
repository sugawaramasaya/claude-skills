#!/usr/bin/env python3
"""
ds-drift-audit（プリミティブ照合監査）の自動テスト。

- 合成 figma_primitives.json フィクスチャを自作し、既知ケース
  （プリミティブ一致/非在/命名差/[Figma確定]なのに非在→重大アラート/
   alpha付き8桁hex一致/非色トークンの対象外分類/パース不能プリミティブ）を検証する。
- バンドルのトークンを取りこぼしなくパースできることを検証する（件数 214 は元の環境の値。
  自分のバンドルの件数に書き換えて使う）。
- audit.py を実行し、JSON/HTMLが生成されターミナル要約が出ることを確認する。

注意: このフィクスチャは完全に合成データであり、実際の Figma ライブ・プリミティブ
（fileKey YOUR_DS_FILE_KEY / nodeId YOUR_NODE_ID から取得予定）ではない。
MCPツール（mcp__figma__get_variable_defs）がこのテスト実行環境から到達不能なため、
本テストはロジック検証のみを目的とする。実データでの最終実行は別途必要。
"""

import json
import subprocess
import sys
import os
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
AUDIT_PY = SKILL_DIR / "audit.py"

SCRATCHPAD = Path(
    os.environ.get("DS_DRIFT_SCRATCHPAD")
    or (Path(tempfile.gettempdir()) / "ds-drift-audit")
)
BUNDLE_DIR = SCRATCHPAD / "bundle"
FIGMA_PRIMITIVES_PATH = SCRATCHPAD / "figma_primitives.json"
OUT_DIR = SCRATCHPAD / "ds-drift-audit-test-out"

sys.path.insert(0, str(SKILL_DIR))
import audit  # noqa: E402


def build_fixture():
    """
    既知ケースを仕込んだ合成 figma_primitives.json ({name: hex値}) を構築する。

    ケース設計:
      1. --ds-background-primary (light/dark共に #3b82f6, [Figma確定], hint=ScaleA9)
         -> primitive 'ds/primitive/color/scale/Blue/9' = '#3b82f6' を用意
         => プリミティブ一致 + 命名差(ScaleA⇄Blue)
      2. --ds-background-primary-hover (light #3578e5 / dark #4a8bfa, [Figma確定][導出])
         -> 対応する値をどのprimitiveにも含めない
         => プリミティブ非在 + [Figma確定]タグ付き => 重大アラート
      3. --ds-background-success (light/dark共に #10b981, [Figma確定], hint=ScaleB9)
         -> primitive 'ds/primitive/color/scale/Teal/9' = '#10b981'
         => プリミティブ一致 + 命名差(ScaleB⇄Teal) ★コーディネータ指定の確認ケース
      4. --ds-effect-focus-ring (light rgba(59,130,246,0.6) / dark rgba(96,165,250,0.7255),
         hint=ScaleAAlpha8) -> alpha付き8桁hex一致テスト。
         light正規化: #3b82f699 / dark正規化: #60a5fab9 をそれぞれ用意
         => プリミティブ一致（rgba→8桁hex正規化の検証）+ 命名差(ScaleAAlpha⇄Blue Alpha)
      5. --ds-space-1 (4px, 非色) => 対象外(非色) として明示される
      6. パース不能なprimitive値 'not-a-color' => unparsedとして記録される
    """
    primitives = {
        "ds/primitive/color/scale/Blue/9": "#3b82f6",
        "ds/primitive/color/scale/Teal/9": "#10b981",
        "ds/primitive/color/scale/Blue Alpha/8": "#3b82f699",
        "ds/primitive/color/scale/Blue Alpha/8-dark": "#60a5fab9",
        "ds/primitive/color/scale/Gray/1": "#f5f5f5",
        "ds/primitive/color/scale/Gray/2": "#eeeeee",
        "ds/primitive/color/scale/Red/9": "#ef4444",
        "ds/primitive/color/scale/Unknown/1": "not-a-color",
    }
    return primitives


def write_fixture():
    fixture = build_fixture()
    FIGMA_PRIMITIVES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIGMA_PRIMITIVES_PATH.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return fixture


def test_normalize_hex8():
    assert audit.normalize_hex8("#3b82f6") == "#3b82f6ff"
    assert audit.normalize_hex8("#0af") == "#00aaffff"
    assert audit.normalize_hex8("#3b82f699") == "#3b82f699"
    assert audit.normalize_hex8("rgba(59, 130, 246, 0.6)") == "#3b82f699"
    assert audit.normalize_hex8("rgb(59, 130, 246)") == "#3b82f6ff"
    assert audit.normalize_hex8("not-a-color") is None
    print("[OK] normalize_hex8: hex6/hex3/hex8/rgba/rgbの正規化と丸め誤差処理")


def test_parsed_count_214():
    records = audit.build_bundle_records(BUNDLE_DIR)
    assert len(records) == 214, f"expected 214, got {len(records)}"
    print("[OK] parsed count == 214")


def test_known_cases(report):
    tokens_by_name = {t["name"]: t for t in report["tokens"]}

    # ケース1: プリミティブ一致 + 命名差 (ScaleA⇄Blue)
    t1 = tokens_by_name["--ds-background-primary"]
    assert t1["primitive_status"] == "プリミティブ一致", t1["primitive_status"]
    assert "ds/primitive/color/scale/Blue/9" in t1["matched_primitive_names"]
    assert t1["naming_diff"] is not None
    assert t1["naming_diff"]["bundle_scale"] == "ScaleA"
    assert t1["naming_diff"]["figma_scale"] == "Blue"
    print("[OK] --ds-background-primary => プリミティブ一致 / 命名差(ScaleA⇄Blue)")

    # ケース2: プリミティブ非在 + [Figma確定] => 重大アラート
    t2 = tokens_by_name["--ds-background-primary-hover"]
    assert t2["primitive_status"] == "プリミティブ非在", t2["primitive_status"]
    assert any("Figma確定" in tag for tag in t2["source_tags"])
    critical_tokens = {
        a["token"] for a in report["consistency_alerts"] if a["severity"] == "重大"
    }
    assert "--ds-background-primary-hover" in critical_tokens, critical_tokens
    print("[OK] --ds-background-primary-hover => プリミティブ非在 / 重大アラート")

    # ケース3: success の ScaleB #10b981 ★コーディネータ指定確認ケース
    t3 = tokens_by_name["--ds-background-success"]
    assert t3["value_light"] == "#10b981"
    assert t3["primitive_status"] == "プリミティブ一致", t3["primitive_status"]
    assert "ds/primitive/color/scale/Teal/9" in t3["matched_primitive_names"]
    assert t3["naming_diff"] is not None
    assert t3["naming_diff"]["bundle_scale"] == "ScaleB"
    assert t3["naming_diff"]["figma_scale"] == "Teal"
    print("[OK] --ds-background-success(#10b981) => プリミティブ一致 / 命名差(ScaleB⇄Teal)")

    # ケース4: alpha付きrgba→8桁hex正規化での一致
    t4 = tokens_by_name["--ds-effect-focus-ring"]
    assert t4["primitive_status"] == "プリミティブ一致", t4["primitive_status"]
    print("[OK] --ds-effect-focus-ring => rgba⇄8桁hex正規化でプリミティブ一致")

    # ケース5: 非色トークンは対象外(非色)
    t5 = tokens_by_name["--ds-space-1"]
    assert t5["primitive_status"] == "対象外(非色)", t5["primitive_status"]
    print("[OK] --ds-space-1 => 対象外(非色)")


def test_unparsed_primitives(report):
    meta = report["figma_primitives_meta"]
    assert "ds/primitive/color/scale/Unknown/1" in meta["unparsed"]
    assert meta["total_count"] == 8
    assert meta["parsed_count"] == 7
    print("[OK] パース不能プリミティブ(Unknown/1)がunparsedに記録される")


def test_html_output(html_path: Path):
    html_text = html_path.read_text(encoding="utf-8")
    assert "@media (prefers-color-scheme: dark)" in html_text
    assert ':root[data-theme="dark"]' in html_text
    assert ':root[data-theme="light"]' in html_text
    assert "overflow-x: auto" in html_text
    assert "overflow-x: hidden" in html_text
    assert "整合性アラート" in html_text
    assert "命名差" in html_text
    assert "前提" in html_text  # Don't6対応の premise box
    assert "pro tier" in html_text
    print("[OK] HTML: light/dark両対応CSS・overflow封じ込め・前提明記・必須セクションを確認")


def test_cli_execution():
    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_PY),
            "--bundle-dir",
            str(BUNDLE_DIR),
            "--figma-primitives",
            str(FIGMA_PRIMITIVES_PATH),
            "--out-dir",
            str(OUT_DIR),
        ],
        capture_output=True,
        text=True,
    )
    print("--- CLI stdout ---")
    print(result.stdout)
    if result.returncode != 0:
        print("--- CLI stderr ---")
        print(result.stderr)
    assert result.returncode == 0, "audit.py CLI実行が失敗"
    assert "parsed count: 214" in result.stdout
    assert (OUT_DIR / "ds-drift-report.json").exists()
    assert (OUT_DIR / "ds-drift-report.html").exists()
    print("[OK] CLI実行成功・JSON/HTML生成・stdout要約確認")


def main():
    write_fixture()

    test_normalize_hex8()

    # 以降のテストは、照合対象のバンドル（claude-design の colors_and_type.css と
    # _ds_manifest.json）が BUNDLE_DIR に置かれていることが前提。
    # これは各自の環境から取得するもので、このリポジトリには同梱していない。
    # 取得手順は SKILL.md の「データの取り方」を参照。
    if not (BUNDLE_DIR / audit.CSS_FILENAME).exists():
        print(
            f"\n[SKIP] バンドル未配置のため以降のテストを飛ばしました。\n"
            f"        {BUNDLE_DIR}/{audit.CSS_FILENAME} を置くと全テストが走ります。\n"
            f"        （配置先は環境変数 DS_DRIFT_SCRATCHPAD で変更できます）"
        )
        print("\nPASSED (バンドル非依存のテストのみ)")
        return

    test_parsed_count_214()

    report, json_path, html_path = audit.run(BUNDLE_DIR, FIGMA_PRIMITIVES_PATH, OUT_DIR)
    test_known_cases(report)
    test_unparsed_primitives(report)
    test_html_output(html_path)

    test_cli_execution()

    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
