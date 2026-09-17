---
name: inbox-triage
description: ~/.claude/board/inbox/ の未処理メモをトリアージし、todo/<area>/ に振り分ける。新しいアイデアや依頼が inbox に溜まった時に使う。
---

# /inbox-triage

`~/.claude/board/inbox/` に投げ込まれたメモをトリアージして `todo/<area>/` に振り分けるスキル。

## 手順

1. `~/.claude/board/inbox/` の `.md` ファイルを Read する
   - **`council-*.md` は対象外。** council-daily.sh が自分でステータス表示に使っているカードで、次回の自動実行が成功したとき `clear_cards()` が `rm -f` する。`todo/` に移すと自動削除が外れて、上限が解消しても消えないカードが残る（2026-08-25）
2. 各ファイルについて以下を推定する:
   - **area**: `engineering` / `content` / `business` / `ops` / `personal`
   - **priority**: `P0`（緊急） / `P1`（重要） / `P2`（通常） / `P3`（低）
   - **title**: 内容を表す短いタイトル
   - **estimate**: 作業時間の概算（例: `30m`, `2h`, `1d`）
3. 振り分け案を一覧でユーザーに提示し、承認を得る
4. 承認されたら:
   - `~/.claude/board/todo/<area>/` に frontmatter 付きカードファイルを Write で作成
   - 元の inbox ファイルをユーザー承認のもと削除
   - `~/.claude/board/dispatch-log.md` に処理履歴を **append** (`>>`) で記録

## カードのフロントマター

```yaml
---
id: <3桁連番>
title: <タイトル>
created: <YYYY-MM-DD>
assignee: human
priority: <P0-P3>
area: <area>
estimate: <時間>
status: todo
---
```

## dispatch-log への記録形式

```
## <YYYY-MM-DD HH:MM>
- [<priority>] <title> → todo/<area>/<filename>.md
```

## 注意

- `dispatch-log.md` は `>` (上書き) 禁止。必ず `>>` (append) または Edit で追記すること。
- board-guard が `>` での上書きをブロックします。
