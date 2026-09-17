---
name: health-check
description: 指定パス（省略時は現在ディレクトリ）の silent failure（静かに壊れる障害）を検査し、検出物を blocked/ にカード化する。修復は行わない。
---

# /health-check [path]

Silent failure（エラーも警告も出さずに「完了しました」と返ってくる障害）を検出するスキル。
**検出のみ行い、修復は行いません。**

## 使い方

```
/health-check ./my-project
/health-check          # 省略時は現在ディレクトリ
/health-check memory   # memory の参照ずれだけを検査する（後述）
```

## 手順

0. **引数が `memory` なら、下の「memory の参照ずれ検査」だけを実行して終わる**（コードの検査はしない）
1. 対象パスを確認する（引数なし → 現在の作業ディレクトリ）
2. 以下の観点で end-to-end 検査を実施する:
   - **型チェック**: TypeScript の型エラー（tsc --noEmit）
   - **依存関係**: package.json と lock ファイルの整合性
   - **環境変数**: .env.example に定義されているが実際の .env にない変数
   - **ビルド**: 本番ビルドが通るか（npm run build など）
   - **テスト**: 既存テストが通るか
   - **デッドコード**: 参照されていないエクスポートやファイル
   - **API整合性**: 型定義と実装の乖離
3. 問題を検出したら `~/.claude/board/blocked/` にカードを作成する:
   - ファイル名: `<NNN>-health-<slug>.md`
   - frontmatter に `assignee: human`, `priority: P1`（重大なら P0）を設定
4. 検査サマリーを報告する

## memory の参照ずれ検査（2026-09-04 追加）

**memory に書かれた「実物を指す主張」が、実物とずれていないかを見る。** これも silent failure＝何も壊れないまま、次のセッションが誤った前提で動く。

```bash
python3 ~/.claude/scripts/memory-ref-check.py --pr
```

検査するのは4種類。**判定ロジックはスクリプト側にある。ここで正規表現を書き直さないこと**（書き直すと、それ自体が未検証の手順になる）。

| 種類 | 突き合わせ先 |
|---|---|
| パス（`~/.claude` `~/Projects` `~/Documents` `~/dev` `~/side-projects`） | ファイルの実在 |
| launchd ラベル（`jp.acme.*`） | `launchctl list` |
| スキル参照（`` `/name` ``） | `~/.claude/skills/` の実体 |
| PR（`github.com/acme/<repo>/pull/N`・`--pr` 時のみ） | `gh pr view` の state と、同じ行の状態語 |

出力は2バケットに分かれる。

- **要対応** — 本文が不在を説明していない。読んだ人が「まだある」と誤解する
- **本文に注記あり** — 「削除済み」「着手していない」等が近くに書かれている。**これは正常なので直さない**

### 使いどころ

**定期実行にしない。** カードを自動で増やしても読まれない（`board/inbox/` に未処理が溜まる）。次の2つのタイミングで手で打つ。

1. **`~/Documents` や `~/Projects` のファイル整理をした直後** — 2026-09-04 の初回点検では、ずれ7件のうち**5件がファイル移動**だった。整理はこの検査を打つ合図
2. **四半期末** — `/worklog-review` の前に通すと、古い前提のまま振り返りを書くのを防げる

### 直しかた

**このスキルは検出のみ。修復しない。** 直すときは `rules/common/memory-layers.md` の「腐らせない書き方」に従う。

- **移動していただけなら**、新しいパスに直す。加えて**ファイル名＋起点**の形にすると次の移動に耐える
- **状態（open/closed・サイズ・稼働）がずれていたなら**、状態を消して**取得コマンド**に置き換える
- **もう無いものなら**、消さずに**打ち消しを1行足す**（`⚠️ 2026-XX-XX 実測で実体なし`）。過去の記録としては正しいので、消すと経緯が失われる

## 検出物のカード形式

```yaml
---
id: <NNN>
title: [health-check] <問題の概要>
created: <YYYY-MM-DD>
assignee: human
priority: P1
area: engineering
status: blocked
source: health-check
target_path: <検査対象パス>
---

## 問題の内容
（検出された具体的な問題）

## 再現手順
（どうすれば問題を確認できるか）

## 修復の手がかり
（推測される原因や参考情報）
```

## 注意

- 修復は行いません。検出結果を blocked/ に記録し、ユーザーが判断します。
- /standup で blocked カードを確認し、対応方針を決めてください。
