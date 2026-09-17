---
name: design-check
description: 画像/PDFのデザインを重心・大きさ・カーニング・色の4観点で定量解析し、修正案シミュレーション付きのHTMLレポート（Artifact）とターミナル要約を出力するスキル
---

# /design-check

画像（PNG/JPG）または PDF を渡すと、**重心（光学センタリング）・大きさ・カーニング・色**の
4観点で「調整したほうがいい箇所」を定量データ付きで指摘し、修正案シミュレーション入りの
HTML レポートを Artifact として生成する。

決定論的な画素計算・計測・組版は Python スクリプト（`uv run` で実行）が担当し、
ROI の意味づけ・判断・文章化は Claude が担当する。API クレジットは使わない
（Claude Code サブスク内で完結）。

## 使い方

```
/design-check <画像またはPDFのパス>
/design-check <パス1> <パス2>   # 複数対象（フロー等）
```

## 動作原則

- 作業ディレクトリは **セッションの scratchpad 配下** `design-check-<対象名>/` を使う
  （スキルディレクトリ自体は汚さない）。以降の手順の `work/` はこのディレクトリを指す。
- 全スクリプトは `uv run <script>.py <サブコマンド> ...` で実行する（PEP 723 インライン依存で
  `pillow`/`numpy` を自動解決するため、事前の pip install は不要）。
- `analyze.py` / `simulate.py` の標準出力は **JSON のみ**。生成画像は `--out-dir` 配下に
  ファイルとして書き出され、JSON にはパスだけが載る。base64 や生画素をコンテキストに
  流し込まないこと。

## ワークフロー

### Step 1: 入力の準備（PDFの場合は変換）

画像ならそのまま Step 2 へ。PDF の場合は以下の手順で PNG 化する
（**72dpi 問題に注意**: PDF の埋め込み解像度によっては `sips` 変換で低解像度になることがある）。

```bash
sips -s format png <input.pdf> --out work/input.png
sips -g pixelWidth -g pixelHeight work/input.png
```

長辺が **1500px 未満**の場合は QuickLook レンダリングにフォールバックする
（ベクターPDFでも高解像度で再描画できる。実機検証済み）:

```bash
mkdir -p work/qlout
qlmanage -t -s 4800 -o work/qlout <input.pdf>
# 出力ファイル名は <元ファイル名>.png になる（例: card.pdf -> card.pdf.png）
```

PDF のポイント寸法（`MediaBox`）から px/mm 換算係数を求め、後続の `--px-per-mm` に渡すと
印刷物では Δpx に加えて Δmm も表示できる。

### Step 2: 目視 → グリッド画像で粗い ROI 座標を取る

```bash
uv run scripts/analyze.py grid --image work/input.png --out-dir work --spacing 100
```

生成された `work/grid.png` を Read で目視し、チェックしたい要素（タグライン・CTAボタン・
ロゴ等）のおおよその bbox 座標（グリッド線基準）を読み取る。この段階では厳密でなくてよい
（後続の `refine_bbox` が実際のインク範囲にタイト化してくれる）。

### Step 3: `rois.json` を記述する

Claude が目視した内容を意味ラベル付きで記述する。

```json
[
  {
    "id": "tagline",
    "label": "タグライン",
    "type": "text_line",
    "bbox": [120, 340, 480, 380],
    "relative_to": "card_frame"
  },
  {
    "id": "card_frame",
    "label": "名刺フレーム",
    "type": "container",
    "bbox": [0, 0, 1050, 600]
  },
  {
    "id": "icon_a",
    "label": "アイコンA",
    "type": "element",
    "group": "icon_row",
    "bbox": [80, 80, 140, 140]
  }
]
```

フィールド:
- `id`: 一意な識別子
- `label`: 日本語の意味ラベル（レポートに表示される）
- `type`: `text_line` | `element` | `container`
- `bbox`: `[x0, y0, x1, y1]`（目視のラフ座標でよい）
- `relative_to`（任意）: 重心を比較する親コンテナ ROI の `id`。指定しない場合は自身の
  タイト化後 bbox 中心と比較する。同じ `id` を自己参照させると「この要素が元々収まって
  いた枠」を固定参照点にできる（`simulate.py offset` の補正確認に有用）。
  参照先が `type: container` の場合はタイト化せず **raw bbox の中心＝紙面実寸の中心**を
  基準にする（`composition` と同一基準。gravity / simulate の再計測も共通）
