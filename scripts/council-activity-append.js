#!/usr/bin/env node
// Council Activity タブ用フィードへの共通 append ヘルパー。
// digest スキル群・Claude Code 日次作業ロールアップなど複数の書き手から呼ばれる想定のため、
// スキーマ補完（id/ts/date）・重複排除・件数/日数キャップ・atomic write を一箇所に集約する。
// 何があっても呼び出し元（スキル/hook）を止めないよう、失敗時も exit 0 で抜ける（ログにのみ残す）。

const fs = require('fs');
const path = require('path');

const MAX_ENTRIES = 300;
const MAX_AGE_DAYS = 90;

main();

function main() {
  try {
    run();
  } catch (_) {
    // ここに来る想定は薄いが、run() 内で拾えなかった例外の最終防波堤。
  }
  process.exit(0);
}

function run() {
  const home = process.env.HOME || `/Users/${process.env.USER}`;
  const councilDir = path.join(home, '.claude', 'board', 'council');
  const logDir = path.join(councilDir, 'logs');
  const activityPath = path.join(councilDir, 'activity.json');

  let raw;
  try {
    raw = readInput();
  } catch (e) {
    log(logDir, `FAIL 入力読み取り失敗: ${e && e.message}`);
    return;
  }

  let entry;
  try {
    entry = JSON.parse(raw);
  } catch (e) {
    log(logDir, `FAIL entry JSON parse失敗: ${e && e.message}`);
    return;
  }

  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
    log(logDir, 'FAIL entry がオブジェクトではない');
    return;
  }

  const now = new Date();
  const normalized = normalizeEntry(entry, now);

  let data = readActivity(activityPath, logDir);
  data.entries = data.entries.filter((e) => e && e.id !== normalized.id);
  data.entries.unshift(normalized);
  data.entries = capEntries(data.entries, now);
  data.generated_at = now.toISOString();

  try {
    fs.mkdirSync(councilDir, { recursive: true });
    atomicWrite(activityPath, JSON.stringify(data, null, 2));
  } catch (e) {
    log(logDir, `FAIL activity.json 書き込み失敗: ${e && e.message}`);
    return;
  }

  log(logDir, `OK id=${normalized.id} kind=${normalized.kind} source=${normalized.source} entries=${data.entries.length}`);
}

function readInput() {
  const argIdx = process.argv.indexOf('--entry');
  if (argIdx !== -1 && process.argv[argIdx + 1] !== undefined) {
    return process.argv[argIdx + 1];
  }
  // 標準入力（同期読み取り。パイプ経由の想定でブロッキングでも問題ない）。
  try {
    return fs.readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

function normalizeEntry(entry, now) {
  const ts = typeof entry.ts === 'string' && entry.ts ? entry.ts : now.toISOString();
  const date = typeof entry.date === 'string' && entry.date ? entry.date : dateFromTs(ts);
  // id 未指定時のフォールバックはミリ秒＋乱数サフィックス。
  // team-digest 等が同一プロセスで複数エントリを連続 append しても衝突しないようにする
  // （worklog 側のように決定的 id を明示する呼び出しは、その id で重複排除される）。
  const id = typeof entry.id === 'string' && entry.id ? entry.id : `act_${now.getTime()}_${Math.random().toString(36).slice(2, 7)}`;
  return {
    id,
    ts,
    date,
    kind: entry.kind,
    source: entry.source,
    title: entry.title,
    summary: entry.summary,
    action_items: Array.isArray(entry.action_items) ? entry.action_items : [],
    project: typeof entry.project === 'string' ? entry.project : entry.project ?? null,
    links: Array.isArray(entry.links) ? entry.links : [],
  };
}

function dateFromTs(ts) {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return new Date().toISOString().slice(0, 10);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// 既存 activity.json を読む。無い/壊れている場合は握りつぶして空から作り直す（ログにのみ残す）。
function readActivity(activityPath, logDir) {
  if (!fs.existsSync(activityPath)) {
    return { generated_at: '', entries: [] };
  }
  try {
    const raw = fs.readFileSync(activityPath, 'utf8');
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.entries)) {
      throw new Error('entries が配列ではない');
    }
    return parsed;
  } catch (e) {
    log(logDir, `WARN 既存activity.json破損のため空から作り直し: ${e && e.message}`);
    return { generated_at: '', entries: [] };
  }
}

// 直近90日 かつ 最大300件でキャップ（古いものから捨てる）。entries は降順（新しいものが先頭）前提。
function capEntries(entries, now) {
  const cutoff = now.getTime() - MAX_AGE_DAYS * 24 * 60 * 60 * 1000;
  const withinAge = entries.filter((e) => {
    const t = new Date(e.ts).getTime();
    return Number.isNaN(t) ? true : t >= cutoff;
  });
  return withinAge.slice(0, MAX_ENTRIES);
}

function atomicWrite(filePath, content) {
  const tmpPath = `${filePath}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tmpPath, content);
  fs.renameSync(tmpPath, filePath);
}

function log(logDir, line) {
  try {
    fs.mkdirSync(logDir, { recursive: true });
    const ts = new Date().toISOString();
    fs.appendFileSync(path.join(logDir, 'activity-append.log'), `${ts} ${line}\n`);
  } catch (_) {
    // ログ自体の失敗は無視する（本体処理には影響させない）
  }
}
