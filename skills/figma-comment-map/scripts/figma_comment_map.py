#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Figma ファイルの全コメントを画面ごとに棚卸しし、HTML / PDF レポートを生成する。

Figma の Slack 連携は「自分がメンションされたコメント」しか通知しないため、
CC 外のコメントは Slack だけ見ていると永久に気づけない。このスクリプトは
REST API から全件を直接取得するので取りこぼしが出ない。

使い方:
    figma_comment_map.py <FigmaのURL または file key> [options]

主なオプション:
    --json-only            レポートを作らず threads.json だけ出す（1パス目）
    --notes notes.json     一行要約・ステータス上書きを流し込む（2パス目）
    --me HANDLE            自分の Figma handle（既定: FIGMA_ME 環境変数）
    --out DIR              出力先ディレクトリ（既定: カレント）
    --days N               直近 N 日のスレッドだけに絞る
    --pdf                  HTML と同時に PDF も書き出す
    --max-total-mb N       埋め込む画像の合計上限（既定 6MB・超えたら自動で圧縮）

トークンは環境変数 FIGMA_TOKEN → macOS キーチェーン（service 名 FIGMA_TOKEN）の順で探す。
"""

import argparse
import base64
import collections
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.figma.com"

# ---------------------------------------------------------------- ステータス定義

# 表示ラベルと配色キー。--notes で status を上書きするときもこのキーを使う。
STATUS = collections.OrderedDict([
    ("crit",    ("要対応・ブロック", "実装や進行が止まっている。自分が名指しで問われている")),
    ("act",     ("要対応",           "未返信。自分が返す番")),
    ("wait",    ("確認が必要",       "内容が読み取れない・判断材料が足りない")),
    ("replied", ("返信済み",         "自分が最後に発言していて、相手の反応待ち")),
    ("done",    ("決着済み",         "結論が出ている / Figma 上で解決済み")),
    ("ref",     ("経緯",             "議論の記録。いま動く必要はない")),
])
NEEDS_ACTION = ("crit", "act", "wait")
PRIORITY = {"crit": "★★★", "act": "★★", "wait": "★★"}

ASK_HINTS = ("?", "？", "お願い", "ください", "どうですか", "どうでしょう", "いかがで",
             "ですかね", "でしょうか", "ますか", "検討", "確認したい", "教えて")


# ---------------------------------------------------------------- 入出力の下ごしらえ

def log(msg):
    print(msg, file=sys.stderr)


def parse_file_key(s):
    """Figma の URL でも file key そのものでも受け付ける。"""
    m = re.search(r"figma\.com/(?:design|file|board)/([A-Za-z0-9]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{10,}", s):
        return s
    raise SystemExit(f"Figma の URL か file key を渡してください: {s!r}")


def get_token():
    tok = os.environ.get("FIGMA_TOKEN")
    if tok:
        return tok.strip()
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "FIGMA_TOKEN", "-w"],
            capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    raise SystemExit(
        "Figma のトークンが見つかりません。次のどちらかを用意してください:\n"
        "  export FIGMA_TOKEN=figd_xxx\n"
        "  security add-generic-password -s FIGMA_TOKEN -a $USER -w figd_xxx")


def api_get(path, token):
    req = urllib.request.Request(API + path, headers={"X-Figma-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"Figma API が {e.code} を返しました（{path}）\n{detail}")


# ---------------------------------------------------------------- ファイル構造

def fetch_structure(key, token, depth):
    """id -> {name, type, path, group, w, h} を作る。

    group は「最も近い SECTION の名前」。SECTION が無ければページ名。
    Figma では検討中の案をセクションで仕切るのが一般的なので、
    セクション単位でまとめるとそのまま検討フェーズの区切りになる。
    """
    doc = api_get(f"/v1/files/{key}?depth={depth}", token)
    info = {}

    def walk(node, path, group):
        b = node.get("absoluteBoundingBox") or {}
        info[node["id"]] = {
            "name": node.get("name", "(名称なし)"),
            "type": node.get("type", ""),
            "path": " / ".join(path),
            "group": group,
            "w": b.get("width") or 0,
            "h": b.get("height") or 0,
        }
        for child in node.get("children") or []:
            g = child.get("name", group) if child.get("type") == "SECTION" else group
            walk(child, path + [node.get("name", "")], g)

    for page in doc["document"]["children"]:
        walk(page, [], page.get("name", "ページ"))
    return doc.get("name", "Figma file"), info


# ---------------------------------------------------------------- 画像

def export_images(key, token, nodes, info, max_dim):
    """ノードごとに書き出し URL を取る。巨大なノードは縮小率を下げて取得する。"""
    buckets = collections.defaultdict(list)
    for nid in nodes:
        w, h = info[nid]["w"], info[nid]["h"]
        longest = max(w, h)
        if longest <= 0:
            continue
        if longest > 20000:            # ページ全体などはスクショが無意味なので諦める
            log(f"  スキップ（範囲が大きすぎます {int(w)}x{int(h)}）: {info[nid]['name']}")
            continue
        buckets[round(min(1.5, max_dim / longest), 3)].append(nid)

    urls = {}
    for scale, ids in buckets.items():
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            q = urllib.parse.quote(",".join(chunk))
            res = api_get(f"/v1/images/{key}?ids={q}&format=png&scale={scale}", token)
            if res.get("err"):
                log(f"  画像の書き出しに失敗（scale={scale}）: {res['err']}")
                continue
            urls.update({k: v for k, v in res["images"].items() if v})
    return urls


def _compress(src, dst, quality, max_dim):
    """sips（macOS）→ Pillow の順で JPEG 化。どちらも無ければ PNG のまま返す。"""
    if shutil.which("sips"):
        r = subprocess.run(
            ["sips", "-Z", str(max_dim), "-s", "format", "jpeg",
             "-s", "formatOptions", str(quality), src, "--out", dst],
            capture_output=True)
        if r.returncode == 0 and os.path.exists(dst):
            return dst, "image/jpeg"
    try:
        from PIL import Image  # noqa: PLC0415
        im = Image.open(src).convert("RGB")
        im.thumbnail((max_dim, max_dim))
        im.save(dst, "JPEG", quality=quality, optimize=True)
        return dst, "image/jpeg"
    except Exception:
        return src, "image/png"


def fetch_and_embed(urls, workdir, budget_bytes):
    """画像を落として data URI にする。合計が予算を超えるなら画質を落として再試行。"""
    os.makedirs(workdir, exist_ok=True)
    raw = {}
    for nid, url in urls.items():
        p = os.path.join(workdir, nid.replace(":", "-") + ".png")
        try:
            urllib.request.urlretrieve(url, p)
            raw[nid] = p
        except OSError as e:
            log(f"  画像のダウンロードに失敗: {nid} ({e})")

    for quality, max_dim in ((45, 900), (38, 900), (32, 760), (26, 640), (20, 520)):
        embedded, total = {}, 0
        for nid, src in raw.items():
            dst = os.path.join(workdir, nid.replace(":", "-") + f".q{quality}.jpg")
            out, mime = _compress(src, dst, quality, max_dim)
            data = open(out, "rb").read()
            total += len(data)
            embedded[nid] = f"data:{mime};base64," + base64.b64encode(data).decode()
        if total <= budget_bytes:
            log(f"  画像 {len(embedded)} 枚 / 合計 {total // 1024}KB（quality={quality}, 長辺{max_dim}px）")
            return embedded
        log(f"  画像が {total // 1024}KB で予算超過 → 画質を下げて再圧縮します")
    log("  ⚠ 予算内に収まりませんでした。最小画質のまま埋め込みます")
    return embedded


# ---------------------------------------------------------------- スレッド組み立て

def classify(root, replies, me):
    """機械的にステータスを決める。判断が要るものは --notes で上書きする前提。"""
    if root.get("resolved_at"):
        return "done"
    chain = [root] + replies
    last = chain[-1]
    last_is_me = me and me in last["user"]["handle"]
    mentioned = me and any(("@" + me) in (m.get("message") or "") for m in chain[-2:])
    text = last.get("message") or ""

    if last_is_me:
        return "replied"
    if mentioned:
        return "crit"
    if not replies:
        return "act"
    if any(h in text for h in ASK_HINTS):
        return "act"
    return "ref"


DEMOTE = {"crit": "act", "act": "ref", "wait": "ref"}


def mark_stale(threads, stale_days):
    """最終発言から離れているスレッドは一段下げる。

    古い未返信を要対応と同列に並べると、いま返すべきものが埋もれる。
    消さずに一段下げて「N日動きなし」を添えるだけにする。
    """
    if not stale_days:
        return
    limit = (datetime.now(timezone.utc) - timedelta(days=stale_days)).date().isoformat()
    today = datetime.now(timezone.utc).date()
    for t in threads:
        if t["last_activity"] >= limit or t["status"] not in DEMOTE:
            continue
        age = (today - datetime.fromisoformat(t["last_activity"]).date()).days
        t["status"] = DEMOTE[t["status"]]
        t["stale"] = age


def build_threads(comments, info, me, days, base_url):
    kids = collections.defaultdict(list)
    for c in comments:
        if c.get("parent_id"):
            kids[c["parent_id"]].append(c)
    roots = sorted([c for c in comments if not c.get("parent_id")],
                   key=lambda c: c["created_at"])

    cutoff = None
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    threads = []
    for n, c in enumerate(roots, 1):
        cm = c.get("client_meta") or {}
        nid = cm.get("node_id")
        if not nid or nid not in info:
            continue
        reps = sorted(kids[c["id"]], key=lambda x: x["created_at"])
        latest = (reps[-1] if reps else c)["created_at"]
        if cutoff and latest < cutoff:
            continue

        meta = info[nid]
        off = cm.get("node_offset") or {"x": 0, "y": 0}
        w = meta["w"] or 1
        h = meta["h"] or 1
        threads.append({
            "n": n,
            "id": c["id"],
            "node_id": nid,
            "screen": meta["name"],
            "path": meta["path"],
            "group": meta["group"],
            "status": classify(c, reps, me),
            "note": "",
            "author": c["user"]["handle"].replace("　", " "),
            "date": c["created_at"][:10],
            "time": c["created_at"][11:16],
            "last_activity": latest[:10],
            "stale": 0,
            "resolved": bool(c.get("resolved_at")),
            "text": (c.get("message") or "").strip(),
            "url": f"{base_url}?node-id={nid.replace(':', '-')}#{c['id']}",
            "px": max(1.5, min(98.5, off["x"] / w * 100)),
            "py": max(1.0, min(99.0, off["y"] / h * 100)),
            "replies": [{
                "author": r["user"]["handle"].replace("　", " "),
                "date": r["created_at"][:10],
                "text": (r.get("message") or "").strip(),
            } for r in reps],
        })
    return threads


def apply_notes(threads, path):
    """{"<comment id>": {"note": "...", "status": "crit"}} を流し込む。キーは T<n> でも可。"""
    notes = {k: v for k, v in json.load(open(path, encoding="utf-8")).items()
             if not k.startswith("_")}          # _readme などのメモ行は無視する
    hit = 0
    for t in threads:
        for key in (t["id"], f"T{t['n']}", str(t["n"])):
            if key in notes:
                item = notes[key]
                if isinstance(item, str):
                    item = {"note": item}
                if item.get("note"):
                    t["note"] = item["note"]
                if item.get("status") in STATUS:
                    t["status"] = item["status"]
                hit += 1
                break
    log(f"  notes を {hit} 件反映しました（全 {len(threads)} 件）")
    unknown = [k for k in notes
               if not any(k in (t["id"], f"T{t['n']}", str(t["n"])) for t in threads)]
    if unknown:
        log(f"  ⚠ 対応するスレッドが見つからないキー: {', '.join(unknown[:8])}")


# ---------------------------------------------------------------- HTML

CSS = """
:root {
  --paper:#E8E6E0; --card:#FCFBF9; --ink:#17191C; --soft:#6C6A62;
  --rule:#D3D0C8; --accent:#2E4057;
  --crit:#A5311F; --act:#8A5A12; --wait:#4E5460; --replied:#2E4057; --done:#3D6247; --ref:#8B8A83;
  --shadow:0 1px 2px rgba(23,25,28,.06), 0 8px 24px -14px rgba(23,25,28,.22);
  --mincho:"Hiragino Mincho ProN","Yu Mincho",YuMincho,"Songti SC",serif;
  --gothic:"Hiragino Sans","Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper:#15161A; --card:#1D1F24; --ink:#E9E7E1; --soft:#9B978C;
    --rule:#32353C; --accent:#9FB4CC;
    --crit:#E4715C; --act:#D5A452; --wait:#A7AFBD; --replied:#9FB4CC; --done:#7DB292; --ref:#7A7972;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -16px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"] {
  --paper:#15161A; --card:#1D1F24; --ink:#E9E7E1; --soft:#9B978C;
  --rule:#32353C; --accent:#9FB4CC;
  --crit:#E4715C; --act:#D5A452; --wait:#A7AFBD; --replied:#9FB4CC; --done:#7DB292; --ref:#7A7972;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -16px rgba(0,0,0,.7);
}
:root[data-theme="light"] {
  --paper:#E8E6E0; --card:#FCFBF9; --ink:#17191C; --soft:#6C6A62;
  --rule:#D3D0C8; --accent:#2E4057;
  --crit:#A5311F; --act:#8A5A12; --wait:#4E5460; --replied:#2E4057; --done:#3D6247; --ref:#8B8A83;
  --shadow:0 1px 2px rgba(23,25,28,.06), 0 8px 24px -14px rgba(23,25,28,.22);
}
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink); font-family:var(--gothic);
  font-size:15px; line-height:1.75; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1180px; margin:0 auto; padding:0 22px 96px; }