- `group`（任意）: 大きさ比較をするグループ名

### Step 4: 解析を実行する

```bash
uv run scripts/analyze.py all --image work/input.png --rois work/rois.json --out-dir work \
  > work/analysis.json
```

個別に実行したい場合は `gravity` / `size` / `kerning` / `color` / `composition` サブコマンドも
使える（`--group` は `size` に、`--roi-id` は `gravity`/`kerning` に指定）。

`rois.json` に `type: container` の ROI が含まれていれば、`all` 実行時に自動で
`work/composition.png`（紙面全体の重心ズレビジュアル）も生成され、`analysis.json` の
`composition` キーに含まれる。単独で実行したい場合は:

```bash
uv run scripts/analyze.py composition --image work/input.png --rois work/rois.json \
  --out-dir work [--px-per-mm N]
```

`composition` は「紙面（最大の container ROI。無ければ画像全体）のどこがどれだけズレて
いるか」を1枚のビジュアルで示す機能。基準コンテナの幾何中心を通る垂直・水平の灰破線と、
各非container ROI（element/text_line）のタイト枠・光学重心の十字マーカー・基準線からの
水平オフセット矢印＋Δxラベル（例「タグライン Δx +1.1px (+0.02mm)」）、右下の凡例ブロック
（要素名・Δx/Δy・flagの●色）を1枚のPNGに描き込む。文字ラベルは macOS のヒラギノ
（`/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc` 等）で描画し、フォントが見つからない
環境では自動的に英数字の `id` にフォールバックする（`common.py` の `load_label_font`）。

> **注意**: `composition` は rois.json 内の**すべての非container ROI**を表示対象にする。
> 同じ物理要素を複数の `relative_to` バリエーションで用意している場合（例:
> 「タグライン（card基準）」「タグライン（logo基準）」「タグライン（自枠基準）」のように
> bbox が同一のROIを目的別に複数定義しているケース）、composition ではそれらが完全に
> 重なって表示され読みにくくなる。読みやすい紙面全体ビューが欲しい場合は、composition
> 専用に「物理的に異なる要素だけ」を含む rois.json（または一時的にROIを絞った複製）を
> 用意することを推奨する。

生成された `work/analysis.json` を Read で読み、閾値表（下記）と照らして課題を判断する。

### Step 5: 修正案をシミュレーションする

重心ズレなど、平坦背景の要素であれば移動・字送り・拡縮の効果を再計測できる。

```bash
uv run scripts/simulate.py offset --image work/input.png --rois work/rois.json \
  --roi-id tagline --out-dir work
uv run scripts/simulate.py tracking --image work/input.png --rois work/rois.json \
  --roi-id tagline --out-dir work
uv run scripts/simulate.py scale --image work/input.png --rois work/rois.json \
  --roi-id icon_a --target-diameter 90 --out-dir work
```

複雑な背景（写真等）では `"simulation": "unavailable"` が返るので、その場合は
オーバーレイ画像と数値提案のみでレポートする。

### Step 6: `report_spec.json` を記述する

`analysis.json` / `simulate.py` の出力をもとに、カテゴリごとのスコア・課題・改善提案を
Claude が文章化する。スキーマ:

```json
{
  "title": "デザインチェックレポート: <対象名>",
  "target": "<対象の説明>",
  "overall_score": 3.0,
  "overall_summary": "...",
  "categories": [
    {
      "name": "重心（光学センタリング）",
      "score": 2,
      "priority": "高",
      "issues": ["..."],
      "suggestions": ["○○を△△pxに変更してください"],
      "assets": [{"path": "work/gravity_tagline.png", "caption": "..."}],
      "charts": [{"type": "bar", "data": {"title": "Δpx", "items": [...]}}],
      "table": {"headers": [...], "rows": [[...]]}
    }
  ],
  "comparison": {
    "caption": "案A: 重心ズレ 46px → 2px に改善",
    "image": "work/offset_tagline_compare.png",
    "charts": [{"type": "comparison_bar", "data": {...}}]
  }
}
```

