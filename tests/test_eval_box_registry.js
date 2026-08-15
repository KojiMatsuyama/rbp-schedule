// tests/test_eval_box_registry.js — rbp/eval_box_registry.js を検証。
// 対象:
//   computeNextEvalBoxId が欠番を埋めず最大値+1を採番すること（正常系）
//   buildEvalBoxName がDISEASESの命名規則（"+"連結）に従うこと（正常系）
//   classifyAndRegisterVector の新規登録・冪等性・MODEL_DEFINITION_ERROR伝播（正常系・エッジケース）
//
// EB_VECTORS/EB_MATRIX/EB_NAMESはグローバルミューテーションを伴うため、
// テスト間の汚染を避けるためテストごとにloadApp()で新しいvmコンテキストを作る。
const { loadApp, evalInApp } = require('./load_app');
const { describe, it, assert, summary } = require('./tiny_test');

describe('computeNextEvalBoxId（正常系）', () => {
  it('欠番（EB-05, EB-10）を埋めず、最大番号+1を採番する', () => {
    const ctx = loadApp();
    const result = evalInApp(ctx, `computeNextEvalBoxId(['EB-01','EB-02','EB-22'])`);
    assert.strictEqual(result, 'EB-23');
  });

  it('既存IDが空でも例外にならず EB-01 を返す', () => {
    const ctx = loadApp();
    const result = evalInApp(ctx, `computeNextEvalBoxId([])`);
    assert.strictEqual(result, 'EB-01');
  });
});

describe('buildEvalBoxName（正常系）', () => {
  it('立っている次元のDISEASES名を"+"で連結する', () => {
    const ctx = loadApp();
    const result = evalInApp(ctx, `buildEvalBoxName([1,0,1,0,0,0,0,0,0,0])`);
    assert.strictEqual(result, '炭疽病+うどんこ病');
  });

  it('全て0のベクトルは空文字を返す', () => {
    const ctx = loadApp();
    const result = evalInApp(ctx, `buildEvalBoxName([0,0,0,0,0,0,0,0,0,0])`);
    assert.strictEqual(result, '');
  });
});

describe('classifyAndRegisterVector（正常系）', () => {
  it('既存EVAL_BOXと完全一致する入力は isNew=false を返し、EB_MATRIXは増えない', () => {
    const ctx = loadApp();
    const before = evalInApp(ctx, 'EB_MATRIX.length');
    // EB-01は[1,0,0,0,0,0,0,0,0,0]（data/eval_boxes.js）
    const result = evalInApp(ctx, `classifyAndRegisterVector([1,0,0,0,0,0,0,0,0,0])`);
    const after = evalInApp(ctx, 'EB_MATRIX.length');
    assert.strictEqual(result.status, 'OK');
    assert.strictEqual(result.isNew, false);
    assert.strictEqual(result.id, 'EB-01');
    assert.strictEqual(before, after, '既存BOXと一致する場合はEB_MATRIXへの追加が発生しないはず');
  });

  it('どのEVAL_BOXとも一致しない入力は新規登録され isNew=true になる', () => {
    const ctx = loadApp();
    // 全22EVAL_BOXの中に存在しない組み合わせ: アブラムシのみ単独(index=8)
    const before = evalInApp(ctx, 'EB_MATRIX.length');
    const result = evalInApp(ctx, `classifyAndRegisterVector([0,0,0,0,0,0,0,0,1,0])`);
    const after = evalInApp(ctx, 'EB_MATRIX.length');
    assert.strictEqual(result.status, 'OK');
    assert.strictEqual(result.isNew, true);
    assert.strictEqual(after, before + 1, '新規登録時はEB_MATRIXが1件増えるはず');
    assert.strictEqual(result.name, 'アブラムシ');
  });

  it('新規登録した直後に同じベクトルを再度渡すと、2回目は isNew=false になる（冪等性）', () => {
    const ctx = loadApp();
    const first = evalInApp(ctx, `classifyAndRegisterVector([0,0,0,0,0,0,0,0,1,0])`);
    const second = evalInApp(ctx, `classifyAndRegisterVector([0,0,0,0,0,0,0,0,1,0])`);
    assert.strictEqual(first.isNew, true);
    assert.strictEqual(second.isNew, false);
    assert.strictEqual(second.id, first.id, '2回目は同一IDに解決されるはず');
  });

  it('新規登録されたIDは欠番(EB-05/EB-10)を使わず最大+1で採番される', () => {
    const ctx = loadApp();
    const result = evalInApp(ctx, `classifyAndRegisterVector([0,0,0,0,0,0,0,0,1,0])`);
    assert.strictEqual(result.id, 'EB-23', '既存最大がEB-22なので新規はEB-23になるはず');
  });
});

describe('classifyAndRegisterVector（エッジケース）', () => {
  it('意図的に重複させたベクトルはMODEL_DEFINITION_ERRORを伝播し、新規登録しない', () => {
    const ctx = loadApp();
    // EB-01と同じベクトルをEB_VECTORS/EB_MATRIX/EB_NAMESへ直接重複登録してから判定する
    evalInApp(ctx, `
      (function() {
        EB_VECTORS['EB-DUP'] = [1,0,0,0,0,0,0,0,0,0];
        EB_MATRIX.push([1,0,0,0,0,0,0,0,0,0]);
        EB_NAMES['EB-DUP'] = '重複テスト';
      })()
    `);
    const before = evalInApp(ctx, 'EB_MATRIX.length');
    const result = evalInApp(ctx, `classifyAndRegisterVector([1,0,0,0,0,0,0,0,0,0])`);
    const after = evalInApp(ctx, 'EB_MATRIX.length');
    assert.strictEqual(result.status, 'MODEL_DEFINITION_ERROR');
    assert.strictEqual(before, after, 'MODEL_DEFINITION_ERROR時はEB_MATRIXへの追加が起きないはず');
  });
});

summary();
