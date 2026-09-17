#!/usr/bin/env python3
"""memory の参照ずれ（silent failure）を検出する。検出のみ・修復はしない。

memory に書かれた「実物を指す主張」を機械抽出し、実物と突き合わせる。
何も壊れないまま誤った判断を生むタイプの障害なので、放っておくと気づけない。

  使い方:
    python3 ~/.claude/scripts/memory-ref-check.py          # パス・launchd・スキル
    python3 ~/.claude/scripts/memory-ref-check.py --pr     # PR 状態も（gh が要る・遅い）

  出典: 2026-09-04 の一斉点検。94ファイル中7件のずれを検出し、
        うち5件がファイル移動、2件が状態の腐りだった。
        除外条件はそのとき出た誤検出（全13件）から起こしている。
"""
import os, re, sys, subprocess, collections

HOME = os.path.expanduser("~")
# 検査対象の memory ディレクトリ。環境変数 MEMORY_REF_CHECK_DIR で指定する。
# 未指定なら ~/.claude/projects/ 配下の memory ディレクトリを自動で探す。
def _default_memory_dir():
    base = os.path.join(HOME, ".claude", "projects")
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            cand = os.path.join(base, name, "memory")
            if os.path.isdir(cand):
                return cand
    return os.path.join(base, "memory")

MEMORY = os.environ.get("MEMORY_REF_CHECK_DIR") or _default_memory_dir()

# worklog/ は追記専用（過去の記述を書き換えない）ため検査対象外
SKIP_DIRS = ("/worklog",)

# Claude Code の組み込みコマンド。skills/ に無くて正常
BUILTIN_COMMANDS = {
    "btw", "context", "doctor", "effort", "fork", "rewind", "mcp", "schedule",
    "clear", "help", "config", "cost", "usage", "usage-credits", "statusline",
    "compact", "init", "review", "resume", "agents", "artifacts", "tmp", "loop",
}

# 「その時だけ生成される／まだ作っていない」ことが正常なパス
EXPECTED_ABSENT = (
    "/.claude/plans/",                       # Claude Code が自動生成・古いものは消える
    "/.claude/board/inbox/ks-branch-main-detected.md",  # 異常検知時にだけ作られるカード
    "/.claude/board/design-os/",             # 未着手 Phase の予定地
    "/.claude/tmp/",                         # 実行時の一時領域
)

# これがあれば「本文が既にその不在を説明している」とみなし、別バケットへ。
# ⚠️ 見るのは該当行だけでなく「直前行・直近の見出し・ファイル冒頭12行」も含む。
# 行だけを見ると、見出しに「構想（着手していない）」と書いてある項目を拾ってしまう（2026-09-04 実測）。
ANNOTATED = ("削除済み", "着手していない", "構想", "予定地", "退避済み", "存在しない",
             "残っていない", "消え", "実測でパス修正", "書かない", "廃止", "撤収",
             "クローズ済み", "実害", "当時", "止まっていた", "使えない", "実体が無い",
             "実体は無い", "消失")

RE_PATH    = re.compile(r'`(~/(?:\.claude|Projects|Documents|dev|side-projects)/[^`\s]+)`')
# launchd ラベルの接頭辞。環境変数 MEMORY_REF_CHECK_LAUNCHD_PREFIX で指定する（例: jp.example）
LAUNCHD_PREFIX = os.environ.get("MEMORY_REF_CHECK_LAUNCHD_PREFIX", "jp.example")
RE_LAUNCHD = re.compile(r'`?(' + re.escape(LAUNCHD_PREFIX) + r'\.[A-Za-z0-9_.-]+)`?')
RE_SKILL   = re.compile(r'`/([a-z][a-z0-9-]{2,})`')
# PR を突き合わせる GitHub Organization。環境変数 MEMORY_REF_CHECK_GH_ORG で指定する
GH_ORG = os.environ.get("MEMORY_REF_CHECK_GH_ORG", "your-org")
RE_PR      = re.compile(r'github\.com/' + re.escape(GH_ORG) + r'/([A-Za-z0-9_.-]+)/pull/(\d+)')

# グロブ・プレースホルダは主張ではないので除外
RE_TEMPLATE = re.compile(r'[*<>]|YYYY|MM-DD|年度|日付|session_id')


def md_files(root):
    for dp, _dn, fn in os.walk(root):
        if any(s in dp for s in SKIP_DIRS):
            continue
        for f in fn:
            if f.endswith(".md"):
                yield os.path.join(dp, f)


