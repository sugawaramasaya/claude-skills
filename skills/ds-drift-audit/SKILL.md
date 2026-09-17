---
name: ds-drift-audit
description: claude-designバンドル（コード側トークンコピー）とFigma「Design System」のライブ・プリミティブ色を突合し、値ドリフト・命名差・写経ドリフト（[Figma確定]なのに非在）を検出してHTML/JSONレポートを出すスキル。
---

# ds-drift-audit — DS ドリフト監査（プリミティブ照合）

`<自分のリポジトリ>` の `docs/design/claude-design/`（claude.ai/design 取込用バンドル＝Figma からの写経コピー）の
色トークンが、Figma「Design System」のライブ・プリミティブ色と一致するかを照合し、乖離を可視化する。

## 前提・制約（重要）
- Figma **pro tier** の場合、Variables の REST 読み書きは Enterprise 限定 → **使えない**。
- MCP `get_variable_defs` は**ノード単位**で、変数を fill にバインドしたノードしか値を返さない。
- セマンティック変数（`ds/semantic/*`）の解決値は**吸えない**（swatch がバインドしていない／M3表は別物）。
- そのため本監査は **「バンドル色値がライブ・プリミティブ（`ds/primitive/color/scale/*` ※原文タイポ）に存在するか」** に限定。
  セマンティックの再エイリアス検出は対象外。**dark プリミティブは別モード系統で未取得** → dark は「未照合」（今回スコープ外）。
- 「プリミティブ非在」は**必ずしもドリフトではない**：hover/surface のティントやアルファ合成など**意図的な派生値**は元々プリミティブと一致しない。
  最も強い所見は **`[Figma確定]` タグ付きなのに非在**（＝重大アラート）。

## データの取り方（オーケストレータ＝メインが実施。サブエージェントは Figma MCP 不可）
1. DSライブラリ特定: `get_libraries(fileKey=<DSを使っているファイル>)` → "Design System" の libraryKey。
2. DSファイル: `YOUR_DS_FILE_KEY`（page 0:1 "Color"）。
3. **ライブ・プリミティブ取得**: `get_variable_defs(fileKey=YOUR_DS_FILE_KEY, nodeId=YOUR_NODE_ID)`
   → `{ "ds/primitive/color/scale/<Scale>/<n>": "#rrggbb(aa)" }` 674件（lightモード）。
   これを `figma_primitives.light.json` として保存する（**同梱していない。自分の DS から取る**）。
   同梱の `figma_primitives.sample.json` は**キーの形と値の型を示すためだけの中立サンプル**で、
   実在する DS の色ではない。これを指して走らせても意味のある結果は出ない。
4. バンドル取得: `gh api repos/<your-org>/<your-repo>/contents/docs/design/claude-design/{colors_and_type.css,_ds_manifest.json} --jq '.content' | base64 -d` を `<bundle-dir>` へ。

## 実行
```
python3 audit.py \
  --bundle-dir <bundle-dir> \
  --figma-primitives ./figma_primitives.light.json \   # ← 自分で取得したもの
  [--figma-primitives-dark <dark.json>] \
  --out-dir ~/dev/digests
```
出力: `ds-drift-report.html`（自己完結・light/dark対応・Artifact可）/ `ds-drift-report.json` / ターミナル要約。

## 分類
- 総合（light基準）: `プリミティブ一致` / `プリミティブ非在` / `対象外(非色)`
- dark: `一致`/`非在`（dark JSON提供時）または `未照合(ダークprimitive未取得)`
- `命名差`: 値一致だがスケール名相違（**バンドル側の命名 ScaleA/ScaleB ⇄ Figma/Radix Blue/Teal**）
- 重大アラート: `[Figma確定]` かつ light 非在

## 完全な監査に近づけるには（オプション）
セマンティック値・dark値・エイリアスまで正確に取るには、Figma の **Variables エクスポート系プラグイン**（Design Tokens 等）で
DSファイルの全変数を JSON 化してもらい、それを入力にすればよい（pro tier でも可）。本スキルはその JSON を受け取れるよう拡張余地を残している。

関連メモリ: 自分の DS のメモ（バンドルの場所・トークン実値・Figma適用Tips）を書いておくと引きやすい
