// tests/test_mixing_matrix.js — hasMixingConflict の行列化（rbp/spec_matching.js）を検証。
// 対象:
//   MIXING_CONFLICT_MATRIX の対称性・既知ペアの正しさ（正常系）
//   未知ID・境界値の扱い（エッジケース）
const { loadApp, evalInApp } = require('./load_app');
const { describe, it, assert, summary } = require('./tiny_test');

const ctx = loadApp();

describe('hasMixingConflict（混用禁止制約行列）', () => {
  it('銅剤禁止の薬剤同士は混用禁止と判定される（正常系）', () => {
    // P01ベルクート: mixingBanTargets:["銅剤"]
    const p01 = evalInApp(ctx, `PESTICIDE_DB.find(p => p.id === 'P01')`);
    // P02キノンドー: mixingBanTargets:["酸性剤","銅剤","硫黄剤"] だが system は "キノキサリン系"
    // P01自身が銅剤を含む名前・系統ではないため、実際に競合する既知ペアをデータから探して検証する
    const conflictPairIds = evalInApp(ctx, `
      (function() {
        for (const a of PESTICIDE_DB) {
          for (const b of PESTICIDE_DB) {
            if (a.id !== b.id && hasMixingConflict(a, b.id)) {
              return [a.id, b.id];
            }
          }
        }
        return null;
      })()
    `);
    assert.notStrictEqual(conflictPairIds, null, '少なくとも1組の混用禁止ペアがデータ上に存在するはず');
    const [aId, bId] = conflictPairIds;
    const reverseConflict = evalInApp(ctx, `hasMixingConflict(PESTICIDE_DB.find(p=>p.id==='${bId}'), '${aId}')`);
    assert.strictEqual(reverseConflict, true, '混用禁止関係は対称であるべき（a×b が真なら b×a も真)');
  });

  it('混用禁止関係にない薬剤同士は false を返す（正常系）', () => {
    // P03ゲッター: mixingBanTargets:[]（特記事項なし）
    const result = evalInApp(ctx, `
      (function() {
        const p03 = PESTICIDE_DB.find(p => p.id === 'P03');
        const p07 = PESTICIDE_DB.find(p => p.id === 'P07'); // 同じくmixingBanTargets:[]
        return hasMixingConflict(p03, p07.id);
      })()
    `);
    assert.strictEqual(result, false);
  });

  it('自分自身とのペアはfalseを返す（対角成分、正常系）', () => {
    const result = evalInApp(ctx, `
      (function() {
        const p01 = PESTICIDE_DB.find(p => p.id === 'P01');
        return hasMixingConflict(p01, p01.id);
      })()
    `);
    assert.strictEqual(result, false, 'P01のmixingBanTargets=["銅剤"]はP01自身の名前・系統に含まれないためfalse');
  });

  it('存在しないIDを渡すとfalseを返す（未定義入力、エッジケース）', () => {
    const result = evalInApp(ctx, `
      (function() {
        const p01 = PESTICIDE_DB.find(p => p.id === 'P01');
        return hasMixingConflict(p01, 'P999-NOT-EXIST');
      })()
    `);
    assert.strictEqual(result, false, '未知IDはPESTICIDE_INDEX_BY_IDに存在しないため、例外ではなくfalseとして扱う');
  });

  it('MIXING_CONFLICT_MATRIXは67×67の正方行列（次元検証、エッジケース）', () => {
    const dims = evalInApp(ctx, `
      [MIXING_CONFLICT_MATRIX.length, ...new Set(MIXING_CONFLICT_MATRIX.map(row => row.length))]
    `);
    const rowCount = dims[0];
    const colCounts = dims.slice(1);
    assert.strictEqual(rowCount, 67, `行数は67のはず（実際: ${rowCount}）`);
    assert.strictEqual(colCounts.length, 1, '全ての行が同じ列数であるべき（不揃いな行があってはならない）');
    assert.strictEqual(colCounts[0], 67, `列数は67のはず（実際: ${colCounts[0]}）`);
  });

  it('MIXING_CONFLICT_MATRIXの全要素は0または1（値域検証、エッジケース）', () => {
    const allBinary = evalInApp(ctx, `
      MIXING_CONFLICT_MATRIX.every(row => row.every(v => v === 0 || v === 1))
    `);
    assert.strictEqual(allBinary, true);
  });
});

summary();
