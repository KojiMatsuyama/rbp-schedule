// tests/tiny_test.js — 外部依存なしの最小テストランナー。
// このリポジトリにはpackage.jsonもnpm依存もなく、Node v12のため
// 組み込みテストランナー（node:test、v18以降）も使えない。
// describe/it形式で書けて、失敗時は理由を表示し、最後に件数サマリを出す。
const assert = require('assert');

let currentSuite = '';
let passCount = 0;
let failCount = 0;
const failures = [];

function describe(name, fn) {
  const prevSuite = currentSuite;
  currentSuite = prevSuite ? `${prevSuite} > ${name}` : name;
  fn();
  currentSuite = prevSuite;
}

function it(name, fn) {
  const fullName = `${currentSuite} > ${name}`;
  try {
    fn();
    passCount++;
    console.log(`  ✓ ${fullName}`);
  } catch (err) {
    failCount++;
    failures.push({ name: fullName, err });
    console.log(`  ✗ ${fullName}`);
    console.log(`    ${err.message}`);
  }
}

function summary() {
  console.log('');
  console.log(`${passCount + failCount}件中 ${passCount}件成功、${failCount}件失敗`);
  if (failures.length > 0) {
    console.log('\n失敗したテスト:');
    failures.forEach(f => console.log(`  - ${f.name}: ${f.err.message}`));
    process.exitCode = 1;
  }
}

// vm.createContext() で作られた別レルムのArray/Objectは、メインコンテキストの
// Array.prototype/Object.prototypeと異なるため、assert.deepStrictEqualが
// 「構造は同じだが参照的に等価でない」として失敗する。中身の値だけを見たい場合は
// JSON.stringifyでシリアライズしてから比較する（値がJSON化可能である前提）。
function assertDeepEqualAcrossRealms(actual, expected, message) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  assert.strictEqual(a, e, message || `期待値と一致しません:\n  actual:   ${a}\n  expected: ${e}`);
}

module.exports = { describe, it, assert, assertDeepEqualAcrossRealms, summary };
