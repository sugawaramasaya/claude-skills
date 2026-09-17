# claude-skills

個人で使っている [Claude Code](https://claude.com/claude-code) のスキル25個。
デザイナーの実務（Figma の作図・デザインの検品・チームからの受信の集約・作業ログ）を
Claude Code 側に寄せるために作ったもの。

**これは動く成果物ではなく、テンプレートです。** 元の環境に固有だった値
（組織名・人名・Slack/Figma/Notion/Drive の ID・ブランド色・リポジトリ名）は
すべてプレースホルダに置き換えてあるので、**自分の値を入れるまで動かないスキルがあります**。
どこを埋めるかは下の「セットアップ」にまとめました。

## 導入

### プラグインとして入れる（推奨）

```
/plugin marketplace add sugawaramasaya/claude-skills
/plugin install claude-skills@claude-skills
```

初回のセッション開始時に、スキルが使う `~/.claude/board/` のディレクトリと
Council のヘルパー2本を用意します。**既にあるものは触りません**（上書きしません）。

### ファイルを直接置く

プラグインを使わず、必要なものだけ選んで置くこともできます。

```bash
git clone https://github.com/sugawaramasaya/claude-skills.git
cd claude-skills

cp -R skills/<使いたいスキル> ~/.claude/skills/          # 個人用
cp -R skills/<使いたいスキル> <プロジェクト>/.claude/skills/  # プロジェクト用
```

この場合、`~/.claude/board/` のディレクトリは自分で作ります。

```bash
mkdir -p ~/.claude/board/{inbox,todo,blocked,done,in-progress,mirror,transcripts,council}
cp scripts/*.js ~/.claude/scripts/
```

**呼び出し名が変わります。** プラグインで入れると `/claude-skills:design-check`、
ファイルを直接置くと `/design-check` になります。

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
| | `council` — 10ロールの評議会で受信に優先度をつけ、返信ドラフトまで作る | **要メンバー設定** |
| | `council-slack` / `council-notion` / `council-figma` — 収集サブスキル（単体実行も可） | **要メンバー設定** |
| | `meeting-digest` — 議事録から決定事項・その背景・自分のタスクを抽出 | Notion MCP |
| | `morning` / `standup` — 朝のまとめ／ボードの状況（読み取り専用） | 不要 |
| **記録する・振り返る** | `worklog` / `worklog-review` — 四半期ワークログと期末の下書き生成 | **要評価軸ファイル** |
| | `transcript-in` / `transcript-digest` — 会議の書き起こしの取り込みとタスク化 | **要 Drive フォルダID** |
| | `mirror` — 「自分を再現する AI」の再現テスト（出力の封・進行の封） | **要判断軸ファイル** |
| **環境を整える・その他** | `health-check` — silent failure（静かに壊れる障害）を検査してカード化。修復はしない | 不要 |
| | `inbox-triage` — 溜まったメモをトリアージして振り分ける | 不要 |
| | `explain-code` — アナロジーと ASCII 図でコードを説明 | 不要 |
| | `weather` — 天気・気温・降水確率 | 不要 |

**セットアップ不要のものから試すのを勧めます。**
`explain-code` と `weather` は置くだけで動きます。

## ⚠️ Slack / Notion 前提の9個は、個人環境では動きません

`council` ×4 / `team-digest` / `member-a・b・c-digest` / `meeting-digest` は、
**複数人が参加している Slack / Notion のワークスペース**と、そこにいるメンバーの ID が要ります。
自分ひとりの環境ではプレースホルダを埋めても意味のある結果が出ません。

残りの16個は、下のセットアップを済ませれば単独で使えます。

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
| `D_FIGMA_DM` | Figma アプリからの通知が来る Slack DM のチャンネルID | 同上 |
| `YOUR_FIGMA_FILE_KEY` | コメントを追いたい Figma ファイルの fileKey | `council` `council-figma` `team-digest` `member-*-digest` |
| `YOUR_DS_FILE_KEY` `YOUR_NODE_ID` | デザインシステムのファイルとノード | `ds-drift-audit` |
| `YOUR_DRIVE_FOLDER_ID` | 会議の記録が入っている Google Drive フォルダID | `transcript-in` |
| `your-org/...` | 自分の GitHub Organization とリポジトリ | `ds-drift-audit` `morning` |
| `Project Alpha` `Screen A` ほか | 自分の案件名・画面名（サンプル出力の中の例） | 多数（**置き換えなくても動く**） |

`Project Alpha` などは出力例の中の飾りなので、動作には影響しません。読みやすさのために直すだけです。

### 2. 値を用意するもの

| 何が必要か | どうするか |
|---|---|
| **`design-check` のパレット** | `skills/design-check/scripts/common.py` の `BRAND_COLORS` / `SYSTEM_COLORS` を自分のブランド色に差し替える。同梱の値は**中立なプレースホルダで、どこかの組織の色ではない**。差し替えないと色の観点だけ無意味な結果になる（重心・大きさ・カーニングの3観点は影響を受けない） |
| **`ds-drift-audit` のプリミティブ色** | 自分の Figma から `get_variable_defs` で取得し `figma_primitives.light.json` として置く（手順は `skills/ds-drift-audit/SKILL.md`）。同梱の `figma_primitives.sample.json` は**キーの形と値の型を示すだけの中立サンプル**で、実在する DS ではない |
| **`FIGMA_TOKEN`** | Figma の Personal Access Token。`figma-comment-map` が REST API を叩くのに使う |
| **`evaluation-axes.md`** | `~/.claude/rules/common/evaluation-axes.md` に自分の評価軸を書く。**雛形は [`templates/rules/evaluation-axes.md`](templates/rules/evaluation-axes.md)**。無ければ `council` の Role-Evaluator と `worklog-review` の軸別振り返りはスキップされる |
| `health-check` の memory 参照ずれ検査（任意） | `scripts/memory-ref-check.py` が使う値を環境変数で渡す。`MEMORY_REF_CHECK_DIR`（検査する memory ディレクトリ。未指定なら `~/.claude/projects/` 配下を自動で探す）/ `MEMORY_REF_CHECK_LAUNCHD_PREFIX`（launchd ラベルの接頭辞。既定 `jp.example`）/ `MEMORY_REF_CHECK_GH_ORG`（PR を突き合わせる GitHub Organization。既定 `your-org`）。**設定しなくても `health-check` の本体は動く** |

⚠️ **プラグインで入れた場合、パレットとプリミティブ色は「プラグインの中のファイルを編集する」形になります。**
プラグインの更新は新しいバージョンのディレクトリを作るため、**編集はそのとき引き継がれません**。
この2つを頻繁に使うなら、プラグインではなくファイルを直接置く方式を勧めます。

### 3. リポジトリに入っていないもの

| 依存 | 使っているスキル | 無いとどうなるか |
|---|---|---|
| `~/.claude/rules/common/philosophy.md` / `ui-design-principles.md` | `mirror` `figma-design-basics` | 判断軸の参照先が無い。自分の判断軸を書いて置き換える |
| MCP サーバー（Slack / Notion / Figma / Google Calendar / Google Drive） | 受信系、`transcript-in`、`morning` の予定取得 | そのスキルは動かない。`morning` は予定取得の節だけスキップして続行する設計 |

## 中身

```
claude-skills/
├ .claude-plugin/     プラグインとマーケットプレイスの定義
├ hooks/              初回起動時に board/ とヘルパーを用意する SessionStart hook
├ scripts/            スキルが呼ぶヘルパー3本
├ skills/             スキル25個
└ templates/rules/    評価軸ファイルの雛形
```

## 注意

- **スキルはファイルを書き換えます。** どのスキルが書き込むかは [`skills/README.md`](skills/README.md) の「書き込み」列で確認してください。読み取り専用のものは気軽に試せます
- 日本語で書かれています。Claude Code 側の応答言語を日本語にしている前提の文面が混じっています
- macOS / Claude Code CLI 以外では試していません

## ライセンス

MIT