a { color:var(--accent); text-decoration-thickness:1px; text-underline-offset:2px; }
a:focus-visible, button:focus-visible, summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.mono { font-family:var(--mono); font-size:.78em; letter-spacing:.01em; font-variant-numeric:tabular-nums; }
.muted { color:var(--soft); }

header.top { padding:64px 0 34px; border-bottom:1px solid var(--rule); }
.eyebrow { font-family:var(--mono); font-size:11px; letter-spacing:.16em; text-transform:uppercase;
  color:var(--soft); margin:0 0 14px; }
h1 { font-family:var(--mincho); font-weight:600; font-size:clamp(30px,4.6vw,46px); line-height:1.24;
  margin:0 0 16px; text-wrap:balance; letter-spacing:.01em; }
.lede { max-width:64ch; margin:0; color:var(--soft); }
.stats { display:flex; flex-wrap:wrap; gap:0; margin:30px 0 0; border:1px solid var(--rule);
  background:var(--card); border-radius:2px; overflow:hidden; }
.stat { flex:1 1 130px; padding:16px 18px; border-right:1px solid var(--rule); }
.stat:last-child { border-right:0; }
.stat b { display:block; font-family:var(--mincho); font-size:30px; line-height:1.1;
  font-variant-numeric:tabular-nums; font-weight:600; }
