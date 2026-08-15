// tests/test_dsl_lexer.js — dsl/lexer.js のトークナイザを検証。
// 対象: 識別子（ハイフン・漢字仮名含む）、数値、文字列、(* *)コメント、
//       多文字記号、エラーケース（未閉じ文字列・未閉じコメント・不明文字）。
const { tokenize } = require('../dsl/lexer');
const { describe, it, assert, assertDeepEqualAcrossRealms, summary } = require('./tiny_test');

function types(tokens) {
  return tokens.map(t => t.type);
}
function values(tokens) {
  return tokens.map(t => t.value);
}

describe('tokenize（正常系）', () => {
  it('キーワード・識別子・記号を分割する', () => {
    const tokens = tokenize('Domain STB {');
    assertDeepEqualAcrossRealms(types(tokens), ['IDENT', 'IDENT', 'PUNCT', 'EOF']);
    assertDeepEqualAcrossRealms(values(tokens), ['Domain', 'STB', '{', null]);
  });

  it('ハイフンを含む識別子は1トークンになる（BOX-22, Line-3）', () => {
    const tokens = tokenize('BOX-22 Line-3 SPEC-BRIDGE-TARGET');
    assertDeepEqualAcrossRealms(values(tokens).slice(0, 3), ['BOX-22', 'Line-3', 'SPEC-BRIDGE-TARGET']);
  });

  it('漢字・かな・カタカナを含む識別子を1トークンとして読む', () => {
    const tokens = tokenize('灰色かび病 ベルクート');
    assertDeepEqualAcrossRealms(values(tokens).slice(0, 2), ['灰色かび病', 'ベルクート']);
  });

  it('文字列リテラルを1トークンとして読む', () => {
    const tokens = tokenize('"炭疽+うどんこ"');
    assert.strictEqual(tokens[0].type, 'STRING');
    assert.strictEqual(tokens[0].value, '炭疽+うどんこ');
  });

  it('整数・小数を読む', () => {
    const tokens = tokenize('10 0.5 5.5');
    assertDeepEqualAcrossRealms(values(tokens).slice(0, 3), [10, 0.5, 5.5]);
  });

  it('(* ... *) コメントはトークンを生成しない', () => {
    const tokens = tokenize('(* これはコメント { } *) Domain');
    assertDeepEqualAcrossRealms(values(tokens), ['Domain', null]);
  });

  it('複数行コメントも正しく読み飛ばす', () => {
    const tokens = tokenize('(*\n複数行\nコメント\n*) STB');
    assertDeepEqualAcrossRealms(values(tokens), ['STB', null]);
  });

  it('2文字記号（=> >= <= == != && || ..）を1トークンとして読む', () => {
    const tokens = tokenize('=> >= <= == != && || ..');
    assertDeepEqualAcrossRealms(values(tokens).slice(0, 8), ['=>', '>=', '<=', '==', '!=', '&&', '||', '..']);
  });

  it('"(disease)"はコメント開始と誤認しない（"(*"のみコメント）', () => {
    const tokens = tokenize('(disease)');
    assertDeepEqualAcrossRealms(values(tokens), ['(', 'disease', ')', null]);
  });

  it('負の数はマイナス記号と数値の2トークンに分かれる（skip対象なので厳密な符号解析は不要）', () => {
    const tokens = tokenize('delta=-10');
    assertDeepEqualAcrossRealms(types(tokens), ['IDENT', 'PUNCT', 'PUNCT', 'NUMBER', 'EOF']);
    assertDeepEqualAcrossRealms(values(tokens), ['delta', '=', '-', 10, null]);
  });

  it('"1..2"を数値・".."・数値に分割する（範囲構文の粗い扱い）', () => {
    const tokens = tokenize('1..2');
    assertDeepEqualAcrossRealms(values(tokens), [1, '..', 2, null]);
  });

  it('行番号を正しく追跡する', () => {
    const tokens = tokenize('Domain\nSTB\n{');
    assert.strictEqual(tokens[0].line, 1);
    assert.strictEqual(tokens[1].line, 2);
    assert.strictEqual(tokens[2].line, 3);
  });
});

describe('tokenize（エラーケース）', () => {
  it('未閉じの文字列リテラルはエラーを投げる', () => {
    assert.throws(() => tokenize('"閉じられていない'), /文字列リテラルが閉じられていません/);
  });

  it('未閉じのコメントはエラーを投げる', () => {
    assert.throws(() => tokenize('(* 閉じられていない'), /コメント.*閉じられていません/);
  });

  it('不明な文字はエラーを投げる', () => {
    assert.throws(() => tokenize('Domain § STB'), /不明な文字/);
  });
});

summary();
