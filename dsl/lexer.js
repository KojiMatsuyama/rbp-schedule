// dsl/lexer.js — 境界言語DSLのトークナイザ
// Node製ビルド時ツール（CommonJS）。ブラウザ実行用framework/data/rbp配下とは独立の
// オフラインコンパイラなので、モジュール形式で書いてよい。
// 境界言語BNF.txt §7 の字句規則（Identifier/Number/String/コメント）に対応する。

const KANJI_KANA = '\\u3040-\\u30FF\\u4E00-\\u9FFF';
const IDENT_START = new RegExp(`[A-Za-z_${KANJI_KANA}]`);
const IDENT_CONT = new RegExp(`[A-Za-z0-9_\\-${KANJI_KANA}]`);

const TWO_CHAR_PUNCT = ['=>', '==', '!=', '>=', '<=', '&&', '||', '..'];
const ONE_CHAR_PUNCT = '{}()[]:;,=+-*/.!<>&|^';

function isDigit(ch) {
  return ch >= '0' && ch <= '9';
}

// 境界言語DSLソース文字列をトークン列へ変換する。
// Reflect-Boundary/Spec-Boundary内の複雑な式（Weight-Rule/Condition/Score-Expr等）も
// パーサ側でトークンレベルの中括弧平衡カウントで読み飛ばすため、
// レクサはそれらの意味を解釈する必要がなく、字句として矛盾なく分割できればよい。
function tokenize(source) {
  const tokens = [];
  const n = source.length;
  let i = 0;
  let line = 1;

  function peekChar(offset) {
    return source[i + (offset || 0)];
  }

  while (i < n) {
    const ch = source[i];

    if (ch === '\n') {
      line++;
      i++;
      continue;
    }
    if (ch === ' ' || ch === '\t' || ch === '\r') {
      i++;
      continue;
    }

    // (* ... *) コメント
    if (ch === '(' && peekChar(1) === '*') {
      const startLine = line;
      i += 2;
      while (i < n && !(source[i] === '*' && peekChar(1) === ')')) {
        if (source[i] === '\n') line++;
        i++;
      }
      if (i >= n) {
        throw new Error(`Lexer error (line ${startLine}): コメント "(* ... *)" が閉じられていません`);
      }
      i += 2;
      continue;
    }

    // 文字列リテラル
    if (ch === '"') {
      const startLine = line;
      let j = i + 1;
      let value = '';
      while (j < n && source[j] !== '"') {
        if (source[j] === '\n') line++;
        value += source[j];
        j++;
      }
      if (j >= n) {
        throw new Error(`Lexer error (line ${startLine}): 文字列リテラルが閉じられていません`);
      }
      tokens.push({ type: 'STRING', value, line: startLine });
      i = j + 1;
      continue;
    }

    // 数値（整数部 [ "." 小数部 ] ）
    if (isDigit(ch)) {
      const startLine = line;
      let j = i;
      while (j < n && isDigit(source[j])) j++;
      if (source[j] === '.' && isDigit(source[j + 1])) {
        j++;
        while (j < n && isDigit(source[j])) j++;
      }
      tokens.push({ type: 'NUMBER', value: Number(source.slice(i, j)), line: startLine });
      i = j;
      continue;
    }

    // 識別子・キーワード（ハイフンを継続文字として許す: "BOX-22", "Line-3", "SPEC-BRIDGE-TARGET" 等）
    if (IDENT_START.test(ch)) {
      const startLine = line;
      let j = i + 1;
      while (j < n && IDENT_CONT.test(source[j])) j++;
      tokens.push({ type: 'IDENT', value: source.slice(i, j), line: startLine });
      i = j;
      continue;
    }

    // 2文字記号
    const two = source.slice(i, i + 2);
    if (TWO_CHAR_PUNCT.indexOf(two) !== -1) {
      tokens.push({ type: 'PUNCT', value: two, line });
      i += 2;
      continue;
    }

    // 1文字記号
    if (ONE_CHAR_PUNCT.indexOf(ch) !== -1) {
      tokens.push({ type: 'PUNCT', value: ch, line });
      i++;
      continue;
    }

    throw new Error(`Lexer error (line ${line}): 不明な文字 "${ch}"`);
  }

  tokens.push({ type: 'EOF', value: null, line });
  return tokens;
}

module.exports = { tokenize };