.stat span { font-family:var(--mono); font-size:10.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--soft); }
.stat--crit b { color:var(--crit); }
.stat--act b { color:var(--act); }

.callout { margin:38px 0 0; padding:20px 22px; background:var(--card); border:1px solid var(--rule);
  border-left:3px solid var(--crit); border-radius:2px; }
.callout h2 { font-family:var(--gothic); font-size:14px; margin:0 0 8px; letter-spacing:.02em; }
.callout p { margin:0; max-width:74ch; }
.callout p + p { margin-top:10px; }

.block { margin:40px 0 0; }
.block h2 { font-family:var(--mincho); font-size:22px; margin:0 0 4px; font-weight:600; }
.block > p { margin:0 0 16px; color:var(--soft); }
.block ol { margin:0; padding:0; list-style:none; border-top:1px solid var(--rule); }
.block li { padding:11px 0 11px 2px; border-bottom:1px solid var(--rule); }
.block li a { font-weight:700; }

.legend { margin:40px 0 0; }
.legend h2 { font-family:var(--mincho); font-size:22px; margin:0 0 14px; font-weight:600; }
.legend ul { list-style:none; margin:0; padding:0; display:grid; gap:8px;
  grid-template-columns:repeat(auto-fit,minmax(268px,1fr)); }
