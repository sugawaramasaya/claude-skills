---
name: team-digest
description: member-a/member-b/member-c 3名からの Slack メンションと Figma コメントを並行収集し、送信者別に概要・アクション依頼を表示する統合 digest。引数で日数指定可（例：/team-digest 3d）。
---

# team-digest スキル（オーケストレーター）

member-a（Member A）/ member-b（Member B）/ member-c（Member C）の3名から me（`U_SELF`）または所属グループ（`group_alpha` / `group_designer` / `group_beta`）へのメンションを **1コマンドで横断収集** し、送信者別に概要・アクション依頼を表示する。`member-a-digest` / `member-b-digest` / `member-c-digest` を毎回個別に叩く手間を集約したオーケストレーター。

**設計方針（BP: Multi-Agent Orchestration / 並列最大化・依存最小化 / graceful degradation）**: メインがサブエージェント4つを並列起動する。Agent-K / Agent-U / Agent-M が各人の Slack 収集＋分析を担当し、Agent-F が Figma を **1回だけ** 取得して3名に振り分ける（案件のチャンネルは数百件・DM は数万文字と大きいため重複取得を回避）。

## 対象者・定数

| 人物 | Slack ID | Figma 表示名（判定キー） | 主な発信先 |
|---|---|---|---|
| member-a Member A | `U_MEMBER_A` | `Member A`（`Member A` / `member-a`） | group_alpha 中心 |
| member-b Member B | `U_MEMBER_B` | `Member B`（`Member B` / `Member B` / `member-b`） | group_alpha / group_designer |
| member-c Member C | `U_MEMBER_C` | `Member C` 推定（`Member C` / `Member C` / `member-c`） | group_beta 中心 |
| me（自分） | `U_SELF` | — | — |

- 監視グループ: `group_alpha` / `group_designer` / `group_beta`
- Figma ファイル（既定ウォッチ）: `YOUR_FIGMA_FILE_KEY`（Project Alpha）
- Figma アプリ DM チャンネル: `D_FIGMA_DM`
- FIGMA_TOKEN: キーチェーン `security find-generic-password -s "FIGMA_TOKEN" -w`
- ⚠️ Slack の `from:` は filter ではなく **クエリ文字列**に入れる（filter だと無視され0件になる）。
- ⚠️ **「4クエリの罠」（2026-09-04 実測で判明・元に戻さないこと）**
  - 旧仕様は `from:<@相手> <@U_SELF> after:日付` のように**メンション文字列を本文のキーワードとして AND 検索**していた。相手が**スレッド内で返信**した場合は本文にメンションが入らないため、**まるごと取りこぼす**。
  - 実例＝2026-09-02 17:29 の member-a のスレッド返信（#proj-alpha-feature）は**4クエリすべて0件**。`from:` + 日付だけに落として初めて出てきた。
  - このため「0件」の表示は、**本当に0件なのか取りこぼしなのかを区別できていなかった**。

## 実行手順

### 1. 日数の決定

引数が指定されている場合（例: `3d`, `7d`, `14d`）はその日数を使用する。指定がなければデフォルトは **7日**。

今日の日付をもとに対象期間の開始タイムスタンプ（Unix 秒）と `after:YYYY-MM-DD` の日付を算出する。

> **注意**: Slack の `after:` フィルターは指定日を含まない（翌日以降が対象）。
> そのため「N日前まで遡る」場合は、(N+1)日前の日付を使う。
> 例: 過去7日 → `after:（8日前）`、昨日〜今日 → `after:（2日前）`

決定した日数・`after:` 日付は、後続の全サブエージェントに同一値で渡す。

### 2. サブエージェントを4つ並列起動（single message・同時）

`Agent` ツールで以下4つを **同一メッセージ内で同時に** 起動する。互いに依存しないため必ず並列にする。

#### Agent-K / Agent-U / Agent-M（各人の Slack 担当）

各エージェントは担当者1名について、以下を実行して構造化結果を返す（**Figma 取得は行わない**＝重複防止）。手順は各個別 digest スキルの手順2〜5に準拠する。

担当者プレースホルダを `<@TARGET_ID>`（member-a=`U_MEMBER_A` / member-b=`U_MEMBER_B` / member-c=`U_MEMBER_C`）として：

1. **Slack 検索（1クエリ・広く引く）** — `slack_search_public_and_private`、パラメータ `sort=timestamp` / `sort_dir=desc` / `include_bots=false` / `limit=20`：
   - `from:<@TARGET_ID> after:YYYY-MM-DD`
   - `limit` は最大20。20件返ったら `cursor` で次ページを取り、**対象期間の最古に届くまで**繰り返す。
   - ⚠️ **メンション文字列をクエリに足さない**（前述の「4クエリの罠」）。