def scan(root):
    hits = {"path": [], "launchd": [], "skill": [], "pr": []}
    for p in md_files(root):
        rel = os.path.relpath(p, root)
        lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
        head = "\n".join(lines[:12])          # frontmatter 直後の状態行を拾うため
        heading, prev = "", ""
        for i, line in enumerate(lines, 1):
            if line.startswith("#"):
                heading = line
            # 打ち消しの有無は「行・直前行・直近の見出し・ファイル冒頭」で判定する
            scope = "\n".join((line, prev, heading, head))
            prev = line
            ctx = (rel, i, line, scope)   # [2]=その行だけ / [3]=打ち消し判定用の広い範囲
            for m in RE_PATH.finditer(line):
                hits["path"].append((m.group(1), ctx))
            for m in RE_LAUNCHD.finditer(line):
                hits["launchd"].append((m.group(1), ctx))
            for m in RE_SKILL.finditer(line):
                hits["skill"].append((m.group(1), ctx))
            for m in RE_PR.finditer(line):
                hits["pr"].append((f"{m.group(1)}#{m.group(2)}", ctx))
    return hits


def bucket(found):
    """(要対応, 注記あり) に振り分ける"""
    need, noted = [], []
    for claim, ctx in found:
        (noted if any(a in ctx[3] for a in ANNOTATED) else need).append((claim, ctx))
    return need, noted


def check_paths(found):
    out = []
    for claim, ctx in found:
        if RE_TEMPLATE.search(claim):
            continue
        if any(e in claim.replace("~", "") for e in EXPECTED_ABSENT):
            continue
        real = claim.replace("~", HOME, 1).rstrip("/")
        if not os.path.exists(real):
            out.append((claim, ctx))
    return out


def check_launchd(found):
    try:
        listed = subprocess.run(["launchctl", "list"], capture_output=True,
                                text=True, timeout=20).stdout
    except Exception:
        return []
    out = []
    for claim, ctx in found:
        if claim.endswith(".plist"):      # ラベルでなくファイル名
            continue
        if claim not in listed:
            out.append((claim, ctx))
    return out


def check_skills(found):
    try:
        installed = set(os.listdir(os.path.join(HOME, ".claude/skills")))
    except OSError:
        return []
    out = []
    for claim, ctx in found:
        if claim in installed or claim in BUILTIN_COMMANDS:
            continue
        if "~/side-projects" in ctx[3]:   # 別環境にあるスキル。こちらに無くて正常
            continue
        out.append((claim, ctx))
    return out


def check_prs(found):
    out, cache = [], {}
    for claim, ctx in found:
        repo, num = claim.rsplit("#", 1)
        if claim not in cache:
            r = subprocess.run(
                ["gh", "pr", "view", num, "--repo", f"{GH_ORG}/{repo}",
                 "--json", "state", "--jq", ".state"],
                capture_output=True, text=True, timeout=30)
            cache[claim] = r.stdout.strip() or "取得失敗"
        state = cache[claim]
        # PR 番号そのものは腐らない。腐るのは同じ行に書かれた状態語のほう。
        # ⚠️ ここは ctx[2]（その行だけ）を見る。見出しやファイル冒頭まで見ると、
        #    過去の経緯として書かれた「Draft PR #30 で提出済み」を拾う（2026-09-04 実測）。
        stale = [w for w in ("Draft", "レビュー待ち", "OPEN", "未マージ", "マージ待ち")
                 if w in ctx[2]]
        if stale and state != "OPEN":
            out.append((f"{claim} は実際は {state} だが行に「{'/'.join(stale)}」と書かれている", ctx))
    return out


def report(title, rows, hint=""):
    print(f"\n=== {title}: {len(rows)} 件 ===")
    if hint and rows:
        print(f"  {hint}")
    seen = set()
    for claim, (f, i, line, scope) in rows:
        key = (claim, f, i)
        if key in seen:
            continue
        seen.add(key)
        print(f"  ✗ {claim}\n      {f}:{i}")


def main():
    want_pr = "--pr" in sys.argv
    hits = scan(MEMORY)

    n_files = sum(1 for _ in md_files(MEMORY))
    print(f"memory 参照ずれ検査 — 対象 {n_files} ファイル（worklog/ は追記専用のため除外）")

    results = {}
    for key, fn, label in (
        ("path", check_paths, "実在しないパス"),
        ("launchd", check_launchd, "登録されていない launchd ラベル"),
        ("skill", check_skills, "実体の無いスキル参照"),
    ):
        need, noted = bucket(fn(hits[key]))
        results[key] = (need, noted)
        report(f"{label}（要対応）", need)
        if noted:
            report(f"{label}（本文に注記あり・おそらく既知）", noted)

    if want_pr:
        need, noted = bucket(check_prs(hits["pr"]))
        results["pr"] = (need, noted)
        report("PR の状態と本文の食い違い（要対応）", need)
    else:
        print("\n（PR 検査は --pr で実行。gh が要る）")

    total = sum(len(v[0]) for v in results.values())
    print(f"\n{'─'*46}\n要対応 合計: {total} 件")
    if total:
        print("修復はしない。直すときは rules/common/memory-layers.md「腐らせない書き方」に従う")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