`charts[].type` は `bar` / `comparison_bar` / `projection_profile` / `color_swatches` を使える
（`scripts/svg_charts.py` 参照）。

**レポート構成ガイド**: `composition.png` が生成されている場合、① 重心カテゴリの
`assets` 冒頭に配置することを推奨する（紙面全体でのズレの全体像を先に見せてから、
個別要素のオーバーレイ画像で詳細を示す構成にする）。**composition を載せた場合でも、
主要要素のクローズアップ（`gravity_<id>.png`）は省略せず併載すること**——全体ビューは
位置関係、クローズアップは要素内のインク偏りを示すもので役割が異なる（ユーザー
フィードバック 2026-07-03 由来）。指摘ゼロのカテゴリでも生成画像があれば載せてよい。

```json
"assets": [
  {"path": "work/composition.png", "caption": "紙面全体の重心ズレ（基準線からのΔx表示）"},
  {"path": "work/gravity_tagline.png", "caption": "タグライン単体のオーバーレイ"}
]
```

### Step 7: レポートを組版する

```bash
uv run scripts/build_report.py --spec work/report_spec.json --out work/report.html
```

非0で終了した場合は `report_spec.json` に外部URLやNGタグが混入している。エラーメッセージを
見て修正すること。

### Step 8: Artifact として公開し、ターミナルに日本語要約を出す

`work/report.html` の中身（`<title>`〜`</script>`まで）を Artifact として公開する。
ターミナルには以下のようなフォーマットで要約する。

```
## デザインチェック: <対象名>
### 総合スコア: X/5 — [評価ラベル]
[全体サマリー 2〜3文]

🔴 重心（光学センタリング） — 2/5
- タグラインの光学重心が枠の中心から右に9.8px、上に6.3pxずれています
- 全体を左に10px、下に6pxへ移動すると重心ズレが0.3px程度まで改善します

🟡 カーニング — 3/5
...

🟢 色 — 4/5
...

→ 詳細レポート（オーバーレイ画像・グラフ・修正案比較）は Artifact を参照してください
```

## 閾値表

| 観点 | 指標 | 🟢 良好 | 🟡 要改善 | 🔴 問題あり |
|---|---|---|---|---|
| ① 重心 | 要素幅に対する Δpx の比率 | < 0.5% | 0.5–1.5% | > 1.5% |
| ② 大きさ | グループ内 visual_diameter の CV(%) | < 3% | 3–8% | > 8% |
| ③ カーニング | 中央値ギャップからの偏差(%) | < 10% | 10–25% | > 25% |
| ④ 色 | ブランド6色・システムカラー12色とのΔE76（統合候補/ズレ疑いの判定レンジ） | ΔE < 2.3（同一とみなす） | 2.3 ≤ ΔE ≤ 10（ズレ疑い） | ΔE > 10（別色） |
| ④ 色（コントラスト） | WCAGコントラスト比 | ≥ 7:1 (AAA) | ≥ 4.5:1 (AA) | < 4.5:1 (fail) |

`composition` の各要素 flag は ① 重心と同じ閾値（基準コンテナ幅に対する Δpx の比率）で判定する。

スコア(1-5)とスコア絵文字の対応（figma-ia-checker と同じ規約）:
- 🟢 5〜4: 問題なし〜軽微
- 🟡 3: 要改善
- 🔴 2〜1: 問題あり〜重大

### 照合するパレット（★利用者が自分のブランド色に差し替える）

スクリプト側の定数は `scripts/common.py`。**下の値は中立なプレースホルダで、どこかの組織の色ではない。**
自分のブランドガイドを正本にして差し替える。

**ブランドカラー6色** — 印刷にも使う色（CMYK / 特色の規定があるもの）を想定した枠。

| 名前 | HEX |
|---|---|
| Brand1 | `#3B82F6` |
| Brand2 | `#10B981` |
| Brand3 | `#F59E0B` |
| Brand4 | `#EC4899` |
| Brand5 | `#1E3A5F` |
| Brand6 | `#EDE4DA` |

