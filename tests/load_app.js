// tests/load_app.js — schedule_app.html と同一の読み込み順で
// framework/*.js → data/*.js → rbp/*.js をグローバルスコープにロードするテスト用ハーネス。
// このアプリはビルドツールを持たない静的HTML/JSサイトで、各ファイルは
// <script src="...">前提の非モジュールグローバルスクリプトのため、
// vmモジュールで同一コンテキストに読み込むことで本番と同じ依存関係を再現する。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');

const LOAD_ORDER = [
  'framework/engine.js',
  'framework/mirror.js',
  'framework/rbp_core.js',
  'data/diseases.js',
  'data/pesticides.js',
  'data/eval_boxes.js',
  'rbp/safety.js',
  'rbp/spec_matching.js',
  'rbp/spec_bridges.js',
  'rbp/prescription.js',
  'rbp/eval_box_registry.js',
];

function loadApp() {
  const ctx = { console, Infinity, Math, Object, Array, JSON, Date };
  vm.createContext(ctx);
  for (const rel of LOAD_ORDER) {
    const code = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    vm.runInContext(code, ctx, { filename: rel });
  }
  return ctx;
}

// vmコンテキストのトップレベルconst/functionはNodeオブジェクトのプロパティとして
// 直接アクセスできないため、コンテキスト内で式を実行して結果を取り出す。
function evalInApp(ctx, expression) {
  return vm.runInContext(expression, ctx, { filename: 'eval.js' });
}

module.exports = { loadApp, evalInApp, ROOT };