.legend li { display:flex; gap:10px; align-items:baseline; }
.legend .desc { color:var(--soft); font-size:13.5px; }

.idx { margin:46px 0 0; }
.idx h2 { font-family:var(--mincho); font-size:22px; margin:0 0 14px; font-weight:600; }
.tablescroll { overflow-x:auto; border:1px solid var(--rule); background:var(--card); border-radius:2px; }
table { border-collapse:collapse; width:100%; min-width:560px; }
th, td { text-align:left; padding:9px 14px; border-bottom:1px solid var(--rule); vertical-align:middle; }
th { font-family:var(--mono); font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--soft); font-weight:400; }
tr:last-child td { border-bottom:0; }
td.num { font-family:var(--mono); font-variant-numeric:tabular-nums; color:var(--soft); width:64px; }
td.pills { width:118px; white-space:nowrap; }

.chip { display:inline-block; font-family:var(--mono); font-size:10.5px; letter-spacing:.04em;
  padding:2px 7px; border-radius:2px; margin-right:5px; border:1px solid currentColor; }
.chip--crit { color:var(--crit); } .chip--act { color:var(--act); }
.chip--wait { color:var(--wait); } .chip--replied { color:var(--replied); }
.chip--done { color:var(--done); } .chip--ref { color:var(--ref); }
.tag { font-family:var(--mono); font-size:10.5px; letter-spacing:.05em; padding:2px 7px;
  border-radius:2px; color:#FCFBF9; background:var(--ref); }
.tag--crit { background:var(--crit); } .tag--act { background:var(--act); }
.tag--wait { background:var(--wait); } .tag--replied { background:var(--replied); }
.tag--done { background:var(--done); }
.tag--ref { background:transparent; color:var(--soft); border:1px solid var(--rule); }

.groupband { margin:72px 0 26px; padding:22px 0 0; border-top:2px solid var(--ink); }
.groupband h2 { font-family:var(--mincho); font-size:clamp(22px,3vw,30px); margin:0 0 6px; font-weight:600; }
.groupband p { margin:0; color:var(--soft); max-width:72ch; }

.screen { display:grid; grid-template-columns:minmax(240px,308px) 1fr; gap:34px;
  padding:26px 0 34px; border-bottom:1px solid var(--rule); }
.shotcol { position:sticky; top:22px; align-self:start; }
.shotwrap { position:relative; border:1px solid var(--rule); background:var(--card);
  border-radius:3px; box-shadow:var(--shadow); overflow:hidden; }
.shotwrap img { display:block; width:100%; height:auto; }
.noshot { min-height:190px; display:grid; place-items:center; padding:24px; }
.noshot p { margin:0; color:var(--soft); font-size:13px; text-align:center; }
.shotcap { margin:9px 0 0; font-size:12.5px; display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
.pin { position:absolute; transform:translate(-50%,-50%); width:23px; height:23px;
  border-radius:50% 50% 50% 2px; border:1.5px solid var(--card); background:var(--ref); color:#fff;
  font-family:var(--mono); font-size:10.5px; line-height:1; display:grid; place-items:center;
  cursor:pointer; padding:0; box-shadow:0 1px 4px rgba(0,0,0,.35); transition:transform .12s ease; }
.pin:hover { transform:translate(-50%,-50%) scale(1.28); z-index:5; }
.pin--crit { background:var(--crit); } .pin--act { background:var(--act); }
.pin--wait { background:var(--wait); } .pin--replied { background:var(--replied); }
.pin--done { background:var(--done); } .pin--ref { background:var(--ref); opacity:.62; }
.pin.lit { transform:translate(-50%,-50%) scale(1.5); z-index:6;
  box-shadow:0 0 0 3px var(--paper), 0 0 0 5px currentColor; }

.thcol h3 { font-family:var(--mincho); font-size:20px; margin:0 0 2px; font-weight:600; text-wrap:balance; }
.path { margin:0 0 10px; color:var(--soft); }
.chips { margin:0 0 16px; }

.th { display:grid; grid-template-columns:30px 1fr; gap:0 12px; padding:14px 0;
  border-top:1px solid var(--rule); }
.th > header { display:contents; }
.th p, .th details { grid-column:2; }
.pinbtn { grid-column:1; grid-row:span 4; align-self:start; width:26px; height:26px;
  border-radius:50% 50% 50% 2px; border:0; background:var(--ref); color:#fff;
  font-family:var(--mono); font-size:11px; cursor:pointer; padding:0; }
.pinbtn.pin--crit { background:var(--crit); } .pinbtn.pin--act { background:var(--act); }
.pinbtn.pin--wait { background:var(--wait); } .pinbtn.pin--replied { background:var(--replied); }
.pinbtn.pin--done { background:var(--done); }
.pinbtn.pin--ref { background:transparent; color:var(--soft); border:1px solid var(--rule); }
.thmeta { grid-column:2; display:flex; flex-wrap:wrap; gap:9px; align-items:center; margin-bottom:5px; }
.thmeta .who { font-size:13.5px; }
.pri { font-size:11px; color:var(--act); letter-spacing:.06em; }
.meta { color:var(--soft); }
.tick { color:var(--done); font-size:13px; }
.jump { margin-left:auto; white-space:nowrap; }
.note { margin:0 0 8px; font-size:13.5px; color:var(--ink); font-family:var(--mincho);
  border-left:2px solid var(--rule); padding-left:11px; }
.th--crit .note { border-left-color:var(--crit); }
.th--act .note { border-left-color:var(--act); }
.body { margin:0; }
.th--ref .body, .th--done .body { color:var(--soft); }
.reps { margin:9px 0 0; font-size:14px; }
.reps summary { cursor:pointer; color:var(--soft); font-family:var(--mono); font-size:11.5px; letter-spacing:.03em; }
.reps ul { list-style:none; margin:10px 0 0; padding:0 0 0 13px; border-left:1px solid var(--rule); }
.reps li { margin:0 0 12px; }
.reps li p { margin:2px 0 0; }
.th.lit { background:color-mix(in srgb, var(--accent) 8%, transparent); }

footer { margin-top:56px; padding-top:22px; border-top:1px solid var(--rule); color:var(--soft); font-size:13px; }
@media (max-width:820px) {
  .screen { grid-template-columns:1fr; gap:20px; }
  .shotcol { position:static; max-width:330px; }
}
@media (prefers-reduced-motion:reduce) { * { transition:none !important; } }
@media print {
  :root { --paper:#FFF; --card:#FFF; --ink:#15171A; --soft:#5B5952; --rule:#C9C6BE;
    --accent:#2E4057; --crit:#9C2E1D; --act:#7E5210; --wait:#4A4F5A; --replied:#2E4057;
    --done:#38583F; --ref:#7E7D76; --shadow:none; }
  body { font-size:10.5px; }
  .wrap { max-width:none; padding:0 8mm 8mm; }
  header.top { padding-top:0; }
  .shotcol { position:static; }
  .screen { grid-template-columns:200px 1fr; gap:16px; }
  .th { break-inside:avoid; }
  .groupband { break-before:page; }
  .idx, .block, .callout, .legend { break-inside:avoid; }
  .jump { display:none; }
  a { color:var(--ink); text-decoration:none; }
}
"""

JS = """
(function () {
  var lit = [];
  function clear() { lit.forEach(function (el) { el.classList.remove('lit'); }); lit = []; }
  function light(el) { if (!el) return; el.classList.add('lit'); lit.push(el); }

  document.addEventListener('click', function (e) {
    var pin = e.target.closest('.pin[data-thread]');
    if (pin) {
      clear();
      var th = document.getElementById('t' + pin.dataset.thread);
      light(pin); light(th);
      if (th) th.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    var btn = e.target.closest('.pinbtn[data-goto]');
    if (btn) {
      clear();
      var screen = btn.closest('.screen');
      var target = screen && screen.querySelector('.pin[data-thread="' + btn.dataset.goto + '"]');
      light(target); light(btn.closest('.th'));
      if (target) target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  });

  document.addEventListener('mouseover', function (e) {
    var pin = e.target.closest('.pin[data-thread]');
    if (!pin) return;
    var th = document.getElementById('t' + pin.dataset.thread);
    if (th) th.classList.add('lit');
    pin.addEventListener('mouseleave', function () {
      if (th) th.classList.remove('lit');
    }, { once: true });
  });
})();
"""

URL_RE = re.compile(r"https?://[^\s　]+")


def esc(text):
    out = html.escape(text)

    def rep(m):
        u = m.group(0)
        label = "Figma を開く" if "figma.com" in u else (u[:40] + "…" if len(u) > 40 else u)
        return f'<a href="{u}" target="_blank" rel="noopener">{label}</a>'

    return URL_RE.sub(rep, out).replace("\n", "<br>")


def render(file_name, base_url, threads, images, info, me, out_path, extra_note=""):
    by_screen = collections.OrderedDict()
    for t in threads:
        by_screen.setdefault(t["node_id"], []).append(t)

    screens = []
    for nid, ths in by_screen.items():
        counts = collections.Counter(t["status"] for t in ths)
        screens.append({
            "nid": nid,
            "name": info[nid]["name"],
            "path": info[nid]["path"],
            "group": info[nid]["group"],
            "threads": ths,
            "counts": counts,
            "rank": (-counts["crit"], -counts["act"], -counts["wait"], -len(ths)),
        })

    groups = collections.OrderedDict()
    for s in screens:
        groups.setdefault(s["group"], []).append(s)
    for g in groups.values():
        g.sort(key=lambda s: s["rank"])
    ordered_groups = sorted(
        groups.items(),
        key=lambda kv: (-sum(s["counts"]["crit"] for s in kv[1]),
                        -sum(s["counts"]["act"] for s in kv[1]),
                        -sum(len(s["threads"]) for s in kv[1])))

    def chips_for(counts):
        out = ""
        for k in ("crit", "act", "wait"):
            if counts[k]:
                out += f'<span class="chip chip--{k}">{STATUS[k][0]} {counts[k]}</span>'
        if not out:
            done = counts["done"] + counts["replied"] + counts["ref"]
            out = f'<span class="chip chip--done">対応不要 {done}</span>'
        return out

    def thread_html(t):
        label = STATUS[t["status"]][0]
        note = f'<p class="note">{html.escape(t["note"])}</p>' if t["note"] else ""
        reps = ""
        if t["replies"]:
            items = "\n".join(
                f'<li><span class="mono meta">{r["date"]}</span> <b>{html.escape(r["author"])}</b>'
                f'<p>{esc(r["text"])}</p></li>' for r in t["replies"])
            last = t["replies"][-1]
            reps = (f'<details class="reps"><summary>返信 {len(t["replies"])}件 — 最後は '
                    f'{html.escape(last["author"])}（{last["date"]}）</summary>'
                    f"<ul>{items}</ul></details>")
        tick = '<span class="tick" title="Figma 上で解決済み">✓</span>' if t["resolved"] else ""
        stale = (f'<span class="chip chip--ref">{t["stale"]}日動きなし</span>'
                 if t.get("stale") else "")
        return f"""<article class="th th--{t['status']}" id="t{t['n']}">
      <header>
        <button class="pinbtn pin--{t['status']}" data-goto="{t['n']}"
          aria-label="画面上のピン {t['n']} を示す">{t['n']}</button>
        <div class="thmeta">
          <span class="tag tag--{t['status']}">{label}</span>{tick}
          <span class="pri">{PRIORITY.get(t['status'], '★')}</span>
          <span class="mono meta">{t['date']} {t['time']}</span>
          <b class="who">{html.escape(t['author'])}</b>{stale}
          <a class="mono jump" href="{t['url']}" target="_blank" rel="noopener">Figma ↗</a>
        </div>
      </header>
      {note}
      <p class="body">{esc(t['text'])}</p>
      {reps}
    </article>"""

    def screen_html(s):
        sid = s["nid"].replace(":", "-")
        pins = "\n".join(
            f'<button class="pin pin--{t["status"]}" style="left:{t["px"]:.2f}%;top:{t["py"]:.2f}%" '
            f'data-thread="{t["n"]}" aria-label="コメント {t["n"]}：{html.escape(t["author"])}">'
            f'{t["n"]}</button>' for t in s["threads"])
        if images.get(s["nid"]):
            shot = (f'<div class="shotwrap"><img src="{images[s["nid"]]}" '
                    f'alt="{html.escape(s["name"])}">{pins}</div>')
        else:
            shot = ('<div class="shotwrap noshot"><p>この範囲は大きすぎるため'
                    f'スクリーンショットを省略しています。</p>{pins}</div>')
        return f"""<section class="screen" id="s-{sid}">
    <div class="shotcol">
      {shot}
      <p class="shotcap"><a href="{base_url}?node-id={sid}" target="_blank" rel="noopener">Figma で開く ↗</a>
        <span class="mono muted">{s['nid']}</span></p>
    </div>
    <div class="thcol">
      <h3>{html.escape(s['name'])}</h3>
      <p class="path mono">{html.escape(s['path'])}</p>
      <p class="chips">{chips_for(s['counts'])}</p>
      {''.join(thread_html(t) for t in s['threads'])}
    </div>
  </section>"""

    body = []
    for gname, ss in ordered_groups:
        n_th = sum(len(s["threads"]) for s in ss)
        need = sum(s["counts"][k] for s in ss for k in NEEDS_ACTION)
        body.append(f"""<div class="groupband">
  <h2>{html.escape(gname)}</h2>
  <p>要対応 {need} 件。<span class="mono muted">画面 {len(ss)} / スレッド {n_th}</span></p>
</div>""")
        body.extend(screen_html(s) for s in ss)

    total = collections.Counter(t["status"] for t in threads)
    n_comments = sum(1 + len(t["replies"]) for t in threads)

    action = sorted([t for t in threads if t["status"] in NEEDS_ACTION],
                    key=lambda t: (NEEDS_ACTION.index(t["status"]), t["n"]))
    action_items = "\n".join(
        f'<li><a href="#t{t["n"]}"><span class="mono">T{t["n"]}</span></a> '
        f'{html.escape(t["note"] or t["text"][:64].replace(chr(10), " "))} '
        f'<span class="muted">— {html.escape(t["author"])} / {html.escape(t["screen"])}</span></li>'
        for t in action[:24])

    idx_rows = "\n".join(
        f"""<tr>
      <td><a href="#s-{s['nid'].replace(':', '-')}">{html.escape(s['name'])}</a></td>
      <td class="muted">{html.escape(s['group'])}</td>
      <td class="num">{len(s['threads'])}</td>
      <td class="pills">{chips_for(s['counts'])}</td>
    </tr>"""
        for _, ss in ordered_groups for s in ss)

    legend = "\n".join(
        f'<li><span class="tag tag--{k}">{v[0]}</span><span class="desc">{v[1]}</span></li>'
        for k, v in STATUS.items())

    me_label = html.escape(me) if me else "（未指定）"
    extra = f'<p>{html.escape(extra_note)}</p>' if extra_note else ""

    doc = f"""<title>{html.escape(file_name)} — Figma コメント棚卸し</title>
<style>{CSS}</style>
<div class="wrap">
<header class="top">
  <p class="eyebrow">Figma コメント棚卸し</p>
  <h1>{html.escape(file_name)}<br>コメント {len(threads)} スレッドを画面ごとに整理</h1>
  <p class="lede">Figma REST API から全コメントを取得し、ピンが打たれた画面に貼り直しています。
  ピンの番号は右側のスレッド番号（T◯）と対応し、クリックすると該当スレッドへ移動します。
  基準ユーザー: <span class="mono">{me_label}</span></p>
  <div class="stats">
    <div class="stat"><b>{n_comments}</b><span>コメント</span></div>
    <div class="stat"><b>{len(threads)}</b><span>スレッド</span></div>
    <div class="stat"><b>{len(screens)}</b><span>画面・範囲</span></div>
    <div class="stat stat--crit"><b>{total['crit']}</b><span>名指しで未回答</span></div>
    <div class="stat stat--act"><b>{total['act'] + total['wait']}</b><span>要対応</span></div>
    <div class="stat"><b>{total['done'] + total['replied'] + total['ref']}</b><span>対応不要</span></div>
  </div>

  <div class="callout">
    <h2>Slack 通知だけでは足りない理由</h2>
    <p>Figma の Slack 連携は「自分がメンションされたコメント」と「自分が参加しているスレッド」しか通知しません。
    CC 外のコメントは通知が来ないため、Slack だけを見ていると気づけません。
    このレポートは REST API から全件を直接取得しているので欠けがありません。</p>
    {extra}
  </div>

  <div class="block">
    <h2>対応が必要な {len(action)} 件</h2>
    <p>返信が止まっている、または名指しで問われているスレッド。{'上位 24 件を表示。' if len(action) > 24 else ''}</p>
    <ol>{action_items or '<li class="muted">対応が必要なスレッドはありません。</li>'}</ol>
  </div>

  <div class="legend">
    <h2>ステータスの見方</h2>
    <ul>{legend}</ul>
  </div>

  <div class="idx">
    <h2>画面インデックス</h2>
    <div class="tablescroll">
      <table>
        <thead><tr><th>画面</th><th>セクション</th><th>スレッド</th><th>状態</th></tr></thead>
        <tbody>
{idx_rows}
        </tbody>
      </table>
    </div>
  </div>
</header>

{''.join(body)}

<footer>
  <p>Figma REST API <span class="mono">/v1/files/&lt;key&gt;/comments</span> から
  {datetime.now().strftime('%Y-%m-%d %H:%M')} 時点の全件を取得して生成。
  ステータスは発言順・メンション・解決フラグからの自動判定です（<span class="mono">--notes</span> で上書き可）。
  ✓ が付いているものだけが Figma 上で解決済みになっています。</p>
</footer>
</div>
<script>{JS}</script>
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


# ---------------------------------------------------------------- PDF

CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]


def to_pdf(html_path, pdf_path):
    chrome = next((c for c in CHROMES if os.path.exists(c)), None) or shutil.which("chromium")
    if not chrome:
        log("  Chrome 系ブラウザが見つからないため PDF は生成しません")
        return None
    # PDF では返信を開いた状態にする（畳んだ details は印刷されない）
    src = open(html_path, encoding="utf-8").read()
    tmp = html_path + ".forpdf.html"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(src.replace('<details class="reps">', '<details class="reps" open>'))
    r = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         "--virtual-time-budget=20000", f"--print-to-pdf={pdf_path}",
         "file://" + urllib.parse.quote(os.path.abspath(tmp))],
        capture_output=True)
    os.remove(tmp)
    if os.path.exists(pdf_path):
        return pdf_path
    log(f"  PDF の生成に失敗しました: {r.stderr.decode('utf-8', 'replace')[:200]}")
    return None


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Figma のコメントを画面ごとに棚卸しする")
    ap.add_argument("target", help="Figma の URL または file key")
    ap.add_argument("--me", default=os.environ.get("FIGMA_ME", ""),
                    help="自分の Figma handle（要対応の判定に使う）")
    ap.add_argument("--out", default=".", help="出力先ディレクトリ")
    ap.add_argument("--slug", default="", help="出力ファイル名の接頭辞（既定: Figma のファイル名）")
    ap.add_argument("--days", type=int, default=0, help="直近 N 日に動きがあったスレッドだけ")
    ap.add_argument("--stale-days", type=int, default=21,
                    help="この日数より古い未返信は一段下げる（0 で無効）")
    ap.add_argument("--depth", type=int, default=5, help="ファイル構造を読む深さ")
    ap.add_argument("--json-only", action="store_true", help="threads.json だけ出して終了")
    ap.add_argument("--notes", default="", help="一行要約・ステータス上書きの JSON")
    ap.add_argument("--pdf", action="store_true", help="PDF も生成する")
    ap.add_argument("--max-total-mb", type=float, default=6.0, help="埋め込む画像の合計上限")
    ap.add_argument("--max-dim", type=int, default=900, help="画像の長辺 px")
    args = ap.parse_args()

    key = parse_file_key(args.target)
    token = get_token()
    os.makedirs(args.out, exist_ok=True)

    log(f"■ ファイル {key} のコメントを取得します")
    comments = api_get(f"/v1/files/{key}/comments", token).get("comments", [])
    log(f"  コメント {len(comments)} 件")
    if not comments:
        raise SystemExit("コメントが 1 件もありません。file key と権限を確認してください。")

    file_name, info = fetch_structure(key, token, args.depth)
    base_url = f"https://www.figma.com/design/{key}/{urllib.parse.quote(file_name)}"
    threads = build_threads(comments, info, args.me, args.days, base_url)
    log(f"  スレッド {len(threads)} 件 / 画面 {len({t['node_id'] for t in threads})} 面")
    if not threads:
        raise SystemExit("対象スレッドがありません（--days を広げてください）。")
    mark_stale(threads, args.stale_days)
    n_stale = sum(1 for t in threads if t.get("stale"))
    if n_stale:
        log(f"  うち {n_stale} 件は {args.stale_days} 日以上動きがないため一段下げました")

    if args.notes:
        apply_notes(threads, args.notes)

    slug = args.slug or re.sub(r"[^\w぀-ヿ一-鿿-]+", "_", file_name)[:40]
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = os.path.join(args.out, f"{slug}_threads.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"file_name": file_name, "file_key": key, "base_url": base_url,
                   "me": args.me, "threads": threads}, f, ensure_ascii=False, indent=1)
    log(f"  → {json_path}")

    counts = collections.Counter(t["status"] for t in threads)
    log("  内訳: " + " / ".join(f"{STATUS[k][0]} {counts[k]}" for k in STATUS if counts[k]))

    if args.json_only:
        print(json_path)
        return

    log("■ 画面のスクリーンショットを書き出します")
    node_ids = list({t["node_id"] for t in threads})
    urls = export_images(key, token, node_ids, info, args.max_dim)
    images = fetch_and_embed(urls, os.path.join(args.out, ".figma_shots"),
                             int(args.max_total_mb * 1024 * 1024))

    html_path = os.path.join(args.out, f"{slug}_コメント棚卸し_{stamp}.html")
    render(file_name, base_url, threads, images, info, args.me, html_path)
    log(f"■ 完成: {html_path}（{os.path.getsize(html_path) // 1024}KB）")
    outs = [html_path]

    if args.pdf:
        pdf_path = html_path[:-5] + ".pdf"
        if to_pdf(html_path, pdf_path):
            log(f"■ 完成: {pdf_path}（{os.path.getsize(pdf_path) // 1024}KB）")
            outs.append(pdf_path)

    shutil.rmtree(os.path.join(args.out, ".figma_shots"), ignore_errors=True)
    for p in outs:
        print(p)


if __name__ == "__main__":
    main()
