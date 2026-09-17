---
name: council-figma
description: Figmaコメントを「Slack DM通知 + Figma REST API」の二経路で収集するサブスキル（メンション未着・集中着信の取りこぼしを防止）。Figma MCPでデザインコンテキストを補足し、ADとして返信ドラフトを生成する。councilから呼ばれる。引数で日数指定可能（例：/council-figma 3d）。
---

# ad-figma — Figmaコメント収集サブスキル

Figma コメントを **2経路** で収集し、取りこぼしを防ぐ：
1. **Slack DM 通知**（チャンネル `D_FIGMA_DM`）— 自分宛メンション／参加スレッドの通知
2. **Figma REST API**（`/v1/files/<key>/comments`）— 通知の有無に依存しないファイル単位の全コメント

Figma MCP ツールでデザインコンテキストを補足し、AD視点の返信ドラフトを生成する。

`council` から呼ばれる場合は生データ＋初期分析を返す。単体実行時は完全な分析と返信ドラフトを表示する。

> **役割の境界**: このスキルは**日々のメンション巡回**（直近 N 日・自分宛中心）が担当。
> 「コメントが溜まったファイルを画面ごとに棚卸ししたい」「どの画面の話か分からない」場合は
> `/figma-comment-map <FigmaのURL>` を使う。全コメントを画面のスクショにピンごと復元した
> HTML/PDF を出すので、こちらでは代替しない。

## 実行手順

### Step 1: 日数の決定

引数が指定されている場合（`3d`, `7d`, `14d` など）はその日数を使用する。
指定がなければデフォルトは **7日**。

今日の日付をもとに対象期間の開始 Unix タイムスタンプ（秒）を算出する。

### Step 2: Figma DM 通知の取得（一次ソース）

`slack_read_channel` を使用:
- `channel_id`: `D_FIGMA_DM`（Figma アプリの Slack DM チャンネル）
- `limit`: `100`

取得後、メッセージの `ts`（Unix タイムスタンプ）が対象期間内のものに絞り込む。

> ⚠️ **1ページで対象期間をカバーできない場合**（最古メッセージの `ts` がまだ対象期間内 = 取りこぼしの恐れ）は、レスポンスの `cursor` で次ページを取得し、対象期間の最古メッセージに到達するまで繰り返す。短時間に多数のコメントが集中して届くと `limit` を超えて埋没するため必須。
>
> ⚠️ **Slack DM の構造的限界**: Figma の Slack 通知は「自分がメンション対象／参加スレッド」のコメントしか届かない。CC 外・別スレッドのコメントは DM に来ない。そのため次の Step 2.5（REST API）で必ず全件補完する。
>
> 実測例（2026-08-03）: あるファイルで実装担当者が 45分間に 19件を連投したが `@自分` が無かったため DM は 0件。翌日の返信（＝参加スレッド扱い）だけが届いた。**通知が来た数件から全体像を推測できてしまう**のが最も危ない挙動。
>
> ✅ **恒久対策**: DM ではなく Slack チャンネルに file を購読させれば、メンション無しのコメントも流れる（対象チャンネルで `/figma subscribe` → **file** を選択 → 頻度は **real time**。10分以内のコメントは1メッセージにまとめられるためバーストでも埋まらない）。file 購読の対象は **Comments, replies, @mentions**。重要ファイルは購読を入れ、DM は補助として扱う。全件突合が必要なときは `/figma-comment-map`。

### Step 2.5: Figma REST API で全コメントを補完（取りこぼし防止の要）⚠️ 必須

Slack 通知の有無に依存せず、対象 Figma ファイルの**全コメントを直接取得**してマージする。

**1. 対象ファイルキーの決定**
- 既定のウォッチ対象（常に取得）:
  - Project Alpha = `YOUR_FIGMA_FILE_KEY`
- Step 2/3 で得た通知本文・コメント内に**生 URL**（`figma.com/design/<KEY>/…` または `figma.com/file/<KEY>/…`）があれば、その `<KEY>` も対象に追加（重複排除）。
- ※ 通知 DM の `figma.com/email/link_redirect?link_uuid=…` 形式はファイルキーが隠蔽されているため抽出不可。生 URL か上記ウォッチ対象リストで補う。

**2. トークン取得**（Bash）
```bash
FIGMA_TOKEN=$(security find-generic-password -s "FIGMA_TOKEN" -w)
```

**3. 各ファイルキーで全コメント取得**（Bash・ファイルごとに並行可）
```bash
curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
  "https://api.figma.com/v1/files/<FILE_KEY>/comments"
```
レスポンス `comments[]` の各要素: `id` / `message` / `user.handle` / `created_at`(ISO8601) / `resolved_at` / `parent_id` / `client_meta` / `order_id`。
- 401/404/トークン取得失敗時は **graceful skip** し、Slack DM の結果のみで続行（後段の出力に「REST API 取得失敗・Slack DM のみ」と明記する）。

**4. フィルタ**
- `created_at` が対象期間内のもののみ。
- 自分自身（me / me）のコメントは除外。
- `resolved_at` が設定済み（解決済み）のコメントは ★ 相当に格下げ（参考情報）。

**5. Slack DM 結果とのマージ・重複排除**
- 同一コメント判定キー: `commenter handle` ＋ `message 先頭40文字` ＋ `created_at/ts が60秒以内`。
- **REST API 由来を正**とし、Slack DM のみに存在するものは追加。両方にあるものは REST のメタ（`comment id`・`parent_id`・`resolved` 状態）を優先。
- 各 item に取得経路 `via`（`"rest"` / `"slack_dm"` / `"both"`）を付与し、可視化する。

### Step 3: コメント通知のパース

各メッセージから以下を抽出する。

