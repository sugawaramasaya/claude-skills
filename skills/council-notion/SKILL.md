---
name: council-notion
description: Notionのメンション・コメントを収集するサブスキル（Slack通知経由 + Notion直接検索の両方）。councilから呼ばれる。単体実行時はAD視点での分析と返信ドラフトも生成する。引数で日数指定可能（例：/council-notion 3d）。
---

# ad-notion — Notionメンション収集サブスキル

Notionのメンション・コメントを2ルートで収集する:
1. **Slack経由**: Notion bot からのSlack通知（`@Notion` ボット）
2. **Notion直接**: `notion-search` + `notion-get-comments` でメンション検索

`council` から呼ばれる場合は生データを返す。単体実行時はAD視点の分析と返信ドラフトも生成する。

## 実行手順

### Step 0: meのSlack IDを取得

`slack_search_users` で検索する:
- query: `me`

結果から `you@example.com` に対応するユーザーのID（`U` から始まる文字列）を取得し、`SELF_ID` として以降のSlack検索クエリで使用する。

取得できない場合は `U_SELF` をフォールバックとして使用する。

### Step 1: 日数の決定

引数が指定されている場合（`3d`, `7d`, `14d` など）はその日数を使用する。
指定がなければデフォルトは **7日**。

今日の日付をもとに:
- Slack検索用の `after:YYYY-MM-DD` を算出する（N+1日前の日付）
- Notion取得後フィルタ用の `start_date` を算出する

### Step 2: データ収集（Slack経由 ＋ Notion直接検索を並行実行）

まず **監視ユーザーグループを取得する**:
- `~/.claude/board/council/usergroups.json` を Read で読み込み、各エントリの `handle` を監視対象とする。
- ファイルが無い／空／読めない場合は既定の `group_alpha` / `group_designer` / `group_beta` を使う。

以下をすべて**同時実行**する:

**Slack経由** — `slack_search_public_and_private`:

| クエリ | パラメータ |
|--------|-----------|
| `from:Notion <@SELF_ID> after:YYYY-MM-DD` | `include_bots: true`, `sort: timestamp`, `limit: 20` |
| `from:Notion <handle> after:YYYY-MM-DD`（取得した各 handle につき1本） | `include_bots: true`, `sort: timestamp`, `limit: 20` |

> Notion bot からの通知を取得するため `include_bots: true` が必須。

**Notion直接検索（3クエリ）** — `notion-search`:

| クエリ | 意図 |
|--------|------|
| `@me` | コメント内のメンション |
| `@me` | コメント内のメンション（表記ゆれ対応） |
| `レビュー依頼` | デザインレビュー依頼ページの検索 |

各Notion検索パラメータ:
- `query_type`: `"internal"`
- `filters`: `{}`
- `page_size`: `10`

### Step 3: 結果の統合・重複排除

- Slack 2クエリの結果を `ts` で重複排除
- Notion 3クエリの結果をページURLで重複排除
- 取得後に `last_edited_time` が対象期間内のものに絞り込む

### Step 4: Notionページ本文・コメントの取得（並行実行）

**Slack通知で見つかったNotion URL**: `notion-fetch` で**全件並行取得**

**Notion直接検索で見つかったページ**: `notion-fetch` + `notion-get-comments` を**各ページ並行実行**:
```
各ページに対して同時:
  - notion-fetch (page_id)
  - notion-get-comments (block_id: page_id)
```

エラー発生時は graceful skip（そのページをスキップして続行）。

### Step 5: メンション・コメントの絞り込み

取得したコメントから自分（me / me）へのメンションを含むものを抽出する。
コメントがない場合でも、ページ本文にデザインレビュー依頼・フィードバック依頼があれば対象とする。

---

## councilから呼ばれた場合の出力

以下の形式のデータを返す:

```
[
  {
    "source": "notion",
    "source_detail": "slack_notification" or "notion_direct",
    "page_id": "<NotionページID>",
    "page_title": "<ページタイトル>",
    "page_url": "<NotionページURL>",
    "last_edited": "<最終編集日時>",
    "content_summary": "<ページ内容の要約>",
    "comments": [<コメントの配列>],
    "my_mentions": [<自分へのメンションを含むコメント>]
  },
  ...
]
```

---

## 単体実行時の分析と出力

`/council-notion Nd` で単体実行した場合は、収集後に以下の分析を行って表示する。

### 各アイテムの分析

**概要（1〜2文）**: ページの目的と自分に求められているアクションを要約。

**アクション種別の判定**:
- `DESIGN_REVIEW`: Figma埋め込み・デザイン確認依頼
- `DIRECTION`: 方向性・意思決定を求めるもの
- `FEEDBACK`: コメント・フィードバックの依頼
- `APPROVAL`: 承認・最終確認
- `INFO`: 参照・情報共有のみ

**優先度の判断**:
- ★★★: コメントで直接メンションされている、またはDeadlineが今週中
- ★★: ページ本文にレビュー・フィードバック依頼あり
- ★: 情報共有・参照のみ

**価値仮説の推定**（`INFO` 以外のアイテムのみ）:
依頼内容・ページ本文・コメントをもとに以下を各1文で推定する。
- **欲求**: 依頼者が本当に達成・解決したいこと（HOW／手段は入れない）
- **課題**: 欲求を満たすのを妨げている状況の制約（「〜ないため」の形）

### アートディレクターとしての返信ドラフト生成

Notionコメントへの返信ドラフトを生成する。

**生成原則**:
- 日本語で簡潔・決断的
- コメントで指摘された具体的な箇所に言及
- ページ上のコメントとして返信するトーン（「〇〇セクション: 〜について」と箇所を明示）
- グラフィック品質・デザインシステム準拠の観点を含める

### 単体実行時の出力フォーマット

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 Notion メンション（過去 N 日間: X 件）
  ├ Slack通知経由: A 件
  └ Notion直接検索: B 件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[1] ★★★ DESIGN_REVIEW | 2026-05-18 | Screen B リニューアル仕様書
ソース: Notion直接（コメントメンション）
概要: コンポーネント選定についてコメントで @me へ確認依頼。
依頼: Button コンポーネントの variant 選択について意見を求められている。
欲求推定: 実装前に仕様を確定させてデザインと実装の乖離をなくしたい
課題推定: コンポーネント選定の判断軸がチーム内で共有されていないため
📄 https://www.notion.so/...

💬 ADとしての返信ドラフト（Notionコメント用）:
─────────────────────────
確認しました。このケースでは `Button/Primary` ではなく `Button/Secondary` が適切です。
理由：メインCTAが同一画面に既に存在するため、視覚的階層を保つ必要があります。
Figmaのコンポーネント仕様はこちら：[design-system/Button]
─────────────────────────

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
合計 アクション必要: X 件 / 情報共有のみ: Y 件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

0件の場合: 「過去 N 日間に対象の Notion メンションはありませんでした。」と表示。