2. **絞り込み（本文の文字列ではなく、宛先と関与で判定する）** — 1で取れた全件を `message_ts` で重複排除し、対象期間内に絞ったうえで、**次のいずれかに該当するものだけを残す**：
   - (a) 本文に `<@U_SELF>` を含む（自分への直接メンション）
   - (b) 本文に `group_alpha` / `group_designer` / `group_beta` を含む（グループ宛）
   - (c) **自分が関与しているスレッド内の発言** — `thread_ts` を持つメッセージについて `slack_read_thread` で親スレッドを引き、**親または他の返信に `<@U_SELF>` か自分（`U_SELF`）の発言がある**なら残す
   - (a)(b)(c) のどれにも当たらないものは除外する（相手が別件で書いただけのものはノイズ）
   - (c) の判定でスレッドを読むのは、(a)(b) で既に残ったものを除いた**残り**だけでよい（無駄な取得を避ける）
   - 残ったものをタイムスタンプ降順にソートする。
3. **スレッドリプライ取得（並行）** — `reply_count > 0` のメッセージについて `slack_read_thread`（`channel_id` / `thread_ts`=親 `ts` / `limit=20`）を**すべて並行**で実行。**手順2の (c) 判定で既に読んだスレッドは再取得せず、その結果を再利用する。** エラーは graceful skip。
4. **Notion リンク取得（並行・必須）** — 親メッセージ本文とスレッドリプライ本文から `notion.so` URL を探し、見つかった分をすべて並行で `notion-fetch`。1件も無い場合のみスキップ。
5. **分析** — 各メッセージ（＋Notion 内容＋スレッド）について：
   - **概要**（1〜2文・日本語）。スレッドがあれば末尾に「スレッド X 件（参加者 Y 名）」を付記。
   - **アクション依頼の抽出** — me / 各グループに対し「何をしてほしいか」。無ければ「情報共有のみ」。
   - **価値仮説の推定**（依頼がある場合のみ）— 欲求（達成したいこと・HOW を含めない）／課題（「〜ないため」の制約）を各1文。
   - **優先度** — ★★★（期限・締切の言及／強い言い回し）／★★（依頼・質問あり）／★（情報共有・CC）。
   - スレッドがある場合は決定事項・未解決の論点・他参加者からの追加依頼も抽出。

各エージェントは「Slack メンション配列（各要素に優先度・概要・依頼・欲求/課題・スレッド要約・URL を含む）」を返す。

#### Agent-F（Figma 担当・1回のみ取得して3名へ振り分け）

1. **Figma DM 通知** — `slack_read_channel`（`channel_id=D_FIGMA_DM` / `limit=50`）。対象期間の最古に届かなければ `cursor` で次ページを補う。
2. **Figma REST API（必須）** — Bash で実行：
   ```bash
   FIGMA_TOKEN=$(security find-generic-password -s "FIGMA_TOKEN" -w)
   curl -s -H "X-Figma-Token: $FIGMA_TOKEN" \
     "https://api.figma.com/v1/files/YOUR_FIGMA_FILE_KEY/comments"   # Project Alpha
   ```
   - 通知本文・コメント内に生URL（`figma.com/design/<KEY>`）があれば、その `<KEY>` も対象に追加。
   - `comments[]` から対象期間内（`created_at`）のコメントを抽出。
   - ⚠️ REST API の `user.handle` は Figma 表示名で Slack ハンドルとは別物。**3名の判定キー**で振り分ける：
     - member-a → `Member A` / `member-a`
     - member-b → `Member B` / `Member B` / `member-b`
     - member-c → `Member C` / `Member C` / `member-c`
   - 401/404/トークン無しは graceful skip（DM通知のみで続行・その旨を注記）。
3. **マージ** — (1)(2) を重複排除（commenter + message先頭40字 + 時刻60秒以内をキー、REST優先）。
4. 各コメントを上記キーで member-a / member-b / member-c に振り分け、各人ごとに概要・依頼・優先度（★基準は Slack と同じ）を付けて返す。どの3名にも該当しないコメントは除外する。

### 3. マージ

Agent-F が返した Figma 振り分け結果を、Agent-K / U / M の Slack 結果とそれぞれ突き合わせ、人物ごとに「Slack メンション」「Figma コメント」の2系統を保持する。

### 4. 出力フォーマット

冒頭に全員横断の **🔥 今すぐ対応** サマリ（★★★ 案件を人物名つきで集約）、続いて送信者別セクション（**member-a → member-b → member-c** の順）。各セクション内は Slack メンションと Figma コメントを分けて表示する。

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 今すぐ対応（過去 N 日間・★★★）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
・[member-a] Feature Aの仕様レビュー依頼（5/18 18:26）→ Notion 仕様書 v1.2
・[member-c] Project BetaPDのKPI定義の確認（…）
（★★★ が無ければ「★★★ 案件はありません」）

════════════════════════════════
👤 member-a（Member A）
════════════════════════════════
━━━ 📨 Slack メンション（X 件）━━━

[1] ★★★ 2026-05-18 18:26 | #proj-alpha-all
概要: 〜。スレッド 30 件（参加者 5 名）。
依頼: 〜
欲求推定: 〜
課題推定: 〜
💬 スレッド: 30件 — 決定: 〇〇 / 未解決: △△
🔗 https://your-workspace.slack.com/archives/...

━━━ 🎨 Figma コメント（Y 件）━━━

