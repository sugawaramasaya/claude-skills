#!/usr/bin/env node
// claude-skills プラグインの足回りを用意する SessionStart hook。
//
// いくつかのスキルは ~/.claude/ の外部構造に依存している（board のディレクトリ、
// Council のヘルパー2本）。新しいマシンにはこれが無いので、無ければここで作る。
//
// 原則:
//   - 既存のファイル・ディレクトリを絶対に上書きしない（無いときだけ作る）
//   - 何があってもセッションを止めない（失敗しても exit 0）
//   - 何も作らなかったときは何も言わない（毎回の起動でうるさくしない）

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const PLUGIN_ROOT = process.env.CLAUDE_PLUGIN_ROOT
  || path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');

const HOME = process.env.HOME || os.homedir();
const CLAUDE_DIR = path.join(HOME, '.claude');
const BOARD_DIR = path.join(CLAUDE_DIR, 'board');
const SCRIPTS_DIR = path.join(CLAUDE_DIR, 'scripts');

// スキルが読み書きするボードのディレクトリ
const BOARD_SUBDIRS = [
  'inbox',
  'todo',
  'blocked',
  'done',
  'in-progress',
  'mirror',
  'transcripts',
  'council',
  'worklog-index',
  'design-os',
];

// スキルが `node ~/.claude/scripts/<name>` の形で呼ぶヘルパー。
// プラグイン側の実体へ symlink を張って、配置方法（プラグイン / 手動コピー）に依らず同じパスで動くようにする。
const HELPERS = [
  'council-activity-append.js',
  'council-task-append.js',
  'memory-ref-check.py',
];

// レポートの出力先（design-check / ds-drift-audit / worklog-review などが書く）
const DIGESTS_DIR = path.join(HOME, 'dev', 'digests');

function main() {
  const created = [];

  // 1. board のディレクトリ
  for (const sub of BOARD_SUBDIRS) {
    const dir = path.join(BOARD_DIR, sub);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
      created.push(`~/.claude/board/${sub}/`);
    }
  }

  // 2. レポートの出力先
  if (!fs.existsSync(DIGESTS_DIR)) {
    fs.mkdirSync(DIGESTS_DIR, { recursive: true });
    created.push('~/dev/digests/');
  }

  // 3. ヘルパーへの symlink（既に何かあれば触らない）
  if (!fs.existsSync(SCRIPTS_DIR)) {
    fs.mkdirSync(SCRIPTS_DIR, { recursive: true });
  }
  for (const name of HELPERS) {
    const dest = path.join(SCRIPTS_DIR, name);
    const src = path.join(PLUGIN_ROOT, 'scripts', name);
    // lstat で見る。壊れた symlink も「ある」として扱い、触らない
    let exists = true;
    try {
      fs.lstatSync(dest);
    } catch {
      exists = false;
    }
    if (!exists && fs.existsSync(src)) {
      fs.symlinkSync(src, dest);
      created.push(`~/.claude/scripts/${name}`);
    }
  }

  if (created.length === 0) return; // 何も作らなかったので黙る

  const lines = [
    'claude-skills: 足回りを用意しました（既存のものは触っていません）。',
    ...created.map((c) => `  + ${c}`),
  ];

  // 一部のスキルは判断軸のファイルを読む。無ければ場所だけ知らせる（自動では作らない）
  const axes = path.join(CLAUDE_DIR, 'rules', 'common', 'evaluation-axes.md');
  if (!fs.existsSync(axes)) {
    lines.push(
      '  なお worklog / worklog-review / council は ~/.claude/rules/common/evaluation-axes.md を読みます。',
      `  雛形: ${path.join(PLUGIN_ROOT, 'templates/rules/evaluation-axes.md')}`
    );
  }

  console.log(lines.join('\n'));
}

try {
  main();
} catch {
  // セッションを止めない
}
process.exit(0);
