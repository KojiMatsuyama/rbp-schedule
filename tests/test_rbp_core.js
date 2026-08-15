// tests/test_rbp_core.js — framework/rbp_core.js の runLineThroughBridges を検証。
// 対象:
//   Hadamard積による通過・遮断・減衰の中間ベクトル（正常系）
//   一様重みベクトル前提が破られた場合の MODEL_DEFINITION_ERROR（エッジケース）
//   level/direction 制約違反時のエラー（エッジケース）
const { loadApp, evalInApp } = require('./load_app');
const { describe, it, assert, assertDeepEqualAcrossRealms, summary } = require('./tiny_test');

const ctx = loadApp();

describe('runLineThroughBridges（正常系）', () => {
  it('全BRIDGEを通過すると flow は初期値のまま、blocked=false', () => {
    const result = evalInApp(ctx, `
      runLineThroughBridges([1,1,0], [
        { id: 'B1', level: 1, direction: 'forward', weight_vector_fn: () => [1,1,1] },
        { id: 'B2', level: 2, direction: 'forward', weight_vector_fn: () => [1,1,1] },
      ], {})
    `);
    assertDeepEqualAcrossRealms(result.flow, [1, 1, 0]);
    assert.strictEqual(result.blocked, false);
    assert.strictEqual(result.blockedAt, null);
    assert.strictEqual(result.trace.length, 2);
    assert.strictEqual(result.trace[0].passed, true);
    assert.strictEqual(result.trace[0].attenuated, false);
  });

  it('途中のBRIDGEで全0ベクトルを返すと、その時点でblocked=trueになり以降は実行されない', () => {
    let laterBridgeCalled = false;
    ctx.laterBridgeCalled = false;
    const result = evalInApp(ctx, `
      runLineThroughBridges([1,1,1], [
        { id: 'B1', level: 1, direction: 'forward', weight_vector_fn: () => [0,0,0], reason_fn: () => '遮断理由' },
        { id: 'B2', level: 2, direction: 'forward', weight_vector_fn: () => { laterBridgeCalled = true; return [1,1,1]; } },
      ], {})
    `);
    laterBridgeCalled = evalInApp(ctx, 'laterBridgeCalled');
    assertDeepEqualAcrossRealms(result.flow, [0, 0, 0]);
    assert.strictEqual(result.blocked, true);
    assert.strictEqual(result.blockedAt, 'B1');
    assert.strictEqual(result.blockReason, '遮断理由');
    assert.strictEqual(laterBridgeCalled, false, '遮断後のBRIDGEは呼ばれないはず');
  });

  it('0<w<1の減衰ベクトルはHadamard積で正しく反映され、attenuated=trueになる', () => {
    const result = evalInApp(ctx, `
      runLineThroughBridges([1,1,1,1], [
        { id: 'B1', level: 1, direction: 'forward', weight_vector_fn: () => [0.5,0.5,0.5,0.5] },
      ], {})
    `);
    assertDeepEqualAcrossRealms(result.flow, [0.5, 0.5, 0.5, 0.5]);
    assert.strictEqual(result.blocked, false);
    assert.strictEqual(result.trace[0].attenuated, true);
    assert.strictEqual(result.trace[0].weight, 0.5);
  });

  it('levelの昇順とは無関係な配列順で渡しても、実行はlevel昇順で行われる', () => {
    const result = evalInApp(ctx, `
      (function() {
        const order = [];
        runLineThroughBridges([1], [
          { id: 'HIGH', level: 3, direction: 'forward', weight_vector_fn: () => { order.push('HIGH'); return [1]; } },
          { id: 'LOW', level: 1, direction: 'forward', weight_vector_fn: () => { order.push('LOW'); return [1]; } },
          { id: 'MID', level: 2, direction: 'forward', weight_vector_fn: () => { order.push('MID'); return [1]; } },
        ], {});
        return order;
      })()
    `);
    assertDeepEqualAcrossRealms(result, ['LOW', 'MID', 'HIGH']);
  });
});

describe('runLineThroughBridges（エッジケース：モデル定義違反）', () => {
  it('次元ごとに異なる重みを返すBRIDGEはMODEL_DEFINITION_ERRORとしてthrowされる', () => {
    assert.throws(() => {
      evalInApp(ctx, `
        runLineThroughBridges([1,1,1], [
          { id: 'NON-UNIFORM', level: 1, direction: 'forward', weight_vector_fn: () => [1, 0, 1] },
        ], {})
      `);
    }, /NON-UNIFORM.*一様重みベクトル必須/, '非一様weightは黙って先頭要素を使うのではなく例外にすべき');
  });

  it('直値0/false/空配列への暗黙変換ではなく、エラーメッセージに違反BRIDGEのIDが含まれる', () => {
    try {
      evalInApp(ctx, `
        runLineThroughBridges([1,1], [
          { id: 'BAD-BRIDGE-XYZ', level: 1, direction: 'forward', weight_vector_fn: () => [0.3, 0.7] },
        ], {})
      `);
      assert.fail('例外が発生するはずだった');
    } catch (err) {
      assert.ok(err.message.includes('BAD-BRIDGE-XYZ'), 'エラーメッセージに原因BRIDGEのIDが含まれるべき');
    }
  });

  it('directionが forward 以外だとエラー', () => {
    assert.throws(() => {
      evalInApp(ctx, `
        runLineThroughBridges([1], [
          { id: 'REVERSE', level: 1, direction: 'backward', weight_vector_fn: () => [1] },
        ], {})
      `);
    }, /forward.*必須/);
  });

  it('levelがstrictly increasingでないとエラー（循環なし制約）', () => {
    assert.throws(() => {
      evalInApp(ctx, `
        runLineThroughBridges([1], [
          { id: 'B1', level: 2, direction: 'forward', weight_vector_fn: () => [1] },
          { id: 'B2', level: 2, direction: 'forward', weight_vector_fn: () => [1] },
        ], {})
      `);
    }, /strictly increasing/);
  });
});

summary();
