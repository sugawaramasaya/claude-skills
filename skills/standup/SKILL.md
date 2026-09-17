---
name: standup
description: ローカルボード（done/in-progress/blocked/ai-handoff）を読み、本日の優先3件と進行状況を提示する。朝のキックオフ・夕方の振り返りなど任意のタイミングで使える。ファイルは書き換えない（読み取り専用）。
---

# /standup スキル

ローカルボードを読み取り専用で参照し「昨日の完了・現在の進行状況・今日のTop3・引き継ぎログ」を一画面にまとめる。**ファイルは書き換えない（読み取り専用）。**

## 実行手順

### 1. ボードを並行読み取り

以下を **すべて並行実行** する：

- `~/.claude/board/done/` — 直近 24h 以内に完了したカード（mtime で判定）
- `~/.claude/board/in-progress/` — 進行中カード（全件）
- `~/.claude/board/blocked/` — ブロック中カード（全件）
- `~/.claude/board/inbox/` — 未トリアージのメモ（件数のみ確認）
- `~/.claude/projects/-Users-you-Documents-work/memory/ai-handoff/` — 引き継ぎログ（最新 2〜3 件）

存在しないディレクトリはスキップする。

### 2. 優先度の判定

各カードの frontmatter（`priority`, `deadline`, `status`）を読み取り、以下の基準で Today's Top 3 を選ぶ：

1. **P0** — deadline が今日・明日、または status が blocked
2. **期限切迫** — deadline が今週中の P1
3. **P1** — 明確な依頼・実装タスク
4. **停滞** — in-progress にあって更新が古いもの

### 3. 出力フォーマット

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 スタンドアップ（YYYY-MM-DD）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## ✅ Yesterday / Overnight（直近 24h で完了）

- タスク名（完了時刻）
なし

## 🔄 In Progress / Blocked

- [進行中] タスク名 — 経過 X 日
- [ブロック] タスク名 — 待ち: 理由 / 滞留 X 日

なし

## 🎯 Today's Top 3

1. [P0] タスク名 — deadline: YYYY-MM-DD
2. [P1] タスク名 — deadline: YYYY-MM-DD
3. [P1] タスク名

## 📬 Open Handoffs

- YYYY-MM-DD: 引き継ぎ概要（ai-handoff/）

なし

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
inbox 未トリアージ: X 件（あれば /inbox-triage を推奨）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**フォーマットルール:**
- ボードが全て空の場合は「ボードにタスクはありません。」と表示する
- inbox に 1 件以上ある場合は末尾に `/inbox-triage` を促す
- ai-handoff/ が空またはディレクトリ不在の場合は「Open Handoffs」セクションを省略する
- ファイルの書き換えは一切行わない