**システムカラー12色** — アプリ内のカテゴリ分けやグラフに部分的に用いる補助色の枠。

| 名前 | HEX |  | 名前 | HEX |
|---|---|---|---|---|
| Red | `#EF4444` |  | Green | `#22C55E` |
| Purple | `#A855F7` |  | Orchid | `#D946EF` |
| Orange | `#F97316` |  | Indigo | `#6366F1` |
| Lime | `#84CC16` |  | Grass | `#4ADE80` |
| Aqua | `#22D3EE` |  | Gold | `#CA8A04` |
| Brown | `#A16207` |  | Gray | `#9CA3AF` |

> ⚠️ **システムカラーは CMYK / DIC が規定されていない＝RGB のみ。**
> **印刷物・特色印刷の解析でシステムカラーに一致したら、それは指摘対象**（そのままでは入稿できない）。
> ブランドカラーと重複する色がガイドにある場合は、重複を避けて補助色だけをここに持たせる。

**レポートへの書き分け**: `analyze.py` の `brand_color_flags` は各一致に `category`（`brand` / `system`）と
`print_ok`（システムカラーは `false`）を持つ。**「ブランド外の色」と一括で書かず、
ブランドカラー一致 / システムカラー一致 / どちらでもない、の3つに分けて書く。**

## レポート文体の規約

figma-ia-checker を踏襲する:
- 提案は「改善してください」ではなく「○○を△△に変更してください」のように具体的に記述する
  （例:「タグラインを左に10px、下に6px移動してください」）
- 課題がないカテゴリも省略せずすべて出力する
- 問題の重大度に応じて優先度（高/中/低）を付ける

## ファイル構成

```
scripts/
  common.py       # 色空間変換・WCAG・インクマップ・ROI操作・オーバーレイ描画・ブランド6色/システムカラー12色定数
  analyze.py       # CLI: grid / gravity / size / kerning / color / composition / all
  simulate.py      # CLI: offset / tracking / scale（再計測付き）
  build_report.py  # report_spec.json -> 自己完結HTML
  svg_charts.py    # インラインSVGグラフ生成（build_report.py から import）
templates/
  report_template.html  # 自己完結HTML（light/dark両テーマ対応）
tests/
  make_fixtures.py  # 理論値が既知の合成画像フィクスチャ生成
  expected.md        # 各フィクスチャの理論値と許容誤差
```

## 注意事項

- 入力は画像（PNG/JPG）または PDF のみ。Figma URL は対象外。
- 生成 HTML は `<!doctype>`/`<html>`/`<head>`/`<body>` を含まない（Artifact 側が骨格を
  提供するため、ページ内容のみを書く）。外部URL（`http://`/`https://`）はゼロにする
  （`build_report.py` が自己検査して違反時は非0終了する）。
- 画像アセットは長辺1200pxに縮小してから data URI 埋め込みする（Artifact肥大化対策）。
- 複雑な背景（写真等）では `simulate.py` が `"simulation": "unavailable"` を返すことがある。
  その場合はオーバーレイ画像 + 数値提案のみでレポートし、無理にシミュレーションしない。
- **カラーパネル上のテキスト**（色ベタの箱の中の文字など）は、全体画像のままだとパネル自体が
  インク扱いになり解析できない。パネルの**内側だけ**を crop した画像を作って再実行すると、
  背景推定がパネル色になり文字が解析できる（crop にパネル外の色が1pxでも入ると失敗する）。
- **縦組みテキスト**は字間解析はできないが、テキストブロック全体を text_line として渡すと
  垂直射影が「列」を検出するため、**列送り（行間）の均一性計測**に転用できる。
  罫線・装飾線は細いセグメントとして混入するので結果から目視で除外すること。
- **写真背景上のテキストは字間解析が不成立**（確定的な限界・2026-07-03 実測）。
  グラデーションやJPEGノイズが垂直射影の谷を埋めるため、局所クロップでも文字分割
  できない。gravity / composition / コントラスト（ストローク画素の直接抽出）は写真背景でも
  機能する。将来対応の候補は適応的二値化（Sauvola等）の前処理。
