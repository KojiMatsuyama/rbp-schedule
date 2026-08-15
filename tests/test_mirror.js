// tests/test_mirror.js — framework/mirror.js の matchExactBox を検証。
// 対象:
//   完全一致・0件（UNDEFINED）・2件以上（MODEL_DEFINITION_ERROR）の判定（正常系）
//   次元不一致の行を無視すること（エッジケース）
const { loadApp, evalInApp } = require('./load_app');
const { describe, it, assert, assertDeepEqualAcrossRealms, summary } = require('./tiny_test');

const ctx = loadApp();

describe('matchExactBox（正常系）', () => {
  it('完全一致する行が1件なら status=OK でそのindex/idを返す', () => {
    const result = evalInApp(ctx, `
      matchExactBox([1,0,1], [[1,0,0],[0,1,0],[1,0,1]], ['A','B','C'])
    `);
    assert.strictEqual(result.status, 'OK');
    assert.strictEqual(result.index, 2);
    assert.strictEqual(result.id, 'C');
  });

  it('完全一致する行が0件なら status=UNDEFINED', () => {
    const result = evalInApp(ctx, `
      matchExactBox([1,1,1], [[1,0,0],[0,1,0]], ['A','B'])
    `);
    assert.strictEqual(result.status, 'UNDEFINED');
  });

  it('完全一致する行が2件以上なら status=MODEL_DEFINITION_ERROR で全一致idを返す', () => {
    const result = evalInApp(ctx, `
      matchExactBox([1,0,0], [[1,0,0],[0,1,0],[1,0,0]], ['A','B','C'])
    `);
    assert.strictEqual(result.status, 'MODEL_DEFINITION_ERROR');
    assertDeepEqualAcrossRealms(result.matchedIds, ['A', 'C']);
  });
});

describe('matchExactBox（エッジケース）', () => {
  it('次元が異なる行は一致候補から除外される', () => {
    const result = evalInApp(ctx, `
      matchExactBox([1,0], [[1,0,0],[1,0]], ['A','B'])
    `);
    assert.strictEqual(result.status, 'OK');
    assert.strictEqual(result.id, 'B');
  });

  it('全て0のベクトルでも完全一致する行があればOKを返す', () => {
    const result = evalInApp(ctx, `
      matchExactBox([0,0,0], [[1,0,0],[0,0,0]], ['A','B'])
    `);
    assert.strictEqual(result.status, 'OK');
    assert.strictEqual(result.id, 'B');
  });

  it('空の行列を渡すとUNDEFINEDを返す', () => {
    const result = evalInApp(ctx, `matchExactBox([1,0,0], [], [])`);
    assert.strictEqual(result.status, 'UNDEFINED');
  });
});

summary();