[1] ★★ 2026-05-18 17:23 | Project Alpha
概要: 〜
依頼: 〜

════════════════════════════════
👤 member-b（Member B）
════════════════════════════════
（同様）

════════════════════════════════
👤 member-c（Member C）
════════════════════════════════
（同様）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
合計 アクション必要: X 件 / 情報共有のみ: Y 件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**フォーマットルール:**
- スレッドリプライがある Slack メッセージには `💬 スレッド:` 行を追加（件数・決定事項・未解決の論点のうち該当するもの。すべて不明なら件数のみ）。
- Figma コメントにはスレッド行を追加しない（Figma DM はスレッド構造を持たない）。
- ある人物の Slack・Figma がともに0件なら、そのセクションは「過去 N 日間にメンション・コメントはありませんでした。」と1行で表示。

### 5. graceful degradation

いずれかのサブエージェント／Figma 取得が失敗しても、他は継続して出力する。失敗した取得は該当セクションに「（取得失敗のため未反映）」と注記する。全員・全経路が0件の場合は「過去 N 日間に3名からのメンション・コメントはありませんでした。」と表示する。

## Council 連携（Activity への自動書き出し）

ターミナルへの表示が完了したら、**送信者（member-a / member-b / member-c）ごとに1エントリずつ**、Council アプリの Activity フィードへ書き出す（ヘルパーを人数分・1回ずつ呼ぶ）。

- 対象: Slack・Figma のいずれかが1件以上ある送信者のみ。両方0件の送信者はスキップする（ヘルパーを呼ばない）。
- 各送信者のエントリ内容:
  - `kind`: `"digest"`
  - `source`: `"team-digest"`
  - `title`: `"<送信者名> N件（主な案件）"`（例: `"member-a 3件（Feature Aレビュー依頼ほか）"`）
  - `summary`: その送信者のSlack・Figma全体の要約（1〜2文。★★★案件があれば優先して触れる）
  - `action_items`: その送信者からの「依頼」をすべて配列化（「情報共有のみ」は除外）
  - `project`: その送信者の主な案件名
  - `links`: その送信者に関連するSlack/Figma/Notion URL
- `id`/`ts`/`date` は指定しない（ヘルパーがミリ秒＋乱数で自動補完し、同一プロセス内で連続 append しても衝突しない）。

呼び出し例（送信者ごとに繰り返す。JSON に日本語・引用符・改行を含むため stdin パイプ推奨）:

```bash
cat <<'JSON' | node ~/.claude/scripts/council-activity-append.js
{
  "kind": "digest",
  "source": "team-digest",
  "title": "member-a 3件（Feature Aレビュー依頼ほか）",
  "summary": "Feature AのScreen Aの仕様変更レビュー依頼が中心。",
  "action_items": ["Notion仕様書v1.2のレビュー・コメント"],
  "project": "Project Alpha",
  "links": [{"url": "https://your-workspace.slack.com/archives/...", "label": "Slack: Feature Aレビュー依頼"}]
}
JSON
```

```bash
cat <<'JSON' | node ~/.claude/scripts/council-activity-append.js
{
  "kind": "digest",
  "source": "team-digest",
  "title": "member-c 1件（KPI定義確認）",
  "summary": "Project BetaPDのKPI定義について確認依頼。",
  "action_items": ["KPI定義の確認・返信"],
  "project": "Project Beta",
  "links": []
}
JSON
```

member-b が0件ならこの呼び出しはスキップする（3名それぞれ独立に判定する）。

## Board へのタスク切り出し（AI選別・自動）

Activity への書き出しに続けて、**3名分の `action_items` をまとめて選別**し、タスク化すべきものだけを1件ずつ `council-task-append.js` に渡す（Council アプリの Board に Task として現れる）。

- **選別基準**: 「自分（me）が実行すべき具体的な作業」で「未完了」のもの。以下は除外する：
  - 単なる情報共有・FYI（「情報共有のみ」と判定した項目）
  - すでに返信・対応済みで完結しているもの
  - member-a / member-b / member-c 本人や他者が実行すべきタスク（自分向けではない依頼）
- **priority**: 依頼元の ★ 優先度から機械的に変換する：★★★→`high` / ★★→`medium` / ★→`low`（★=情報共有は基本的に選別基準で除外される想定）。
- **activity_source**: `"team-digest"` 固定（送信者名は `title` 側に含める）。
- 選別後の対象が **0件ならヘルパーを呼ばない**。3名を横断して0件かどうかで判定する。
- 呼び出しは依頼1件=1回、stdin パイプ推奨（JSON に日本語・引用符が含まれるため）:

```bash
cat <<'JSON' | node ~/.claude/scripts/council-task-append.js
{
  "title": "[member-a] Notion仕様書v1.2のレビュー・コメント",
  "priority": "high",
  "activity_source": "team-digest"
}
JSON
```

3名合わせて対象が複数ある場合は、この呼び出しを依頼ごとに繰り返す（`id` は title+activity_source から決定的に生成されるため、同じ依頼を再実行しても増殖せず、Board 側でユーザーが編集・完了・アーカイブ済みのタスクを上書き復活させない）。
