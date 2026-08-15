// tests/test_dsl_parser.js — dsl/parser.js のAST構築を検証。
// 対象: dsl/samples/full_program.txt からDomain/Demand/Bridge/SpecBridgeの
//       AST形状が正しく組み立てられること、Reflect/Spec本体がbrace平衡skipで
//       壊れずに読み飛ばされること（parseProgramがEOFまで到達すること自体がその証拠）。
const fs = require('fs');
const path = require('path');
const { parseProgram } = require('../dsl/parser');
const { describe, it, assert, assertDeepEqualAcrossRealms, summary } = require('./tiny_test');

const SAMPLES_DIR = path.join(__dirname, '..', 'dsl', 'samples');
const fullProgramSource = fs.readFileSync(path.join(SAMPLES_DIR, 'full_program.txt'), 'utf8');

describe('parseProgram（dsl/samples/full_program.txt）', () => {
  const ast = parseProgram(fullProgramSource);

  it('Domain-Declarationを解析する', () => {
    assert.strictEqual(ast.domain.id, 'STB');
    assert.strictEqual(ast.domain.dimensions, 10);
    assert.strictEqual(ast.domain.lines.length, 10);
    assert.strictEqual(ast.domain.lines[0], '炭疽病');
    assert.strictEqual(ast.domain.lines[9], 'コナジラミ');
  });

  it('Demand-BoundaryのLine-Defを10件解析する', () => {
    assert.strictEqual(ast.demand.id, 'PestDemand');
    assert.strictEqual(ast.demand.lineDefs.length, 10);
    assert.strictEqual(ast.demand.lineDefs[1].name, '灰色かび病');
    assert.strictEqual(ast.demand.lineDefs[1].kind, 'disease');
    assert.strictEqual(ast.demand.lineDefs[3].kind, 'pest');
  });

  it('Bridge-BoundaryのBOX-Defを解析する（Line-Ref-Setとas名）', () => {
    assert.strictEqual(ast.bridge.id, 'EvalBox');
    assert.strictEqual(ast.bridge.boxDefs.length, 1);
    const box = ast.bridge.boxDefs[0];
    assert.strictEqual(box.id, '22');
    assertDeepEqualAcrossRealms(box.lineRefs, [0, 1, 2, 4, 5, 8, 9]);
    assert.strictEqual(box.asName, '炭疽+うどんこ+灰色かび+ハスモン+オオタ+アブラムシ+コナジラミ');
  });

  it('Bridge-Extension-Policyを解析する（on-UNDEFINED=auto-register）', () => {
    assert.strictEqual(ast.bridge.extensionPolicy.onUndefined, 'auto-register');
    assert.strictEqual(ast.bridge.extensionPolicy.idRule, 'next-max-plus-one');
  });

  it('SpecBridge-BoundaryのCandidate-Defを解析する（targetsと属性）', () => {
    assert.strictEqual(ast.specBridge.id, 'PesticideCatalog');
    assert.strictEqual(ast.specBridge.candidateDefs.length, 2);

    const p15 = ast.specBridge.candidateDefs.find(c => c.id === 'P15');
    assertDeepEqualAcrossRealms(p15.targets, [1, 2]);
    assertDeepEqualAcrossRealms(p15.attributes, [
      { key: 'system', value: 'ベンズイミダゾール系' },
      { key: 'maxApplications', value: 3 },
    ]);

    const p47 = ast.specBridge.candidateDefs.find(c => c.id === 'P47');
    assertDeepEqualAcrossRealms(p47.targets, [3]);
  });

  it('SpecBridge-Boundary側のBridge-Extension-Policyはalert-and-halt', () => {
    assert.strictEqual(ast.specBridge.extensionPolicy.onUndefined, 'alert-and-halt');
  });

  it('Reflect-Boundary/Spec-Boundaryを読み飛ばした上でEOFに到達する（ast自体にreflect/specキーは持たない）', () => {
    assert.strictEqual('reflect' in ast, false);
    assert.strictEqual('spec' in ast, false);
  });
});

describe('parseProgram（エッジケース・エラー系）', () => {
  it('Bridge-Extension-Policyを省略したBridge-Boundaryはデフォルト値(null)のまま解析できる', () => {
    const source = `
      Domain D { dimensions: 1; lines: ["炭疽病"]; }
      Demand-Boundary Dem { Line-0: 炭疽病(disease); Demand-Rule { on-occurrence => Line-i = 1; on-absence => Line-i = 0; } }
      Bridge-Boundary B { BOX-1: { Line-0 } as "単独"; Bridge-Rule { match = exact; } }
      SpecBridge-Boundary S { Candidate P1: { targets = Line-0; }; SpecBridge-Rule { match-vector = EntryVector; } }
      Reflect-Boundary R { }
      Spec-Boundary Sp { }
    `;
    const ast = parseProgram(source);
    assert.strictEqual(ast.bridge.extensionPolicy.onUndefined, null);
  });

  it('BOX-Defで閉じ括弧を忘れると構文エラーを投げる', () => {
    const source = `
      Domain D { dimensions: 1; lines: ["炭疽病"]; }
      Demand-Boundary Dem { Line-0: 炭疽病(disease); Demand-Rule { on-occurrence => Line-i = 1; on-absence => Line-i = 0; } }
      Bridge-Boundary B { BOX-1: { Line-0 as "単独"; }
    `;
    assert.throws(() => parseProgram(source), /Parse error/);
  });

  it('未知のトップレベルキーワードは構文エラーになる', () => {
    const source = `Unknown-Boundary X { }`;
    assert.throws(() => parseProgram(source), /"Domain" を期待しました/);
  });
});

summary();
