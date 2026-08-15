// tests/test_safety.js — rbp/safety.js の SafetyVector 算出を検証。
// computeRotationState は firstIteration フラグ方式から reduce ベースの
// 連続run長計算に書き換えたため、複数系統・脱落パターンでの一致を重点的に検証する。
const { loadApp, evalInApp } = require('./load_app');
const { describe, it, assert, assertDeepEqualAcrossRealms, summary } = require('./tiny_test');

const ctx = loadApp();

// 実データのPESTICIDE_DBに依存しない、テスト用の最小データで検証したいが、
// safety.jsはグローバルのPESTICIDE_DBを参照するため、実データから
// systemCodeが既知の薬剤ID（P01=FRAC11系, P02=FRAC-QUINOX系）を使う。
describe('computeRotationState（正常系）', () => {
  it('記録が0件なら空オブジェクトを返す（暗黙の0/false変換ではなく明示的な空状態）', () => {
    const result = evalInApp(ctx, `computeRotationState({}, '2026-08-15')`);
    assertDeepEqualAcrossRealms(result, {});
  });

  it('同一系統(P01=FRAC11)を3回連続で使うと連続run長3になる', () => {
    const result = evalInApp(ctx, `
      computeRotationState({
        '2026-08-01': { pesticideIds: ['P01'] },
        '2026-08-03': { pesticideIds: ['P01'] },
        '2026-08-05': { pesticideIds: ['P01'] },
      }, '2026-08-10')
    `);
    assertDeepEqualAcrossRealms(result, { FRAC11: 3 });
  });

  it('直近散布日に系統が使われなければ、そこでrunが途切れて古い使用は数えない', () => {
    // P02(FRAC-QUINOX)を最新日に使い、その前にP01(FRAC11)を2回使っていても
    // FRAC11は直近runの起点になっていないためカウントされない
    const result = evalInApp(ctx, `
      computeRotationState({
        '2026-08-01': { pesticideIds: ['P01'] },
        '2026-08-03': { pesticideIds: ['P01'] },
        '2026-08-05': { pesticideIds: ['P02'] },
      }, '2026-08-10')
    `);
    assertDeepEqualAcrossRealms(result, { 'FRAC-QUINOX': 1 });
  });

  it('lookbackCount（既定5件）を超える古い記録は無視される', () => {
    const result = evalInApp(ctx, `
      computeRotationState({
        '2026-07-01': { pesticideIds: ['P01'] },
        '2026-07-05': { pesticideIds: ['P01'] },
        '2026-07-10': { pesticideIds: ['P01'] },
        '2026-07-15': { pesticideIds: ['P01'] },
        '2026-07-20': { pesticideIds: ['P01'] },
        '2026-07-25': { pesticideIds: ['P01'] },
      }, '2026-08-10')
    `);
    assertDeepEqualAcrossRealms(result, { FRAC11: 5 }, '直近5件のみが対象なので6件目は無視されるはず');
  });
});

describe('computeRotationState（エッジケース）', () => {
  it('targetDateStr当日または未来の記録は対象に含まれない', () => {
    const result = evalInApp(ctx, `
      computeRotationState({
        '2026-08-10': { pesticideIds: ['P01'] },
        '2026-08-15': { pesticideIds: ['P01'] },
      }, '2026-08-10')
    `);
    assertDeepEqualAcrossRealms(result, {}, 'targetDateと同日・未来日は「対象日より前」ではないので除外されるべき');
  });

  it('pesticideIdsが空配列の記録は無視される', () => {
    const result = evalInApp(ctx, `
      computeRotationState({
        '2026-08-01': { pesticideIds: [] },
        '2026-08-05': { pesticideIds: ['P01'] },
      }, '2026-08-10')
    `);
    assertDeepEqualAcrossRealms(result, { FRAC11: 1 });
  });
});

describe('buildSafetyVector（正常系・エッジケース）', () => {
  it('記録が空でも例外を投げず、全フィールドが定義された既定状態を返す', () => {
    const result = evalInApp(ctx, `buildSafetyVector({}, '2026-08-15')`);
    assert.strictEqual(result.lastSprayDate, null);
    assert.strictEqual(result.intervalDays, null);
    assertDeepEqualAcrossRealms(result.lastPesticideIds, []);
    assertDeepEqualAcrossRealms(result.usageState, {});
    assertDeepEqualAcrossRealms(result.rotationState, {});
  });

  it('直近散布記録からintervalDaysが正しく算出される', () => {
    const result = evalInApp(ctx, `
      buildSafetyVector({ '2026-08-01': { pesticideIds: ['P01'] } }, '2026-08-15')
    `);
    assert.strictEqual(result.intervalDays, 14);
    assertDeepEqualAcrossRealms(result.lastPesticideIds, ['P01']);
  });
});

summary();
