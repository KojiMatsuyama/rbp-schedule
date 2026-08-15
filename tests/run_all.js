// tests/run_all.js — tests/ 配下の test_*.js を全て順に実行する。
// 実行: node tests/run_all.js
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const testFiles = fs.readdirSync(__dirname)
  .filter(f => f.startsWith('test_') && f.endsWith('.js'))
  .sort();

let anyFailed = false;

for (const file of testFiles) {
  console.log(`\n=== ${file} ===`);
  try {
    const output = execFileSync(process.execPath, [path.join(__dirname, file)], { encoding: 'utf8' });
    process.stdout.write(output);
  } catch (err) {
    anyFailed = true;
    process.stdout.write(err.stdout || '');
    process.stderr.write(err.stderr || '');
  }
}

console.log('\n' + '='.repeat(40));
console.log(anyFailed ? '一部のテストファイルで失敗があります' : '全テストファイルが成功しました');
process.exitCode = anyFailed ? 1 : 0;
