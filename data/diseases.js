// data/diseases.js — 病害虫定義（10次元ベクトル空間の次元定義）
// ⚠️ 生成物（自動生成・手編集禁止）。唯一無二の正は SQLite DB の diseases テーブル。
//    変更は DB 側で行い、`python3 scripts/export_diseases.py` で再生成すること。
//    bootstrap のシード元は db_setup.py の DISEASES_SEED。
// アプリケーション固有データ。framework/ 層には依存しない。
var DISEASES = [
  { id: 0, name: '炭疽病', type: 'disease', icon: '🍅' },
  { id: 1, name: '灰色かび病', type: 'disease', icon: '🌫️' },
  { id: 2, name: 'うどんこ病', type: 'disease', icon: '🍚' },
  { id: 3, name: 'ナミハダニ', type: 'pest', icon: '🕷️' },
  { id: 4, name: 'ハスモンヨトウ', type: 'pest', icon: '🦋' },
  { id: 5, name: 'オオタバコガ', type: 'pest', icon: '🐛' },
  { id: 6, name: 'ミカンキイロアザミウマ', type: 'pest', icon: '🪳' },
  { id: 7, name: 'ワタアブラムシ', type: 'pest', icon: '🐌' },
  { id: 8, name: 'アブラムシ', type: 'pest', icon: '🐜' },
  { id: 9, name: 'コナジラミ', type: 'pest', icon: '🪽' },
];