**典型的なFigma通知フォーマット**:
```
[コメント者名] commented on [ファイル名]:
"[コメント本文]"
[Figma URL]
```

または:
```
[コメント者名] replied to a comment in [ファイル名]:
"[コメント本文]"
```

抽出項目:
- コメント者名
- ファイル名 / フレーム名
- コメント本文
- Figma URL（`figma.com/file/` または `figma.com/design/` を含むURL）

**除外するもの**:
- 自分自身（me / me）がコメントした通知
- Figma URL が含まれていないメッセージ

### Step 4: Figmaデザインコンテキストの取得（並行実行）

抽出したFigma URLすべてに対して `mcp__figma__get_metadata` を**全件並行実行**:
- `file_url`: 抽出したFigma URL

エラー時は graceful skip（URLのみ保持してメタデータなしで処理）。

取得成功時は以下を保持:
- ファイル名・ページ名・フレーム名
- 最終更新者・更新日時

### Step 5: 各コメントの分析

**指摘種別の判定**:
- `BUG_REPORT`: 「おかしい」「違う」「ずれている」「仕様と異なる」等の誤り指摘
- `FEEDBACK`: デザインに対する意見・改善案の提案
- `QUESTION`: 確認・質問（「〜はどうなりますか？」等）
- `DIRECTION_REQUEST`: 方向性の選択・決定を求めるもの
- `APPROVAL_REQUEST`: 承認・確認を求めるもの
- `INFO`: 通知のみ（自分へのメンションなし）

**優先度の判断**:
- ★★★: `BUG_REPORT` または `APPROVAL_REQUEST` で締め切り言及あり
- ★★: `FEEDBACK`, `QUESTION`, `DIRECTION_REQUEST`
- ★: `INFO`

**価値仮説の推定**（`INFO` 以外のコメントのみ）:
コメント本文・Figmaメタデータをもとに以下を各1文で推定する。
- **欲求**: コメント者が本当に達成・解決したいこと（HOW／手段は入れない）
- **課題**: 欲求を満たすのを妨げている状況の制約（「〜ないため」の形）

### Step 6: スクリーンショット取得（★★★のみ）

★★★ と判定されたコメントに対してのみ `mcp__figma__get_screenshot` を実行:
- `file_url`: Figma URL

エラー時はスキップ（ビジュアルなしで返信ドラフト生成）。

---

## councilから呼ばれた場合の出力

以下の形式のデータを返す:

```
[
  {
    "source": "figma",
    "via": "rest|slack_dm|both",
    "comment_id": "<REST API のコメントID（REST由来時）。slack_dmのみは null>",
    "ts": "<メッセージのUnixタイムスタンプ（REST由来は created_at を Unix秒換算）>",
    "commenter": "<コメント者名>",
    "file_name": "<Figmaファイル名>",
    "frame_name": "<フレーム名>",
    "comment_text": "<コメント本文>",
    "figma_url": "<FigmaのURL>",
    "figma_metadata": { ... },
    "resolved": false,
    "type": "<指摘種別>",
    "priority": "★★★" or "★★" or "★"
  },
  ...
]
```

---

## 単体実行時の完全な分析と出力

`/council-figma Nd` で単体実行した場合は、収集・分析後に以下を表示する。

### アートディレクターとしての返信ドラフト生成

**生成原則**:
- Figmaのコメント欄に返信することを想定したトーン
- 簡潔・決断的（長文禁止）
- デザインコンテキスト（メタデータ）を参照した具体的な返信
- コンポーネント名・デザイントークン名を具体的に使用
- グラフィック品質基準（タイポ・配色・レイアウト・グラフィック要素）を明確に

**指摘種別別のドラフトトーン**:
- `BUG_REPORT`: 「確認しました。〜は〜に修正します」または「仕様通りです。理由：〜」
- `FEEDBACK`: フィードバックへの反応 + 採用/保留の判断と理由
- `QUESTION`: 明確な回答（「〜です」「〜については〜のため、〜の仕様です」）
- `DIRECTION_REQUEST`: 推奨案の提示と理由（「A案を推奨します。理由：〜」）
- `APPROVAL_REQUEST`: 「✅ 承認します」または「修正点があります：〜」

### 単体実行時の出力フォーマット

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎨 Figma コメント通知（過去 N 日間: X 件）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[1] ★★★ BUG_REPORT | 2026-05-18 17:23 | @member-a
ファイル: Project Alpha / ログイン画面
コメント: 「Screen A のボタンの遷移先がリストページになっており挙動がおかしい」
欲求推定: 実装が仕様通りであることを確認して安心してリリースを進めたい
課題推定: FigmaのプロトタイプとDev仕様の齟齬を自分では確認できないため
🔗 https://figma.com/design/...

💬 ADとしての返信ドラフト（Figmaコメント用）:
─────────────────────────
確認しました。遷移先は仕様上「詳細編集ページ」の想定です。
Figmaでは暫定的にリストページと繋いでいましたが、実装では正しい遷移先に修正済みです。
Figmaも更新します。
─────────────────────────

[2] ★★ FEEDBACK | 2026-05-17 14:00 | @member-b
ファイル: デザインシステム / Button コンポーネント
コメント: 「Secondary ボタンのhover色がブランドカラーと違うように見える」
🔗 https://figma.com/design/...

💬 ADとしての返信ドラフト（Figmaコメント用）:
─────────────────────────
ご指摘ありがとうございます。
現状 `color.secondary.600` を使用していますが、正しくは `color.secondary.700` です。
デザイントークンを更新します。
─────────────────────────

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
合計 アクション必要: X 件 / 情報共有のみ: Y 件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

0件の場合: 「過去 N 日間に Figma コメント通知はありませんでした。」と表示。
