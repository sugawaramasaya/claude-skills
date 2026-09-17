# claude-skills

個人で使っている [Claude Code](https://claude.com/claude-code) のスキル25個。
デザイナーの実務（Figma の作図・デザインの検品・チームからの受信の集約・作業ログ）を
Claude Code 側に寄せるために作ったもの。

**これは動く成果物ではなく、テンプレートです。** 元の環境に固有だった値
（社名・人名・Slack/Figma/Notion/Drive の ID・ブランド色・社内リポジトリ名）は
すべてプレースホルダに置き換えてあるので、**自分の値を入れるまで動かないスキルがあります**。
どこを埋めるかは下の「セットアップ」にまとめました。

## スキル一覧

**「何を打つと何がどこに出るか」の対応表は [`skills/README.md`](skills/README.md)。**
書き込みの有無（ファイルが増えるか書き換わるか）も列にしてあります。

| 群 | スキル | セットアップ |
|---|---|---|
| **デザインを見る・直す** | `figma-design-basics` — Figma に書き込む前に読む基本手順（着手前の調査／作図の原則／検品） | 不要 |
| | `figma-ia-checker` — UI 画面の情報設計をチェックして改善提案 | Figma MCP |
| | `figma-comment-map` — Figma の全コメントを画面スクショにピンで復元した HTML/PDF レポート | `FIGMA_TOKEN` |
| | `design-check` — 画像/PDF を重心・大きさ・カーニング・色の4観点で定量解析 | **要パレット登録** |
| | `ds-drift-audit` — コード側のトークンと Figma のライブ・プリミティブ色を突合 | **要プリミティブ取得** |
| **チームからの受信を集める** | `team-digest` / `member-a-digest` / `member-b-digest` / `member-c-digest` | **要メンバー設定** |
| | `council` — 10ロールの評議会で受信に優先度をつけ、返信ドラフトまで作る | **要メンバー設定＋ヘルパー** |
| | `council-slack` / `council-notion` / `council-figma` — 収集サブスキル（単体実行も可） | **要メンバー設定** |
| | `meeting-digest` — 議事録から決定事項・その背景・自分のタスクを抽出 | Notion MCP |
| | `morning` / `standup` — 朝のまとめ／ボードの状況（読み取り専用） | **要ボード構造** |
| **記録する・振り返る** | `worklog` / `worklog-review` — 四半期ワークログと期末の下書き生成 | **要評価軸ファイル** |
| | `transcript-in` / `transcript-digest` — 会議の書き起こしの取り込みとタスク化 | **要 Drive フォルダID** |
| | `mirror` — 「自分を再現する AI」の再現テスト（出力の封・進行の封） | **要判断軸ファイル** |
| **環境を整える・その他** | `health-check` — silent failure（静かに壊れる障害）を検査してカード化。修復はしない | 不要 |
| | `inbox-triage` — 溜まったメモをトリアージして振り分ける | **要ボード構造** |
| | `explain-code` — アナロジーと ASCII 図でコードを説明 | 不要 |
| | `weather` — 天気・気温・降水確率 | 不要 |

**セットアップ不要の4つ**（`figma-design-basics` / `health-check` / `explain-code` / `weather`）は
置くだけで動きます。まず試すならこのあたりから。

## 導入

```bash
git clone https://github.com/<あなた>/claude-skills.git
cd claude-skills

# 個人の ~/.claude/skills/ へ置く（既存と同名のものがあると上書きされるので注意）
cp -R skills/<使いたいスキル> ~/.claude/skills/

# プロジェクト単位で使う
cp -R skills/<使いたいスキル> /path/to/your-project/.claude/skills/
```

全部まとめて入れるより、**使うものだけ選んで入れる**のを勧めます。
セットアップが済んでいないスキルは、`/` を打ったときの候補に出てきて紛らわしいだけになります。

## セットアップ

### 1. 埋めるプレースホルダ

該当スキルの `SKILL.md` を開いて置き換えます。

| プレースホルダ | 入れるもの | 使っているスキル |
|---|---|---|
| `Member A` `Member B` `Member C` | 受信を追いたい相手の名前 | `team-digest` `member-*-digest` `transcript-digest` |
| `U_MEMBER_A` `U_MEMBER_B` `U_MEMBER_C` | 同じ相手の Slack ユーザーID（`U…`） | `team-digest` `member-*-digest` |
| `U_SELF` | 自分の Slack ユーザーID | `council*` `team-digest` `member-*-digest` |
| `group_alpha` `group_beta` `group_designer` | 自分が入っている Slack ユーザーグループ名 | 同上 |
| `your-workspace.slack.com` | 自分の Slack ワークスペースのドメイン | 同上 |
| `YOUR_FIGMA_FILE_KEY` | コメントを追いたい Figma ファイルの fileKey | `council` `council-figma` `team-digest` `member-*-digest` |
| `YOUR_DS_FILE_KEY` `YOUR_NODE_ID` | デザインシステムのファイルとノード | `ds-drift-audit` |
| `YOUR_DRIVE_FOLDER_ID` | 会議の記録が入っている Google Drive フォルダID | `transcript-in` |
| `your-org/...` | 自分の GitHub Organization とリポジトリ | `ds-drift-audit` `morning` |
| `Project Alpha` `Project Beta` `Feature A` `Screen A` | 自分の案件名・画面名（サンプル出力の中の例） | 多数（**置き換えなくても動く**） |

`Project Alpha` などは出力例の中の飾りなので、動作には影響しません。読みやすさのために直すだけです。

### 2. 値を用意するもの

| 何が必要か | どうするか |
|---|---|
| **`design-check` のパレット** | `skills/design-check/scripts/common.py` の `BRAND_COLORS` / `SYSTEM_COLORS` を自分のブランド色に差し替える。同梱の値は**中立なプレースホルダで、どこかの組織の色ではない**。差し替えないと色の観点だけ無意味な結果になる（重心・大きさ・カーニングの3観点は影響を受けない） |
| **`ds-drift-audit` のプリミティブ色** | 自分の Figma から `get_variable_defs` で取得し `figma_primitives.light.json` として置く（手順は `skills/ds-drift-audit/SKILL.md`）。同梱の `figma_primitives.sample.json` は**キーの形と値の型を示すだけの中立サンプル**で、実在する DS ではない |
| **`FIGMA_TOKEN`** | Figma の Personal Access Token。`figma-comment-map` が REST API を叩くのに使う |
| **`evaluation-axes.md`** | `~/.claude/rules/common/evaluation-axes.md` に自分の評価軸（F1〜F5）を書く。無ければ `council` の Role-Evaluator と `worklog-review` の軸別振り返りはスキップされる。同梱の5軸は**書き方のサンプル**で、そのまま使うものではない |

### 3. リポジトリに入っていない依存

**ここは正直に書きます。以下はこのリポジトリに含まれていないので、該当スキルは単体では完結しません。**

| 依存 | 使っているスキル | 無いとどうなるか |
|---|---|---|
| `~/.claude/scripts/council-activity-append.js` | `council` `team-digest` `member-a-digest` `meeting-digest` `transcript-digest` | ターミナルへの表示はできるが、Activity への書き出しが失敗する |
| `~/.claude/scripts/council-task-append.js` | 同上 | 同上（Task への書き出し） |
| `~/.claude/board/` のディレクトリ構造（`inbox` `todo` `blocked` `done` `in-progress` `mirror` `transcripts` `council`） | `morning` `standup` `inbox-triage` `health-check` `mirror` `transcript-*` | 読み込み先が無いので空の結果になる。`mkdir -p` で作れば動く |
| `~/.claude/rules/common/philosophy.md` / `ui-design-principles.md` | `mirror` `figma-design-basics` | 判断軸の参照先が無い。自分の判断軸を書いて置き換える |
| MCP サーバー（Slack / Notion / Figma / Google Calendar / Google Drive） | 受信系ぜんぶ、`transcript-in`、`morning` の予定取得 | そのスキルは動かない。`morning` は予定取得の節だけスキップして続行する設計になっている |

`board` は自分で作れます:

```bash
mkdir -p ~/.claude/board/{inbox,todo,blocked,done,in-progress,mirror,transcripts,council}
```

## 注意

- **スキルはファイルを書き換えます。** どのスキルが書き込むかは [`skills/README.md`](skills/README.md) の「書き込み」列で確認してください。読み取り専用のものは気軽に試せます
- 日本語で書かれています。Claude Code 側の応答言語を日本語にしている前提の文面が混じっています
- 元の環境（macOS / Claude Code CLI）以外では試していません

## ライセンス

MIT
