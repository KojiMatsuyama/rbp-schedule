// tests/test_dsl_derive.js — dsl/derive_matrices.js が導出する
// 要求評価RBP行列（BridgeMatrix）・仕様決定RBP行列（SpecBridgeMatrix）を、
// 実際のdata/eval_boxes.js（EB_VECTORS/EB_NAMES）・data/pesticides.js（PESTICIDE_DB）
// と数値突合する。dsl/samples/full_program.txt はEB-22・P15・P47について
// 実データと同一の値になるよう作成されている。
const fs = require('fs');
const path = require('path');
const { loadApp, evalInApp } = require('./load_app');
const { parseProgram } = require('../dsl/parser');
const { deriveAll } = require('../dsl/derive_matrices');
const { describe, it, assert, assertDeepEqualAcrossRealms, summary } = require('./tiny_test');

const ctx = loadApp();
const source = fs.readFileSync(path.join(__dirname, '..', 'dsl', 'samples', 'full_program.txt'), 'utf8');
const derived = deriveAll(parseProgram(source));

describe('BridgeMatrix（要求評価RBP行列）と実データの突合', () => {
  it('BOX-22から導出したベクトルは実際のEB_VECTORS["EB-22"]と一致する', () => {
    const realVector = evalInApp(ctx, `EB_VECTORS["EB-22"]`);
    assertDeepEqualAcrossRealms(derived.bridgeMatrix.vectors['EB-22'], realVector);
  });

  it('BOX-22のas名は実際のEB_NAMES["EB-22"]と一致する', () => {
    const realName = evalInApp(ctx, `EB_NAMES["EB-22"]`);
    assert.strictEqual(derived.bridgeMatrix.names['EB-22'], realName);
  });

  it('matrixはvectorsのObject.valuesと一致する（EB_MATRIX = Object.values(EB_VECTORS)と同じ導出）', () => {
    assertDeepEqualAcrossRealms(derived.bridgeMatrix.matrix, Object.values(derived.bridgeMatrix.vectors));
  });
});

describe('SpecBridgeMatrix（仕様決定RBP行列）と実データの突合', () => {
  it('Candidate P15のtargetVectorは実際のPESTICIDE_DB["P15"].targetVectorと一致する', () => {
    const real = evalInApp(ctx, `PESTICIDE_DB.find(p => p.id === "P15")`);
    const derivedP15 = derived.specBridgeMatrix.candidates.find(c => c.id === 'P15');
    assertDeepEqualAcrossRealms(derivedP15.targetVector, real.targetVector);
    assert.strictEqual(derivedP15.system, real.system);
    assert.strictEqual(derivedP15.maxApplications, real.maxApplications);
  });

  it('Candidate P47のtargetVectorは実際のPESTICIDE_DB["P47"].targetVectorと一致する', () => {
    const real = evalInApp(ctx, `PESTICIDE_DB.find(p => p.id === "P47")`);
    const derivedP47 = derived.specBridgeMatrix.candidates.find(c => c.id === 'P47');
    assertDeepEqualAcrossRealms(derivedP47.targetVector, real.targetVector);
    assert.strictEqual(derivedP47.system, real.system);
    assert.strictEqual(derivedP47.maxApplications, real.maxApplications);
  });

  it('matrixはcandidatesのtargetVectorをmapしたもの（TARGET_MATRIX = PESTICIDE_DB.map(p=>p.targetVector)と同じ導出）', () => {
    assertDeepEqualAcrossRealms(
      derived.specBridgeMatrix.matrix,
      derived.specBridgeMatrix.candidates.map(c => c.targetVector)
    );
  });
});

summary();
