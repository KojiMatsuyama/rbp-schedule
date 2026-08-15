// tests/test_data_integrity.js — data/*.js の次元整合性を検証。
// P_matrix相当（各薬剤のtargetVector）やEB_MATRIXの次元がDISEASESの次元数と
// 食い違っていないかは、行列演算（内積・Hadamard積）の前提となる不変条件。
// ここが崩れるとMODEL_DEFINITION_ERRORの検出以前に単純な計算間違いを生むため、
// 実行時アサーションとして分離しておく。
const { loadApp, evalInApp } = require('./load_app');
const { describe, it, assert, summary } = require('./tiny_test');

const ctx = loadApp();

describe('次元整合性（正常系）', () => {
  it('DISEASESの次元数は10', () => {
    const len = evalInApp(ctx, 'DISEASES.length');
    assert.strictEqual(len, 10);
  });

  it('全67薬剤のtargetVectorはDISEASESと同じ10次元', () => {
    const result = evalInApp(ctx, `
      PESTICIDE_DB.map(p => ({ id: p.id, len: p.targetVector.length }))
        .filter(x => x.len !== DISEASES.length)
    `);
    assert.strictEqual(result.length, 0, `次元不一致の薬剤: ${JSON.stringify(result)}`);
  });

  it('全22 EVAL_BOXベクトルはDISEASESと同じ10次元', () => {
    const result = evalInApp(ctx, `
      Object.entries(EB_VECTORS)
        .map(([id, v]) => ({ id, len: v.length }))
        .filter(x => x.len !== DISEASES.length)
    `);
    assert.strictEqual(result.length, 0, `次元不一致のEVAL_BOX: ${JSON.stringify(result)}`);
  });

  it('PESTICIDE_DBは67件', () => {
    const len = evalInApp(ctx, 'PESTICIDE_DB.length');
    assert.strictEqual(len, 67);
  });

  it('全薬剤のtargetVectorは0/1のみで構成される（連続値が紛れ込んでいない）', () => {
    const result = evalInApp(ctx, `
      PESTICIDE_DB.filter(p => !p.targetVector.every(v => v === 0 || v === 1)).map(p => p.id)
    `);
    assert.strictEqual(result.length, 0, `0/1以外の値を持つ薬剤: ${JSON.stringify(result)}`);
  });

  it('全薬剤IDは一意（PESTICIDE_INDEX_BY_IDの前提）', () => {
    const result = evalInApp(ctx, `
      (function() {
        const ids = PESTICIDE_DB.map(p => p.id);
        return ids.length === new Set(ids).size;
      })()
    `);
    assert.strictEqual(result, true, 'ID重複があるとPESTICIDE_INDEX_BY_IDの対応が壊れる');
  });

  it('EVAL_BOXベクトルはペアワイズに重複していない（matchExactBoxのMODEL_DEFINITION_ERROR前提）', () => {
    const dup = evalInApp(ctx, `
      (function() {
        const ids = Object.keys(EB_VECTORS);
        const dups = [];
        for (let i = 0; i < ids.length; i++) {
          for (let j = i + 1; j < ids.length; j++) {
            const a = EB_VECTORS[ids[i]], b = EB_VECTORS[ids[j]];
            if (a.length === b.length && a.every((x, k) => x === b[k])) {
              dups.push([ids[i], ids[j]]);
            }
          }
        }
        return dups;
      })()
    `);
    assert.strictEqual(dup.length, 0, `重複するEVAL_BOXベクトルが存在する: ${JSON.stringify(dup)}`);
  });
});

summary();
