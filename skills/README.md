# 打つ言葉 → 出てくるもの（個人スキル25個の対応表）

`~/.claude/skills/` に置く個人スキルの一覧。**「何ができるか」ではなく「何を打つと何がどこに出るか」で引く**ための表。

- ⚠️ **プラグインとして入れた場合は、下の「打つ言葉」の頭に `claude-skills:` が付く**
  （`/design-check` → `/claude-skills:design-check`）。下の表は、ファイルを
  `~/.claude/skills/` に直接置いた場合の表記で書いてある
- 「書き込み」列が **あり** のものは、実行するとファイルが増えるか書き換わる。**なし** のものは読むだけなので、気軽に打ってよい
- 日付の引数（`3d` `7d`）を取るものは、省略すると各スキルの既定値になる
- ⚠️ この表は 2026-09-02 に SKILL.md の実物から起こした。**スキルを足したら1行足す**

---

## チームからの受信を集める

| 打つ言葉 | 出てくるもの | いつ使うか | 書き込み |
|---|---|---|---|
| `/morning` | ターミナルに朝のまとめ（チーム受信・昨日やったこと・継続中・今日のTop3・未完了の引き継ぎ） | 朝いちばん | なし |
| `/standup` | ターミナルにボードの状況（完了・進行中・ブロック・Top3・Open Handoff） | 朝のキックオフ／夕方の振り返り | なし |
| `/team-digest 3d` | ターミナルに3名分の統合digest（Slackメンション＋Figmaコメント） | チームからの受信を一括で見る | **あり**（Council Activity・Board Task） |
| `/member-a-digest 3d` | ターミナルにMember Aからの受信 | Member Aの依頼だけ見たい | **あり**（Council Activity） |
| `/member-b-digest 3d` | ターミナルにMember Bからの受信 | Member Bの依頼だけ見たい | なし |
| `/member-c-digest 3d` | ターミナルにMember Cからの受信 | Member Cの依頼だけ見たい | なし |
| `/meeting-digest 3d` | ターミナルに会議ごとの決定事項・その背景・自分のタスク | 出ていない会議も含めてキャッチアップ | **あり**（Council Activity・Board Task） |
| `/council 3d` | ターミナルに10ロールの協議結果＋守備ライン＋Slack貼り付け用ドラフト。JSONも書き出す | 受信に優先度をつけて返信まで作る | **あり**（board の council 配下） |
| `/council-slack 3d` | ターミナルにSlackメンションの要約と返信ドラフト | Slackだけ単体で見る | なし |
| `/council-notion 3d` | ターミナルにNotionメンション・コメントの要約 | Notionだけ単体で見る | なし |
| `/council-figma 3d` | ターミナルにFigmaコメントの要約と返信ドラフト | Figmaだけ単体で見る | なし |

## デザインを見る・直す

| 打つ言葉 | 出てくるもの | いつ使うか | 書き込み |
|---|---|---|---|
| `/design-check ~/Downloads/banner.png` | HTMLレポート（Artifact）＋ターミナル要約。重心・大きさ・カーニング・色の4観点とパレット照合（**要セットアップ**: 自分のブランド色を登録する） | 画像・PDFを人に出す前 | なし |
| `/figma-ia-checker <FigmaのURL>` | ターミナルに10カテゴリ×スコアと課題・改善提案 | 画面の情報設計をチェックする | なし |
| `/figma-comment-map <FigmaのURL>` | HTML/PDFレポート（画面スクショにコメントピンを復元） | コメントが溜まったファイルを棚卸しする | なし |
| `figma-design-basics` | 着手前の調査／作図の原則／検品手順を読み込む | **Figmaに書き込む前に必ず読む**（打つのではなく読ませる） | なし |
| `/ds-drift-audit` | HTML/JSONレポートを `~/dev/digests/` に出力＋ターミナル要約（**要セットアップ**: 自分のDSからプリミティブ色を取得する） | コード側トークンとFigmaのDSがずれていないか見る | **あり**（`~/dev/digests/`） |

## 記録する・振り返る

| 打つ言葉 | 出てくるもの | いつ使うか | 書き込み |
|---|---|---|---|
| `/worklog` | 当日のワークログを四半期ファイルに追記 | 自動ロールアップの取りこぼしを補う | **あり**（memory の worklog 配下） |
| `/worklog goals` | 当期の目標を登録・更新 | 期のはじめ | **あり**（memory の worklog 配下） |
| `/worklog-review FY2026-Q2` | `~/dev/digests/goal-sheet-FY<年度>-Q<n>.md` に目標管理シートの下書き | 期末 | **あり**（出力のみ。ワークログ本体は書き換えない） |
| `/transcript-in ~/Downloads/<file>` | Meetの書き起こしをMarkdownで保存 | 書き起こしを取り込む | **あり**（board の transcripts 配下） |
| `/transcript-digest` | ターミナルに決定・理由・タスク。処理済みは記録して再処理しない | 取り込んだ書き起こしをタスクにする | **あり**（Activity・Task・処理済みログ） |
| `/mirror seal <場面>` | **出力の封**。予測を封をして保存。**解説は出さない**（実物が引きずられるため） | 自分が返信を書く前にAIの予測を残す | **あり**（board の mirror 配下） |
| `/mirror seal-plan <案件>` | **進行の封**。着手前に「最初に見るもの・作る範囲・作らないもの・付箋・誰に聞くか・いつ出すか」を保存 | **作図を始める前**（`figma-design-basics` Phase 1-6 で自動発火） | **あり**（board の mirror 配下） |
| `/mirror diff <実物>` | 一致・過剰・欠落・逆の4分類と、判断軸への落とし込み | 実物を出したあとに突き合わせる | **あり**（board の mirror 配下・ルール類） |
| `/mirror log` | 一致率の推移 | ときどき | なし |

## 環境を整える・その他

| 打つ言葉 | 出てくるもの | いつ使うか | 書き込み |
|---|---|---|---|
| `/health-check ./my-project` | 静かに壊れている箇所をカード化＋ターミナル要約。**修復はしない** | 動いているつもりの仕組みを疑う | **あり**（board の blocked 配下） |
| `/inbox-triage` | 振り分け提案を出して `<area>` 別のカードにする | inboxにメモが溜まったとき | **あり**（board の todo 配下） |
| `/explain-code` | アナロジーとASCII図でコードの説明 | 仕組みを理解したい・人に説明したい | なし |
| `/weather 東京` | 天気・気温・降水確率 | — | なし |

---

## 読むときの注意

- **同じ「digest」でも挙動が違う。** `member-a-digest` はCouncil Activityに書き出すが、`member-b-digest` と `member-c-digest` は書き出さない（2026-09-02 時点。実装の差であって意図的な設計かは未確認）
- **`figma-design-basics` だけはスラッシュで打つものではない。** Figmaに書き込む作業に入る前に読ませるレファレンス
- **`/mirror` は seal → diff の順で使う。** 実物を見る前に封をしないと測定にならない
