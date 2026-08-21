#!/usr/bin/env python3
"""
mcp_tools.py — STB農業AIアシスタントのMCPツール群

server.py内で直接importして使うインプロセス型ツール。
MCPサーバープロセスは不要。Gemini APIがこれらの関数を呼び出す。
"""

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_ROOT, "data", "stb.db")
VECTOR_DIM = 10

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

# Embedding model constants
# jina-embeddings-v2-base-ja は HuggingFace 認証が必要なので、
# 認証不要の LaBSE (109言語クロスリンガル、768次元) を使用する。
_EMBEDDING_MODEL_ID = "sentence-transformers/LaBSE"
_EMBEDDING_DIM = 768
_RAG_INDEX_PATH = os.path.join(APP_ROOT, "data", "rag_index.pkl")


# =====================================================================
# Japanese date parser — converts various formats to YYYY-MM-DD
# =====================================================================

_JP_MONTH_NAMES = {
    "1": "1月", "2": "2月", "3": "3月", "4": "4月",
    "5": "5月", "6": "6月", "7": "7月", "8": "8月",
    "9": "9月", "10": "10月", "11": "11月", "12": "12月",
}


def parse_japanese_date(text: str) -> Optional[str]:
    """
    日本語の日付表現を YYYY-MM-DD 形式に変換する。

    対応フォーマット:
      - "2025年2月21日"       → "2025-02-21"
      - "2025/2/21"           → "2025-02-21"
      - "2025-02-21"          → "2025-02-21"
      - "25年2月21日"         → "2025-02-21"
      - "2月21日"             → 今年を仮定して "2025-02-21"
      - "来月"                → 翌月（簡易）
      - "再来月"              → 翌々月（簡易）
      - "先月"                → 前月（簡易）
      - "今日" / "明日" / "明後日" → 相対日付

    Returns:
        YYYY-MM-DD 形式の文字列、または None（解析不可）
    """
    text = text.strip()

    # --- 絶対日付パターン ---

    # "2025年2月21日" or "2025年02月21日"
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # "25年2月21日" (2桁年)
    m = re.match(r'(\d{2})年(\d{1,2})月(\d{1,2})日', text)
    if m:
        yy = int(m.group(1))
        yyyy = 2000 + yy if yy < 100 else int(m.group(1))
        return f"{yyyy:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # "2025/2/21" or "2025-2-21"
    m = re.match(r'(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})', text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # "2月21日" (年省略 → 今年)
    m = re.match(r'(\d{1,2})月(\d{1,2})日$', text)
    if m:
        now = datetime.now()
        return f"{now.year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # --- 相対日付パターン ---
    now = datetime.now()

    if text in ("今日", "本日", "きょう", "こんじつ"):
        return now.strftime("%Y-%m-%d")

    if text in ("明日", "あした", "みょうにち"):
        tomorrow = now + timedelta(days=1)
        return tomorrow.strftime("%Y-%m-%d")

    if text in ("明後日", "みょっか"):
        day_after = now + timedelta(days=2)
        return day_after.strftime("%Y-%m-%d")

    if text in ("昨日", "きのう", "さくじつ"):
        yesterday = now - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d")

    if text in ("一昨日", "おととい", "いっさくじつ"):
        day_before = now - timedelta(days=2)
        return day_before.strftime("%Y-%m-%d")

    if text in ("来月", "らいげつ"):
        month = now.month + 1
        year = now.year
        if month > 12:
            month = 1
            year += 1
        return f"{year:04d}-{month:02d}-01"

    if text in ("先月", "せんげつ"):
        month = now.month - 1
        year = now.year
        if month < 1:
            month = 12
            year -= 1
        return f"{year:04d}-{month:02d}-01"

    if text in ("再来月", "さいらいげつ"):
        month = now.month + 2
        year = now.year
        if month > 12:
            month -= 12
            year += 1
        return f"{year:04d}-{month:02d}-01"

    # "今月"
    if text in ("今月", "こんげつ"):
        return f"{now.year:04d}-{now.month:02d}-01"

    # "先週" / "来週" (月初め)
    if text in ("先週", "せんしゅう"):
        week_ago = now - timedelta(weeks=1)
        return week_ago.strftime("%Y-%m-%d")
    if text in ("来週", "らいしゅう"):
        next_week = now + timedelta(weeks=1)
        return next_week.strftime("%Y-%m-%d")

    return None


def get_db():
    """Thread-safe SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _parse_row(row):
    """Convert sqlite Row to dict, parsing JSON-string columns."""
    d = dict(row)
    for col in ("targetVector", "targetNames", "mixingBanTargets", "mixingRestriction"):
        if d.get(col) and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# =====================================================================
# SYMPTOM DICTIONARY — 症状→病害虫 マッピング（ルックアップ用）
# =====================================================================
# セマンティック検索の前にまずここをチェックし、病害虫名が特定できれば
# 既存の強力なSQL検索にフォールバックする。

SYMPTOM_DICTIONARY = {
    # --- 葉の変色 ---
    "葉っぱが黄色くなる": ["アブラムシ", "うどんこ病", "栄養障害"],
    "葉っぱが黄色くなってる": ["アブラムシ", "うどんこ病", "栄養障害"],
    "葉が黄色くなる": ["アブラムシ", "うどんこ病", "栄養障害"],
    "葉が黄色くなってる": ["アブラムシ", "うどんこ病", "栄養障害"],
    "葉がyellow": ["アブラムシ", "うどんこ病", "栄養障害"],
    "黄ばみ": ["アブラムシ", "うどんこ病", "栄養障害"],
    "黄色い": ["アブラムシ", "うどんこ病", "栄養障害"],
    "葉が白っぽくなる": ["アブラムシ", "うどんこ病"],
    "葉が白くなる": ["うどんこ病", "ハダニ"],
    "葉に斑点": ["炭疽病", "灰色かび病", "モザイク病"],
    "葉に黒い斑点": ["炭疽病"],
    "葉に茶色い斑点": ["炭疽病", "灰色かび病"],
    # --- 葉の形状変化 ---
    "葉が丸まる": ["アブラムシ", "モザイク病"],
    "葉が丸まってる": ["アブラムシ", "モザイク病"],
    "葉が巻く": ["アブラムシ", "モザイク病", "温度障害"],
    "葉が卷いてる": ["アブラムシ", "モザイク病", "温度障害"],
    "葉が縮れる": ["アブラムシ", "モザイク病"],
    "葉が縮れてる": ["アブラムシ", "モザイク病"],
    "葉が萎れる": ["灰色かび病", "軟腐病", "水分障害"],
    "葉が枯れる": ["灰色かび病", "炭疽病", "水分障害"],
    "葉に穴": ["ヨトウムシ", "アオムシ", "アザミウマ"],
    "葉に穴が開く": ["ヨトウムシ", "アオムシ"],
    "葉に穴があいてる": ["ヨトウムシ", "アオムシ"],
    "葉が欠ける": ["ヨトウムシ", "アオムシ"],
    # --- 表面の付着物 ---
    "葉に白い粉": ["うどんこ病"],
    "葉に白い粉っぽい": ["うどんこ病"],
    "白い粉が吹く": ["うどんこ病"],
    "白い粉": ["うどんこ病"],
    "葉にぬめり": ["灰色かび病"],
    "葉がねばねば": ["アブラムシ", "コナジラミ"],
    "ハチミツみたいな": ["アブラムシ", "コナジラミ"],
    "葉に銀色の跡": ["アザミウマ"],
    "葉に透明感": ["アザミウマ"],
    "葉に細かい白点": ["アザミウマ", "ハダニ"],
    # --- 裏側の虫 ---
    "葉の裏に小さい虫": ["アブラムシ", "コナジラミ"],
    "葉の裏に虫": ["アブラムシ", "コナジラミ", "ハダニ"],
    "葉っぱの裏に虫がついてる": ["アブラムシ", "コナジラミ", "ハダニ"],
    "葉の裏に虫": ["アブラムシ", "コナジラミ", "ハダニ"],
    "葉の裏に白い虫": ["コナジラミ", "アブラムシ"],
    "裏側に虫": ["アブラムシ", "コナジラミ", "ハダニ"],
    "糸状の蜘蛛": ["ナミハダニ", "ハダニ"],
    "葉に蜘蛛の巣": ["ハダニ", "ナミハダニ"],
    "葉が蛛の巣": ["ハダニ", "ナミハダニ"],
    # --- 実・花・茎 ---
    "実が腐る": ["炭疽病", "灰色かび病"],
    "実が腐ってる": ["炭疽病", "灰色かび病"],
    "実が割れる": ["炭疽病", "水分障害"],
    "花が落ちる": ["灰色かび病", "温度障害"],
    "花が咲かない": ["栄養障害", "温度障害"],
    "茎が柔らかい": ["灰色かび病"],
    "茎が腐る": ["灰色かび病", "軟腐病"],
    "株元が腐る": ["軟腐病", "モザイク病"],
    # --- 成長不良 ---
    "成長が悪い": ["栄養障害", "モザイク病", "温度障害"],
    "苗が弱い": ["モザイク病", "軟腐病", "温度障害"],
    "葉が小さい": ["栄養障害", "モザイク病"],
    "徒長する": ["温度障害", "光量不足"],
    # --- 有機JAS関連 ---
    "有機で使える": ["有機JAS対応農薬"],
    "有機JAS対応": ["有機JAS対応農薬"],
    "有機許可": ["有機JAS対応農薬"],
    "オーガニック": ["有機JAS対応農薬"],
    # --- 混用関連 ---
    "混ぜられる": ["混用相談"],
    "混用": ["混用相談"],
    "一緒に使える": ["混用相談"],
    "同じタイミング": ["混用相談"],
}


# =====================================================================
# RAG STORE (TF-IDF) — セマンティック検索エンジン
# =====================================================================
# DBの農薬・病害虫・記録データをテキストチャンクに変換し、
# TF-IDFベクトル化してセマンティック検索を可能にする。
# インデックスは data/rag_index.pkl にpickle保存する。


def _build_pesticide_chunks(pesticide: dict) -> list:
    """
    農薬レコードから検索用テキストチャンクを生成する。

    2つのチャンクタイプを作成:
      - identity: 基本情報（名前・成分・類別・標的・PHI・毒性・散布制限）
      - usage: 使用方法・抵抗性管理・混用情報
    """
    name = pesticide["name"]
    ingredient = pesticide.get("activeIngredient", "") or ""
    category = pesticide.get("category", "") or ""
    cat_jp = {
        "fungicide": "殺菌剤",
        "insecticide": "殺虫剤",
        "acaricide": "殺ダニ剤",
    }.get(category, category)

    # targetNames is sometimes stored as JSON string with Unicode escapes
    targets_raw = pesticide.get("targetNames", [])
    if isinstance(targets_raw, str):
        try:
            targets = json.loads(targets_raw)
        except (json.JSONDecodeError, TypeError):
            targets = []
    else:
        targets = targets_raw or []
    targets_str = "、".join(targets) if targets else " unspecified"

    phi = pesticide.get("phiDays")
    phi_str = f"{phi}日" if phi is not None else "未設定"
    tox = pesticide.get("toxicityClass", "") or "未設定"
    max_app = pesticide.get("maxApplications")
    if max_app is None:
        max_app_str = "無制限"
    elif isinstance(max_app, str) and max_app.lower() in ("inf", "infinity", "無限"):
        max_app_str = "無制限"
    else:
        try:
            max_app_str = f"{int(float(max_app))}回"
        except (ValueError, TypeError):
            max_app_str = "無制限"
    mix_restrict = pesticide.get("mixingRestriction", "") or ""
    system = pesticide.get("system", "") or ""
    system_code = pesticide.get("systemCode", "") or ""

    # Identity chunk: basic info as natural language
    identity_text = (
        f"{name}は{cat_jp}です。有効成分は{ingredient}。"
        f"FRACグループは{system}{system_code}。"
        f"標的病害虫は{targets_str}。"
        f"PHIは{phi_str}。毒性区分は{tox}。"
        f"最大散布回数は{max_app_str}。"
        f"{mix_restrict}" if mix_restrict else ""
    )

    # Usage chunk: application method & resistance management
    usage_text = (
        f"{name}の使い方: {cat_jp}として{targets_str}に使用。"
        f"{system_code}系統のため、抵抗性管理のため他のFRACグループと回転使用すること。"
        f"{mix_restrict}" if mix_restrict else ""
    )

    return [
        {
            "type": "identity",
            "text": identity_text,
            "source_id": name,
            "source_type": "pesticide",
            "source_ref": pesticide.get("id", ""),
        },
        {
            "type": "usage",
            "text": usage_text,
            "source_id": name,
            "source_type": "pesticide",
            "source_ref": pesticide.get("id", ""),
        },
    ]


def _build_disease_chunks(disease: dict) -> list:
    """
    病害虫レコードから検索用テキストチャンクを生成する。
    """
    name = disease["name"]
    dtype = disease.get("type", "")
    type_jp = {"disease": "病害", "pest": "害虫"}.get(dtype, dtype)

    profile_text = f"{name}は{type_jp}です。ID: {disease.get('id', '')}"

    return [
        {
            "type": "profile",
            "text": profile_text,
            "source_id": name,
            "source_type": "disease",
            "source_ref": str(disease.get("id", "")),
        },
    ]


def _build_record_chunks(record: dict) -> list:
    """
    防除記録レコードから検索用テキストチャンクを生成する。
    """
    date = record.get("date", "")
    pests_raw = record.get("pests", [])
    if isinstance(pests_raw, str):
        try:
            pests = json.loads(pests_raw)
        except (json.JSONDecodeError, TypeError):
            pests = []
    else:
        pests = pests_raw or []
    pests_str = "、".join(pests) if pests else "不明"

    text = f"{date}の防除記録: {pests_str}に対処"

    return [
        {
            "type": "history",
            "text": text,
            "source_id": date,
            "source_type": "record",
            "source_ref": date,
        },
    ]


def _extract_chunks(conn: sqlite3.Connection) -> list:
    """
    DBから全データをロードし、テキストチャンクのリストに変換する。

    Returns:
        list of dict: 각チャンクは {"type", "text", "source_id", "source_type", "source_ref"}
    """
    chunks = []

    # 農薬
    rows = conn.execute("SELECT * FROM pesticides ORDER BY id").fetchall()
    for r in rows:
        d = dict(r)
        # Parse JSON-string columns
        for col in ("targetNames",):
            if d.get(col) and isinstance(d[col], str):
                try:
                    d[col] = json.loads(d[col])
                except (json.JSONDecodeError, TypeError):
                    pass
        chunks.extend(_build_pesticide_chunks(d))

    # 病害虫
    rows = conn.execute("SELECT * FROM diseases ORDER BY id").fetchall()
    for r in rows:
        chunks.extend(_build_disease_chunks(dict(r)))

    # 記録
    rows = conn.execute("SELECT * FROM spray_history ORDER BY date").fetchall()
    for r in rows:
        chunks.extend(_build_record_chunks(dict(r)))

    return chunks


class RAGStore:
    """
    埋め込みモデル + FAISS ベースの RAG インデックス。

    役割:
      - rebuild(): DBから全データをロードして埋め込みインデックスを再生成
      - search(): クエリの埋め込みベクトルとコサイン類似度で検索
      - save/load: ディスクへのpickle永続化

    使用法:
      >>> store = RAGStore()
      >>> if not store.load():
      ...     store.rebuild(get_db())
      ...     store.save()
      >>> results = store.search("葉っぱが黄色い", limit=5)

    アーキテクチャ:
      - モデル: jina-embeddings-v2-base-ja (768次元, 日本語特化)
      - インデックス: FAISS IndexFlatIP (内積 = コサイン類似度)
      - 永続化: pickle (モデル + 埋め込み + チャンクメタデータ)
    """

    def __init__(self):
        self.embedder = None           # SentenceTransformer インスタンス
        self.chunks: list = []         # チャンクリスト
        self.index = None              # FAISS インデックス
        self.embedding_cache = None    # 全チャンクの埋め込み (N x 768)

    def _load_embedder(self):
        """埋め込みモデルを遅延ロードする。初回のみHuggingFaceからダウンロード。"""
        if self.embedder is not None:
            return self.embedder
        try:
            from sentence_transformers import SentenceTransformer
            self.embedder = SentenceTransformer(
                _EMBEDDING_MODEL_ID,
                trust_remote_code=True,
            )
        except Exception as e:
            logger.error(f"埋め込みモデルのロードに失敗: {e}")
            raise
        return self.embedder

    def rebuild(self, conn: sqlite3.Connection):
        """
        DBから全データをロードして埋め込みインデックスを再生成する。

        Args:
            conn: SQLite接続（get_db() で取得可能）
        """
        chunks = _extract_chunks(conn)
        texts = [c["text"] for c in chunks]

        self.chunks = chunks

        # 埋め込みモデルのロード
        embedder = self._load_embedder()

        # 全チャンクを埋め込みベクトルに変換（normalize=True → コサイン類似度 = 内積）
        logger.info(f"埋め込み計算中: {len(texts)}チャンク...")
        self.embedding_cache = embedder.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        # FAISS インデックス構築 (Inner Product = Cosine Similarity)
        import faiss

        dim = self.embedding_cache.shape[1]  # 768
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(self.embedding_cache)

        logger.info(f"FAISSインデックス構築完了: {self.index.ntotal}件, 次元={dim}")

    def load(self) -> bool:
        """
        ディスクからインデックスを読み込む。

        Returns:
            True: 成功して読み込んだ
            False: ファイルが存在しない、または破損
        """
        if not os.path.exists(_RAG_INDEX_PATH):
            return False
        try:
            import pickle
            with open(_RAG_INDEX_PATH, "rb") as f:
                data = pickle.load(f)
            self.chunks = data["chunks"]
            self.embedding_cache = data["embedding_cache"]
            self.index = data["index"]
            # モデルはlazyロード（pickleに含めると巨大になる）
            self.embedder = None
            return True
        except Exception:
            return False

    def save(self):
        """
        インデックスをディスクにpickle保存する。

        注意: モデル本体はpickleに含めない（巨大になる）。
        読み込み時は _load_embedder() で lazy ロードする。
        """
        import pickle
        os.makedirs(os.path.dirname(_RAG_INDEX_PATH), exist_ok=True)
        with open(_RAG_INDEX_PATH, "wb") as f:
            pickle.dump({
                "chunks": self.chunks,
                "embedding_cache": self.embedding_cache,
                "index": self.index,
            }, f)
        logger.info(f"インデックス保存完了: {_RAG_INDEX_PATH}")

    def search(self, query: str, limit: int = 5,
               source_type: Optional[str] = None) -> list:
        """
        クエリに類似したチャンクを埋め込み類似度で検索する。

        Args:
            query: 検索クエリ（自然言語）
            limit: 最大取得件数
            source_type: ソースタイプで絞り込み（"pesticide", "disease",
                         "record", None=全部）

        Returns:
            list of dict: [{"chunk": ..., "score": ...}, ...]
        """
        if self.index is None or self.embedding_cache is None:
            return []

        # クエリの埋め込み
        embedder = self._load_embedder()
        query_emb = embedder.encode([query], normalize_embeddings=True)

        # FAISS検索 (inner product = cosine similarity)
        scores, indices = self.index.search(query_emb, k=len(self.chunks))

        # ソースタイプでフィルタ
        if source_type and source_type != "all":
            filtered_scores = []
            filtered_indices = []
            for s, idx in zip(scores[0], indices[0]):
                if self.chunks[idx]["source_type"] == source_type:
                    filtered_scores.append(s)
                    filtered_indices.append(idx)
            scores = np.array(filtered_scores).reshape(1, -1)
            indices = np.array(filtered_indices).reshape(1, -1)

        # トップK
        top_indices = indices[0][:limit]
        top_scores = scores[0][:limit]

        results = []
        for idx, score in zip(top_indices, top_scores):
            if score <= 0:
                break
            results.append({
                "chunk": self.chunks[int(idx)],
                "score": float(score),
            })

        return results


# Global singleton — lazily initialized on first use
_rag_store_instance: Optional[RAGStore] = None
_rag_init_lock = threading.Lock()


def get_rag_store() -> RAGStore:
    """
    RAGStoreのシングルトンインスタンスを取得する。

    初回呼び出し時にDBからインデックスをロード（なければrebuild）。
    スレッドセーフ。
    """
    global _rag_store_instance
    if _rag_store_instance is not None:
        return _rag_store_instance

    with _rag_init_lock:
        # Double-check
        if _rag_store_instance is not None:
            return _rag_store_instance

        store = RAGStore()
        if store.load():
            _rag_store_instance = store
            return store

        # Load failed or no cache — rebuild from DB
        try:
            conn = get_db()
            store.rebuild(conn)
            store.save()
            conn.close()
        except Exception:
            # If rebuild fails, return empty store
            pass

        _rag_store_instance = store
        return store


# =====================================================================
# TOOL REGISTRY — 全ツール定義の一元管理
# =====================================================================
# このリストを修正すれば、Claude / Gemini / ローカルLLM すべてに反映される。

TOOL_REGISTRY = [
    {
        "name": "search_pesticides",
        "description": "病害虫名や薬剤名で検索して、該当する薬剤を一覧で返す。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "検索キーワード（必須）"},
                "category": {"type": "string", "description": "類別で絞り込み（任意: fungicide=insecticide=殺虫剤, acaricide=殺ダニ剤）"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "list_pesticides",
        "description": "薬剤一覧を取得する。类别で絞り込み可能。",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "類別で絞り込み（任意: fungicide, insecticide, acaricide）"},
                "limit": {"type": "integer", "description": "最大取得件数（デフォルト100）"},
            },
            "required": [],
        },
    },
    {
        "name": "get_pesticide_detail",
        "description": "特定の薬剤の詳細情報を取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pesticide_id": {"type": "string", "description": "薬剤ID（例: P01）"},
            },
            "required": ["pesticide_id"],
        },
    },
    {
        "name": "list_diseases",
        "description": "病害虫マスター一覧を取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease_type": {"type": "string", "description": "タイプで絞り込み（任意: disease=病害, pest=害虫）"},
            },
            "required": [],
        },
    },
    {
        "name": "get_disease_detail",
        "description": "特定の病害虫の詳細情報を取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "disease_id": {"type": "integer", "description": "病害虫ID（例: 0）"},
            },
            "required": ["disease_id"],
        },
    },
    {
        "name": "get_spray_history",
        "description": "防除履歴を取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "年で絞り込み（任意）"},
                "month": {"type": "integer", "description": "月で絞り込み（任意）"},
                "date_from": {"type": "string", "description": "開始日 YYYY-MM-DD（任意）"},
                "date_to": {"type": "string", "description": "終了日 YYYY-MM-DD（任意）"},
                "limit": {"type": "integer", "description": "最大取得件数（デフォルト365）"},
            },
            "required": [],
        },
    },
    {
        "name": "add_spray_history",
        "description": "新規防除記録を追加する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日付 YYYY-MM-DD"},
                "pests": {"type": "string", "description": "病害虫名のJSON配列（例: '[\"炭疽病\"]'）"},
                "vector": {"type": "string", "description": "10次元ベクトルのJSON配列（例: '[1,0,0,0,0,0,0,0,0,0]'）"},
            },
            "required": ["date", "pests", "vector"],
        },
    },
    {
        "name": "update_spray_history",
        "description": "既存の防除記録を更新する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日付 YYYY-MM-DD"},
                "pests": {"type": "string", "description": "病害虫名のJSON配列（更新する場合）"},
                "vector": {"type": "string", "description": "10次元ベクトルのJSON配列（更新する場合）"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "delete_spray_history",
        "description": "防除記録を削除する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日付 YYYY-MM-DD"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "get_spray_schedule",
        "description": "防除暦（今後の予定）を取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "年で絞り込み（任意）"},
                "status": {"type": "string", "description": "ステータスで絞り込み（scheduled/done/missed/rescheduled、任意）"},
                "limit": {"type": "integer", "description": "最大取得件数（デフォルト200）"},
            },
            "required": [],
        },
    },
    {
        "name": "add_spray_schedule",
        "description": "新規の防除暦（予定）を追加する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "schedule_date": {"type": "string", "description": "予定日 YYYY-MM-DD"},
                "set_ids": {"type": "string", "description": "セット名のJSON配列（例: '[\"セット1\"]'）"},
                "pesticide_ids": {"type": "string", "description": "薬剤IDのJSON配列（例: '[\"P40\"]'）"},
                "trigger_type": {"type": "string", "description": "予定作成のきっかけ（cycle/observation/forecast、デフォルトcycle）"},
                "notes": {"type": "string", "description": "備考"},
            },
            "required": ["schedule_date"],
        },
    },
    {
        "name": "update_spray_schedule",
        "description": "既存の防除暦（予定）を更新する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "spray_scheduleのID"},
                "schedule_date": {"type": "string", "description": "予定日（変更する場合）"},
                "status": {"type": "string", "description": "ステータス（scheduled/done/missed/rescheduled）"},
                "actual_date": {"type": "string", "description": "実施日（実施済みにする場合）"},
                "set_ids": {"type": "string", "description": "セット名のJSON配列（更新する場合）"},
                "pesticide_ids": {"type": "string", "description": "薬剤IDのJSON配列（更新する場合）"},
                "notes": {"type": "string", "description": "備考（更新する場合）"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "delete_spray_schedule",
        "description": "防除暦（予定）を削除する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "spray_scheduleのID"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "prescribe",
        "description": "RBPエンジンに处方计算を依頼する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_vector": {"type": "string", "description": "10次元要求ベクトルのJSON配列"},
                "engine": {"type": "string", "description": "エンジン（python または haskell）"},
            },
            "required": ["entry_vector", "engine"],
        },
    },
    {
        "name": "summarize_history",
        "description": "防除履歴を要約する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "description": "期間（week=今週, month=今月, quarter=今四半期, year=今年）"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "get_current_season_advice",
        "description": "現在の季節に応じた一般的な防除アドバイスを返す。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "compare_pesticides",
        "description": "複数の薬剤を比較する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pesticide_ids": {"type": "string", "description": "薬剤IDのJSON配列（例: '[\"P01\",\"P02\"]'）"},
            },
            "required": ["pesticide_ids"],
        },
    },
    {
        "name": "get_usage_stats",
        "description": "使用統計を取得する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "年で絞り込み（任意）"},
            },
            "required": [],
        },
    },
    {
        "name": "send_to_slack",
        "description": "メッセージをSlackチャンネル(#all-stb)に送信する。ユーザーが『Slackに通知して』『メンバーに通知して』と言った時に使用する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Slackに送信するメッセージ本文（改行可）"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "prescribe_by_date",
        "description": "指定日付の防除記録にある病害虫に基づき、RBPエンジンで最適な薬剤を選定する。日付は日本語形式（2025年2月21日）でもOK。記録がない場合は季節推定に自動フォールバック。",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日付（日本語形式OK: 2025年2月21日, 2月21日, 今日, 明日, 先月 など。内部でYYYY-MM-DDに変換）"},
            },
            "required": ["date"],
        },
    },
    {
        "name": "seasonal_prescribe",
        "description": "月日を指定して季節に応じた病害虫を推定し、RBPエンジンで薬剤を選定する。記録がない日付の代替手段。",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "integer", "description": "月（1-12）"},
                "day": {"type": "integer", "description": "日（1-31）"},
            },
            "required": ["month", "day"],
        },
    },
    {
        "name": "retrieve_context",
        "description": "ユーザーの質問に関連する農薬・病害虫・記録のコンテキストを検索して返す。症状描述や自然言語の質問に対応。症状辞典で病害虫名を特定できれば既存のSQL検索にフォールバックする。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ（自然言語で記述: 症状描述、有機JAS対応、混用相談など）"},
                "limit": {"type": "integer", "description": "最大取得件数（デフォルト5）"},
                "source_type": {"type": "string", "description": "ソースタイプで絞り込み（任意: pesticide, disease, record, all）"},
            },
            "required": ["query"],
        },
    },
]


def get_tool_by_name(name: str):
    """ツール名から実際の関数を返す。"""
    _TOOLS_MAP = {
        "search_pesticides": search_pesticides,
        "list_pesticides": list_pesticides,
        "get_pesticide_detail": get_pesticide_detail,
        "list_diseases": list_diseases,
        "get_disease_detail": get_disease_detail,
        "get_spray_history": get_spray_history,
        "add_spray_history": add_spray_history,
        "update_spray_history": update_spray_history,
        "delete_spray_history": delete_spray_history,
        "get_spray_schedule": get_spray_schedule,
        "add_spray_schedule": add_spray_schedule,
        "update_spray_schedule": update_spray_schedule,
        "delete_spray_schedule": delete_spray_schedule,
        "prescribe": prescribe,
        "prescribe_by_date": prescribe_by_date,
        "seasonal_prescribe": seasonal_prescribe,
        "summarize_history": summarize_history,
        "get_current_season_advice": get_current_season_advice,
        "compare_pesticides": compare_pesticides,
        "get_usage_stats": get_usage_stats,
        "send_to_slack": send_to_slack,
        "retrieve_context": retrieve_context,
    }
    return _TOOLS_MAP.get(name)


def get_tool_names() -> list:
    """登録されているツール名のリストを返す。"""
    return [t["name"] for t in TOOL_REGISTRY]


def convert_tools_to_openai_format(tools=None):
    """
    TOOL_REGISTRY（または指定リスト）を OpenAI 互換のツール定義に変換。

    ローカルLLM（LiteLLM/OpenAI互換API）で使用するためのフォーマット。
    """
    if tools is None:
        tools = TOOL_REGISTRY
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


# =====================================================================
# TOOL: search_pesticides
# =====================================================================
def search_pesticides(keyword: str, category: Optional[str] = None) -> str:
    """
    病害虫名や薬剤名で検索して、該当する薬剤を一覧で返す。

    Args:
        keyword: 検索キーワード（薬剤名・有効成分・病害虫名のいずれかで部分一致）
        category: 類別で絞り込み（"fungicide"=殺菌剤, "insecticide"=殺虫剤, "acaricide"=殺ダニ剤）

    Returns:
        JSON文字列（薬剤リスト）
    """
    conn = get_db()

    # --- Phase 1: SQL LIKE (matches name, activeIngredient, category, and
    #         targetNames when stored as plain UTF-8) ---
    query = """
        SELECT * FROM pesticides
        WHERE (name LIKE ? OR activeIngredient LIKE ? OR category LIKE ?)
    """
    params = [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY id"
    rows = conn.execute(query, params).fetchall()

    # --- Phase 2: targetNames is sometimes stored as JSON with Unicode escapes
    #         (e.g. "[\"\\u70ad\\u75bd\"]"). Decode and filter in Python. ---
    all_rows = conn.execute("SELECT * FROM pesticides ORDER BY id").fetchall()
    decoded_rows = []
    # Normalize keyword: strip trailing "病" so "炭疽病" → "炭疽" for matching
    norm_keyword = keyword.rstrip("病")
    candidates = {norm_keyword, keyword}
    for r in all_rows:
        tn_raw = r["targetNames"]
        try:
            tn_list = json.loads(tn_raw) if isinstance(tn_raw, str) else tn_raw
            if isinstance(tn_list, list) and any(
                any(c in t for c in candidates) for t in tn_list
            ):
                decoded_rows.append(r)
        except (json.JSONDecodeError, TypeError):
            pass

    # Prefer Phase 1 results (name/ingredient/category match), fall back to Phase 2
    if not rows:
        rows = decoded_rows

    conn.close()

    result = [_parse_row(r) for r in rows]
    return json.dumps(result, ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: list_pesticides
# =====================================================================
def list_pesticides(category: Optional[str] = None, limit: int = 100) -> str:
    """
    薬剤一覧を取得する。类别で絞り込み可能。

    Args:
        category: 類別で絞り込み（"fungicide", "insecticide", "acaricide"）
        limit: 最大取得件数（デフォルト100）

    Returns:
        JSON文字列（薬剤リスト）
    """
    conn = get_db()
    if category:
        rows = conn.execute(
            "SELECT * FROM pesticides WHERE category = ? ORDER BY id LIMIT ?",
            (category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pesticides ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
    conn.close()

    result = [_parse_row(r) for r in rows]
    return json.dumps(result, ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: get_pesticide_detail
# =====================================================================
def get_pesticide_detail(pesticide_id: str) -> str:
    """
    特定の薬剤の詳細情報を取得する。

    Args:
        pesticide_id: 薬剤ID（例: "P01"）

    Returns:
        JSON文字列（薬剤詳細）
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM pesticides WHERE id = ?", (pesticide_id,)).fetchone()
    conn.close()

    if not row:
        return json.dumps({"error": f"薬剤 {pesticide_id} が見つかりません"}, ensure_ascii=False)

    return json.dumps(_parse_row(row), ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: list_diseases
# =====================================================================
def list_diseases(disease_type: Optional[str] = None) -> str:
    """
    病害虫マスター一覧を取得する。

    Args:
        disease_type: タイプで絞り込み（"disease"=病害, "pest"=害虫）

    Returns:
        JSON文字列（病害虫リスト）
    """
    conn = get_db()
    if disease_type:
        rows = conn.execute(
            "SELECT * FROM diseases WHERE type = ? ORDER BY id",
            (disease_type,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM diseases ORDER BY id").fetchall()
    conn.close()

    return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: get_disease_detail
# =====================================================================
def get_disease_detail(disease_id: int) -> str:
    """
    特定の病害虫の詳細情報を取得する。

    Args:
        disease_id: 病害虫ID（例: 0）

    Returns:
        JSON文字列（病害虫詳細）
    """
    conn = get_db()
    row = conn.execute("SELECT * FROM diseases WHERE id = ?", (int(disease_id),)).fetchone()
    conn.close()

    if not row:
        return json.dumps({"error": f"病害虫 ID {disease_id} が見つかりません"}, ensure_ascii=False)

    return json.dumps(dict(row), ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: get_spray_history
# =====================================================================
def get_spray_history(year: Optional[int] = None, month: Optional[int] = None,
                date_from: Optional[str] = None, date_to: Optional[str] = None,
                limit: int = 365) -> str:
    """
    防除履歴を取得する。

    Args:
        year: 年で絞り込み（例: 2025）
        month: 月で絞り込み（例: 8）
        date_from: 開始日（YYYY-MM-DD形式）
        date_to: 終了日（YYYY-MM-DD形式）
        limit: 最大取得件数（デフォルト365）

    Returns:
        JSON文字列（防除履歴リスト）
    """
    conn = get_db()

    if year and month:
        date_str = f"{year}-{month:02d}"
        rows = conn.execute(
            "SELECT * FROM spray_history WHERE date LIKE ? ORDER BY date DESC LIMIT ?",
            (f"{date_str}%", limit),
        ).fetchall()
    elif date_from and date_to:
        rows = conn.execute(
            "SELECT * FROM spray_history WHERE date BETWEEN ? AND ? ORDER BY date DESC LIMIT ?",
            (date_from, date_to, limit),
        ).fetchall()
    elif year:
        date_str = f"{year}"
        rows = conn.execute(
            "SELECT * FROM spray_history WHERE date LIKE ? ORDER BY date DESC LIMIT ?",
            (f"{date_str}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM spray_history ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()

    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d["pests"] = json.loads(d["pests"]) if isinstance(d["pests"], str) else d["pests"]
        d["vector"] = json.loads(d["vector"]) if isinstance(d["vector"], str) else d["vector"]
        result.append(d)

    return json.dumps(result, ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: add_spray_history
# =====================================================================
def add_spray_history(date: str, pests: list, vector: list) -> str:
    """
    新規防除記録を追加する。

    Args:
        date: 日付（YYYY-MM-DD形式）
        pests: 病害虫名のリスト（例: ["炭疽病", "うどんこ病"]）
        vector: 10次元ベクトル（0/1のリスト）

    Returns:
        JSON文字列（結果）
    """
    if len(vector) != VECTOR_DIM:
        return json.dumps({"error": f"ベクトルは{VECTOR_DIM}次元が必要です"}, ensure_ascii=False)

    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO spray_history (date, pests, vector) VALUES (?, ?, ?)",
        (date, json.dumps(pests), json.dumps(vector)),
    )
    conn.commit()
    conn.close()

    return json.dumps({
        "status": "created",
        "date": date,
        "pests": pests,
    }, ensure_ascii=False)


# =====================================================================
# TOOL: update_spray_history
# =====================================================================
def update_spray_history(date: str, pests: Optional[list] = None,
                  vector: Optional[list] = None) -> str:
    """
    既存の防除記録を更新する。

    Args:
        date: 日付（YYYY-MM-DD形式）
        pests: 病害虫名のリスト（更新する場合）
        vector: 10次元ベクトル（更新する場合）

    Returns:
        JSON文字列（結果）
    """
    conn = get_db()
    existing = conn.execute("SELECT * FROM spray_history WHERE date = ?", (date,)).fetchone()

    if not existing:
        conn.close()
        return json.dumps({"error": f"日付 {date} の記録が見つかりません"}, ensure_ascii=False)

    if pests is not None:
        if vector is not None and len(vector) != VECTOR_DIM:
            conn.close()
            return json.dumps({"error": f"ベクトルは{VECTOR_DIM}次元が必要です"}, ensure_ascii=False)
        conn.execute(
            "UPDATE spray_history SET pests = ?, vector = ? WHERE date = ?",
            (json.dumps(pests), json.dumps(vector), date),
        )
    else:
        conn.execute("DELETE FROM spray_history WHERE date = ?", (date,))

    conn.commit()
    conn.close()

    return json.dumps({
        "status": "updated",
        "date": date,
        "pests": pests,
    }, ensure_ascii=False)


# =====================================================================
# TOOL: delete_spray_history
# =====================================================================
def delete_spray_history(date: str) -> str:
    """
    防除記録を削除する。

    Args:
        date: 日付（YYYY-MM-DD形式）

    Returns:
        JSON文字列（結果）
    """
    conn = get_db()
    cur = conn.execute("DELETE FROM spray_history WHERE date = ?", (date,))
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        return json.dumps({"error": f"日付 {date} の記録が見つかりません"}, ensure_ascii=False)

    return json.dumps({"status": "deleted", "date": date}, ensure_ascii=False)


# =====================================================================
# TOOL: get_spray_schedule
# =====================================================================
def get_spray_schedule(year: Optional[int] = None, status: Optional[str] = None,
                        limit: int = 200) -> str:
    """
    防除暦（今後の予定）を取得する。

    Args:
        year: 年で絞り込み（例: 2026）
        status: ステータスで絞り込み（scheduled/done/missed/rescheduled）
        limit: 最大取得件数（デフォルト200）

    Returns:
        JSON文字列（防除暦リスト）
    """
    conn = get_db()

    conditions = []
    params = []
    if year:
        conditions.append("schedule_date LIKE ?")
        params.append(f"{year}%")
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT * FROM spray_schedule {where} ORDER BY schedule_date LIMIT ?",
        params,
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d["set_ids"] = json.loads(d["set_ids"]) if d["set_ids"] else []
        d["pesticide_ids"] = json.loads(d["pesticide_ids"]) if d["pesticide_ids"] else []
        d["rb_out_json"] = json.loads(d["rb_out_json"]) if d["rb_out_json"] else None
        result.append(d)

    return json.dumps(result, ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: add_spray_schedule
# =====================================================================
def add_spray_schedule(schedule_date: str, set_ids: Optional[list] = None,
                        pesticide_ids: Optional[list] = None,
                        trigger_type: str = "cycle", notes: Optional[str] = None) -> str:
    """
    新規の防除暦（予定）を追加する。

    Args:
        schedule_date: 予定日（YYYY-MM-DD形式）
        set_ids: セット名のリスト（例: ["セット1"]）
        pesticide_ids: 薬剤IDのリスト（例: ["P40"]）
        trigger_type: 予定作成のきっかけ（cycle/observation/forecast、デフォルトcycle）
        notes: 備考

    Returns:
        JSON文字列（結果）
    """
    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO spray_schedule
           (schedule_date, status, trigger_type, set_ids, pesticide_ids, notes,
            created_at, updated_at)
           VALUES (?, 'scheduled', ?, ?, ?, ?, ?, ?)""",
        (
            schedule_date, trigger_type,
            json.dumps(set_ids or []), json.dumps(pesticide_ids or []),
            notes, now, now,
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()

    return json.dumps({
        "status": "created",
        "id": new_id,
        "schedule_date": schedule_date,
    }, ensure_ascii=False)


# =====================================================================
# TOOL: update_spray_schedule
# =====================================================================
def update_spray_schedule(id: int, schedule_date: Optional[str] = None,
                           status: Optional[str] = None,
                           actual_date: Optional[str] = None,
                           set_ids: Optional[list] = None,
                           pesticide_ids: Optional[list] = None,
                           notes: Optional[str] = None) -> str:
    """
    既存の防除暦（予定）を更新する。

    Args:
        id: spray_scheduleのID
        schedule_date: 予定日（変更する場合）
        status: ステータス（scheduled/done/missed/rescheduled）
        actual_date: 実施日（実施済みにする場合）
        set_ids: セット名のリスト（更新する場合）
        pesticide_ids: 薬剤IDのリスト（更新する場合）
        notes: 備考（更新する場合）

    Returns:
        JSON文字列（結果）
    """
    conn = get_db()
    existing = conn.execute("SELECT * FROM spray_schedule WHERE id = ?", (id,)).fetchone()

    if not existing:
        conn.close()
        return json.dumps({"error": f"ID {id} の防除暦が見つかりません"}, ensure_ascii=False)

    now = datetime.utcnow().isoformat()
    conn.execute(
        """UPDATE spray_schedule SET
           schedule_date = ?, status = ?, actual_date = ?, set_ids = ?,
           pesticide_ids = ?, notes = ?, updated_at = ?
           WHERE id = ?""",
        (
            schedule_date if schedule_date is not None else existing["schedule_date"],
            status if status is not None else existing["status"],
            actual_date if actual_date is not None else existing["actual_date"],
            json.dumps(set_ids) if set_ids is not None else existing["set_ids"],
            json.dumps(pesticide_ids) if pesticide_ids is not None else existing["pesticide_ids"],
            notes if notes is not None else existing["notes"],
            now,
            id,
        ),
    )
    conn.commit()
    conn.close()

    return json.dumps({"status": "updated", "id": id}, ensure_ascii=False)


# =====================================================================
# TOOL: delete_spray_schedule
# =====================================================================
def delete_spray_schedule(id: int) -> str:
    """
    防除暦（予定）を削除する。

    Args:
        id: spray_scheduleのID

    Returns:
        JSON文字列（結果）
    """
    conn = get_db()
    cur = conn.execute("DELETE FROM spray_schedule WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        return json.dumps({"error": f"ID {id} の防除暦が見つかりません"}, ensure_ascii=False)

    return json.dumps({"status": "deleted", "id": id}, ensure_ascii=False)


# =====================================================================
# TOOL: prescribe
# =====================================================================
def prescribe(entry_vector: list, engine: str = "js") -> str:
    """
    RBPエンジンに处方计算を依頼する。

    Args:
        entry_vector: 10次元要求ベクトル（0/1のリスト）
        engine: エンジン（"js", "python", "haskell"）

    Returns:
        JSON文字列（处方结果）
    """
    if len(entry_vector) != VECTOR_DIM:
        return json.dumps({"error": f"ベクトルは{VECTOR_DIM}次元が必要です"}, ensure_ascii=False)

    # JS引擎在浏览器端运行，这里只处理python/haskell
    if engine == "js":
        return json.dumps({
            "error": "JS引擎需要在浏览器端运行。请使用Python或Haskell引擎。",
        }, ensure_ascii=False)

    # Python engine
    if engine == "python":
        py_dir = os.path.join(APP_ROOT, "rbp-algebra-python")
        if os.path.isdir(py_dir):
            import sys
            sys.path.insert(0, py_dir)
            try:
                import api as py_api
                result = py_api.prescribe(entry_vector)
                return json.dumps(result, ensure_ascii=False, indent=2)
            except ImportError:
                pass
            finally:
                sys.path.pop(0)

        return json.dumps({
            "error": "Python引擎未找到（rbp-algebra-python/ が必要です）",
        }, ensure_ascii=False)

    return json.dumps({"error": f"未知引擎: {engine}。请选择 'python' 或 'haskell'。"}, ensure_ascii=False)


# =====================================================================
# TOOL: summarize_history
# =====================================================================
def summarize_history(period: str = "month") -> str:
    """
    防除履歴を要約する。

    Args:
        period: 期間（"week"=今週, "month"=今月, "quarter"=今四半期, "year"=今年）

    Returns:
        JSON文字列（要約テキスト）
    """
    conn = get_db()
    today = datetime.now()

    if period == "week":
        start = today - timedelta(days=today.weekday())  # Monday
    elif period == "month":
        start = today.replace(day=1)
    elif period == "quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=quarter_start_month, day=1)
    elif period == "year":
        start = today.replace(month=1, day=1)
    else:
        start = today - timedelta(days=30)

    date_from = start.strftime("%Y-%m-%d")
    date_to = today.strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT * FROM spray_history WHERE date BETWEEN ? AND ? ORDER BY date",
        (date_from, date_to),
    ).fetchall()
    conn.close()

    if not rows:
        return json.dumps({
            "period": period,
            "date_range": f"{date_from} 〜 {date_to}",
            "total_records": 0,
            "summary": "該当期間の防除記録はありません",
        }, ensure_ascii=False)

    # Aggregate stats
    all_pests = []
    total_records = len(rows)
    dates = []

    for r in rows:
        d = dict(r)
        pests = json.loads(d["pests"]) if isinstance(d["pests"], str) else d["pests"]
        all_pests.extend(pests)
        dates.append(d["date"])

    # Pest frequency
    pest_freq = {}
    for p in all_pests:
        pest_freq[p] = pest_freq.get(p, 0) + 1

    sorted_pests = sorted(pest_freq.items(), key=lambda x: -x[1])

    summary_lines = [
        f"期間: {date_from} 〜 {date_to}（{period}）",
        f"総記録数: {total_records}件",
        f"発生した病害虫（頻度順）:",
    ]
    for pest, count in sorted_pests:
        summary_lines.append(f"  - {pest}: {count}回")

    summary_text = "\n".join(summary_lines)

    return json.dumps({
        "period": period,
        "date_range": f"{date_from} 〜 {date_to}",
        "total_records": total_records,
        "pest_frequency": dict(sorted_pests),
        "summary": summary_text,
    }, ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: get_current_season_advice
# =====================================================================
def get_current_season_advice() -> str:
    """
    現在の季節に応じた一般的な防除アドバイスを返す。

    Returns:
        JSON文字列（アドバイス）
    """
    month = datetime.now().month

    season_advice = {
        1: "冬季は低温で病害虫の活動が低下しますが、ハウス内は要注意。灰色かび病の予防散布を行いましょう。",
        2: "早春は寒暖差が大きく、病害が発生しやすくなります。うどんこ病・灰色かび病の予防を強化しましょう。",
        3: "春先の暖かさに合わせて病害虫が活動を開始します。早期発見・早期対応が重要です。",
        4: "気温上昇とともに病害虫の発生が増加します。定期的な scouting（巡回観察）を行いましょう。",
        5: "梅雨前に殺菌剤の予防散布を完了させましょう。うどんこ病・炭疽病に注意。",
        6: "梅雨時期は湿度が高いため、灰色かび病・炭疽病の発生リスクが高まります。排水対策も同時に行いましょう。",
        7: "夏季は高温多湿で病害虫が活発になります。アブラムシ・コナジラミ等の吸汁害虫に注意。",
        8: "夏末期も病害虫対策を継続。秋口の病害発生に備え、植株の健康管理を徹底しましょう。",
        9: "秋季は台風による被害リスク。物理的損傷からの二次感染（炭疽病等）に注意。",
        10: "秋口は気温下降で病害の発生様式が変わります。うどんこ病の発生が増える時期です。",
        11: "晩秋は防寒対策と冬季準備の時期。来年の栽培に備え、土壌消毒・器具の整備を行いましょう。",
        12: "冬季入り。ハウス内の温度管理と病害予防散布を徹底しましょう。",
    }

    advice = season_advice.get(month, "季節に応じた防除アドバイス")

    return json.dumps({
        "season": f"{month}月",
        "advice": advice,
    }, ensure_ascii=False)


# =====================================================================
# TOOL: compare_pesticides
# =====================================================================
def compare_pesticides(pesticide_ids: list) -> str:
    """
    複数の薬剤を比較する。

    Args:
        pesticide_ids: 薬剤IDのリスト（例: ["P01", "P02"]）

    Returns:
        JSON文字列（比較結果）
    """
    conn = get_db()
    placeholders = ",".join(["?"] * len(pesticide_ids))
    rows = conn.execute(
        f"SELECT * FROM pesticides WHERE id IN ({placeholders})",
        pesticide_ids,
    ).fetchall()
    conn.close()

    if not rows:
        return json.dumps({"error": "指定された薬剤が見つかりません"}, ensure_ascii=False)

    drugs = [_parse_row(r) for r in rows]

    # Build comparison table
    comparison = {
        "drugs": [],
        "recommendation": "",
    }

    for d in drugs:
        comparison["drugs"].append({
            "id": d["id"],
            "name": d["name"],
            "category": d.get("category", ""),
            "activeIngredient": d.get("activeIngredient", ""),
            "targets": d.get("targetNames", []),
            "phiDays": d.get("phiDays", 0),
            "maxApplications": d.get("maxApplications", "無制限"),
            "toxicityClass": d.get("toxicityClass", ""),
            "system": d.get("system", ""),
            "systemCode": d.get("systemCode", ""),
        })

    # Simple recommendation logic
    if len(drugs) == 2:
        a, b = drugs[0], drugs[1]
        tips = []
        if a.get("phiDays", 0) < b.get("phiDays", 0):
            tips.append(f"{a['name']}の方がPHI（収穫待期日）が短いです")
        if a.get("toxicityClass") == "普通物" and b.get("toxicityClass") != "普通物":
            tips.append(f"{a['name']}の方が毒性区分が低いです")
        if a.get("maxApplications", 0) > b.get("maxApplications", 0):
            tips.append(f"{a['name']}の方が散布回数の上限が高いです")
        comparison["recommendation"] = "；".join(tips) if tips else "両剤とも同等の特性です。用途に合わせて選択してください。"

    return json.dumps(comparison, ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: get_usage_stats
# =====================================================================
def get_usage_stats(year: Optional[int] = None) -> str:
    """
    使用統計を取得する。

    Args:
        year: 年で絞り込み（None=all time）

    Returns:
        JSON文字列（統計）
    """
    conn = get_db()

    if year:
        rows = conn.execute(
            "SELECT * FROM spray_history WHERE date LIKE ? ORDER BY date",
            (f"{year}%",),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM spray_history ORDER BY date").fetchall()
    conn.close()

    if not rows:
        return json.dumps({"error": "記録が見つかりません"}, ensure_ascii=False)

    # Stats
    total_records = len(rows)
    pest_counts = {}
    date_range_start = None
    date_range_end = None

    for r in rows:
        d = dict(r)
        pests = json.loads(d["pests"]) if isinstance(d["pests"], str) else d["pests"]
        for p in pests:
            pest_counts[p] = pest_counts.get(p, 0) + 1
        if date_range_start is None or d["date"] < date_range_start:
            date_range_start = d["date"]
        if date_range_end is None or d["date"] > date_range_end:
            date_range_end = d["date"]

    sorted_pests = sorted(pest_counts.items(), key=lambda x: -x[1])

    return json.dumps({
        "total_records": total_records,
        "date_range": f"{date_range_start} 〜 {date_range_end}",
        "pest_frequency": dict(sorted_pests),
    }, ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: prescribe_by_date
# =====================================================================
def prescribe_by_date(date: str) -> str:
    """
    指定日付の病害虫記録に基づき、RBPエンジンで最適な薬剤を選定する。

    日本語の日付形式にも対応:
      - "2025年2月21日" → "2025-02-21" に自動変換
      - "2月21日" → 今年の2月21日を仮定
      - "今日" / "明日" / "先月" などの相対日付も可

    Args:
        date: 日付（"2025年2月21日" や "2025-02-21" 等形式自由）

    Returns:
        JSON文字列（RBP処方結果 + 薬剤詳細）
    """
    # Parse Japanese date format → YYYY-MM-DD
    normalized_date = parse_japanese_date(date)
    if normalized_date is None:
        # Fallback: use as-is (may still be YYYY-MM-DD)
        normalized_date = date.strip()

    conn = get_db()

    # Look up record for the given date
    row = conn.execute("SELECT * FROM spray_history WHERE date = ?", (normalized_date,)).fetchone()

    if not row:
        # Record not found → fall back to seasonal prescription
        conn.close()
        # Extract month/day from the normalized date for seasonal lookup
        try:
            dt = datetime.strptime(normalized_date, "%Y-%m-%d")
            month, day = dt.month, dt.day
        except ValueError:
            return json.dumps({
                "error": f"日付 {date} の防除記録が見つかりません",
                "hint": "記録がない場合は季節推定を使いました。もう一度お試しください。",
            }, ensure_ascii=False)

        return seasonal_prescribe_internal(month, day, original_input=date)

    pests = json.loads(row["pests"]) if isinstance(row["pests"], str) else row["pests"]
    vector = json.loads(row["vector"]) if isinstance(row["vector"], str) else row["vector"]

    # Call RBP engine
    try:
        py_dir = os.path.join(APP_ROOT, "rbp-algebra-python")
        if os.path.isdir(py_dir):
            import sys
            sys.path.insert(0, py_dir)
            try:
                import api as py_api
                rbp_result = py_api.prescribe(vector)
            finally:
                sys.path.pop(0)
        else:
            rbp_result = {"error": "Python RBPエンジンが見つかりません"}
    except Exception as e:
        rbp_result = {"error": f"RBPエンジンエラー: {str(e)}"}

    # Enrich with pesticide details from DB
    pesticide_ids = set()
    if "best" in rbp_result and "pesticides" in rbp_result["best"]:
        for p in rbp_result["best"]["pesticides"]:
            pid = p.get("id")
            if pid:
                pesticide_ids.add(pid)
    for alt in rbp_result.get("alternatives", []):
        for p in alt.get("pesticides", []):
            pid = p.get("id")
            if pid:
                pesticide_ids.add(pid)

    # Fetch full details from DB
    enriched_drugs = []
    if pesticide_ids:
        placeholders = ",".join(["?"] * len(pesticide_ids))
        rows = conn.execute(
            f"SELECT * FROM pesticides WHERE id IN ({placeholders})",
            list(pesticide_ids),
        ).fetchall()
        for r in rows:
            enriched_drugs.append(_parse_row(r))

    conn.close()

    # Build response
    response = {
        "date": normalized_date,
        "original_input": date,
        "pests": pests,
        "vector": vector,
        "rbp_status": rbp_result.get("status", "UNKNOWN"),
        "best_match": rbp_result.get("best", {}),
        "alternatives_count": len(rbp_result.get("alternatives", [])),
        "pesticide_details": enriched_drugs,
    }

    return json.dumps(response, ensure_ascii=False, indent=2)


# =====================================================================
# TOOL: seasonal_prescribe
# =====================================================================
def _run_rbp_and_enrich(conn, vector):
    """Run RBP engine on a vector and enrich results with DB pesticide details."""
    try:
        py_dir = os.path.join(APP_ROOT, "rbp-algebra-python")
        if os.path.isdir(py_dir):
            import sys
            sys.path.insert(0, py_dir)
            try:
                import api as py_api
                rbp_result = py_api.prescribe(vector)
            finally:
                sys.path.pop(0)
        else:
            rbp_result = {"error": "Python RBPエンジンが見つかりません"}
    except Exception as e:
        rbp_result = {"error": f"RBPエンジンエラー: {str(e)}"}

    # Enrich with pesticide details
    pesticide_ids = set()
    if "best" in rbp_result and "pesticides" in rbp_result["best"]:
        for p in rbp_result["best"]["pesticides"]:
            pid = p.get("id")
            if pid:
                pesticide_ids.add(pid)
    for alt in rbp_result.get("alternatives", []):
        for p in alt.get("pesticides", []):
            pid = p.get("id")
            if pid:
                pesticide_ids.add(pid)

    enriched_drugs = []
    if pesticide_ids:
        placeholders = ",".join(["?"] * len(pesticide_ids))
        rows = conn.execute(
            f"SELECT * FROM pesticides WHERE id IN ({placeholders})",
            list(pesticide_ids),
        ).fetchall()
        for r in rows:
            enriched_drugs.append(_parse_row(r))

    return rbp_result, enriched_drugs


def seasonal_prescribe_internal(month: int, day: int, original_input: str = None) -> str:
    """
    Internal: seasonal prescription logic shared by prescribe_by_date fallback.
    """
    conn = get_db()

    # Find nearest historical record in the same month
    rows = conn.execute(
        "SELECT * FROM spray_history WHERE strftime('%m', date) = ? ORDER BY ABS(CAST(strftime('%d', date) AS INTEGER) - ?) LIMIT 1",
        (f"{month:02d}", day),
    ).fetchone()

    if rows:
        pests = json.loads(rows["pests"]) if isinstance(rows["pests"], str) else rows["pests"]
        vector = json.loads(rows["vector"]) if isinstance(rows["vector"], str) else rows["vector"]
        source = f"類似記録: {rows['date']}"
    else:
        # Seasonal heuristic
        seasonal_diseases = {
            1: [("灰色かび病", [0, 1, 0, 0, 0, 0, 0, 0, 0, 0])],
            2: [("灰色かび病", [0, 1, 0, 0, 0, 0, 0, 0, 0, 0]), ("うどんこ病", [0, 0, 1, 0, 0, 0, 0, 0, 0, 0])],
            3: [("うどんこ病", [0, 0, 1, 0, 0, 0, 0, 0, 0, 0]), ("炭疽病", [1, 0, 0, 0, 0, 0, 0, 0, 0, 0])],
            4: [("炭疽病", [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]), ("うどんこ病", [0, 0, 1, 0, 0, 0, 0, 0, 0, 0])],
            5: [("炭疽病", [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]), ("うどんこ病", [0, 0, 1, 0, 0, 0, 0, 0, 0, 0])],
            6: [("炭疽病", [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]), ("灰色かび病", [0, 1, 0, 0, 0, 0, 0, 0, 0, 0])],
            7: [("アブラムシ", [0, 0, 0, 0, 0, 0, 1, 0, 0, 0]), ("コナジラミ", [0, 0, 0, 0, 0, 0, 0, 1, 0, 0])],
            8: [("アブラムシ", [0, 0, 0, 0, 0, 0, 1, 0, 0, 0]), ("ハダニ", [0, 0, 0, 1, 0, 0, 0, 0, 0, 0])],
            9: [("炭疽病", [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]), ("アブラムシ", [0, 0, 0, 0, 0, 0, 1, 0, 0, 0])],
            10: [("うどんこ病", [0, 0, 1, 0, 0, 0, 0, 0, 0, 0]), ("炭疽病", [1, 0, 0, 0, 0, 0, 0, 0, 0, 0])],
            11: [("灰色かび病", [0, 1, 0, 0, 0, 0, 0, 0, 0, 0])],
            12: [("灰色かび病", [0, 1, 0, 0, 0, 0, 0, 0, 0, 0])],
        }
        diseases = seasonal_diseases.get(month, [])
        if not diseases:
            conn.close()
            return json.dumps({
                "error": f"月 {month} の季節情報は準備中です",
            }, ensure_ascii=False)

        # Combine vectors (OR operation)
        combined = [0] * VECTOR_DIM
        pests = []
        for name, vec in diseases:
            pests.append(name)
            for i in range(VECTOR_DIM):
                combined[i] |= vec[i]

        vector = combined
        source = "季節推定"

    # Call RBP engine and enrich
    rbp_result, enriched_drugs = _run_rbp_and_enrich(conn, vector)
    conn.close()

    response = {
        "month": month,
        "day": day,
        "original_input": original_input,
        "estimated_pests": pests,
        "vector": vector,
        "source": source,
        "rbp_status": rbp_result.get("status", "UNKNOWN"),
        "best_match": rbp_result.get("best", {}),
        "alternatives_count": len(rbp_result.get("alternatives", [])),
        "pesticide_details": enriched_drugs,
    }

    return json.dumps(response, ensure_ascii=False, indent=2)


def seasonal_prescribe(month: int, day: int) -> str:
    """
    月日を指定して季節に応じた病害虫を推定し、RBPエンジンで薬剤を選定する。
    記録がない日付の代替手段。

    Args:
        month: 月（1-12）
        day: 日（1-31）

    Returns:
        JSON文字列（季節推定 + RBP処方結果）
    """
    return seasonal_prescribe_internal(month, day)


# =====================================================================
# TOOL: send_to_slack
# =====================================================================
def send_to_slack(message: str) -> str:
    """
    メッセージをSlackチャンネル(#all-stb)に送信する。

    Args:
        message: Slackに送信するメッセージ本文

    Returns:
        JSON文字列（送信結果）
    """
    from chat_client import send_message
    result = send_message(message)
    return json.dumps(result, ensure_ascii=False)


# =====================================================================
# TOOL: retrieve_context
# =====================================================================
def _lookup_symptom_dict(query: str) -> list:
    """
    症状辞典でクエリにマッチする病害虫名を探索する。

    Args:
        query: 検索クエリ（自然言語の症状描述など）

    Returns:
        マッチした病害虫名のリスト（重複なし）
    """
    matched = set()
    query_lower = query.lower()

    for pattern, diseases in SYMPTOM_DICTIONARY.items():
        if pattern in query_lower:
            matched.update(diseases)

    return sorted(matched)


def retrieve_context(query: str, limit: int = 5,
                     source_type: str = "all") -> str:
    """
    RAGで関連コンテキストを検索する。

    フロー:
      1. 症状辞典で病害虫名を特定（あればSQL検索にフォールバック）
      2. セマンティック検索で類似チャンクを取得
      3. 結果をJSONで返す

    Args:
        query: 検索クエリ（自然言語）
        limit: 最大取得件数（デフォルト5）
        source_type: ソースタイプで絞り込み（"pesticide", "disease",
                     "record", "all"）

    Returns:
        JSON文字列（検索結果 + 使用手法）
    """
    # Step 1: 症状辞典チェック
    matched_diseases = _lookup_symptom_dict(query)
    if matched_diseases:
        # 病害虫名が特定できた → 既存の強力なSQL検索に委譲
        results = []
        for disease in matched_diseases:
            try:
                pest_result = search_pesticides(disease)
                parsed = json.loads(pest_result)
                if isinstance(parsed, list) and parsed:
                    results.append({
                        "matched_symptom_disease": disease,
                        "pesticides": parsed[:limit],
                    })
            except Exception:
                pass

        return json.dumps({
            "method": "symptom_lookup",
            "matched_diseases": matched_diseases,
            "results": results,
        }, ensure_ascii=False, indent=2)

    # Step 2: セマンティック検索
    rag = get_rag_store()
    chunks = rag.search(query, limit=limit, source_type=source_type)

    return json.dumps({
        "method": "semantic_search",
        "query": query,
        "source_type_filter": source_type,
        "num_chunks": len(chunks),
        "chunks": [
            {
                "type": c["chunk"]["type"],
                "source_id": c["chunk"]["source_id"],
                "source_type": c["chunk"]["source_type"],
                "source_ref": c["chunk"]["source_ref"],
                "text": c["chunk"]["text"],
                "score": round(c["score"], 4),
            }
            for c in chunks
        ],
    }, ensure_ascii=False, indent=2)


# =====================================================================
# INTEGRATED PIPELINE: RAG symptom estimation → RBP prescription
# =====================================================================

# Disease name aliases for fuzzy matching (症状表現 → 正式病害虫名)
_DISEASE_ALIASES = {
    # 炭疽病
    "炭疽": "炭疽病", "かんそ": "炭疽病", "かんそびょう": "炭疽病",
    "葉に黒い斑点": "炭疽病", "葉に茶色い斑点": "炭疽病",
    "実が腐る": "炭疽病", "実が腐ってる": "炭疽病",
    # 灰色かび病
    "灰色かび": "灰色かび病", "gray mold": "灰色かび病",
    "葉にぬめり": "灰色かび病", "葉が萎れる": "灰色かび病",
    "葉が枯れる": "灰色かび病", "花が落ちる": "灰色かび病",
    "茎が柔らかい": "灰色かび病", "茎が腐る": "灰色かび病",
    # うどんこ病
    "うどんこ": "うどんこ病", "powdery mildew": "うどんこ病",
    "葉に白い粉": "うどんこ病", "白い粉が吹く": "うどんこ病",
    "白い粉": "うどんこ病", "葉が白くなる": "うどんこ病",
    "葉が白っぽくなる": "うどんこ病",
    # ハダニ系
    "ハダニ": "ハダニ", "ナミハダニ": "ナミハダニ",
    "蜘蛛の巣": "ハダニ", "糸状の蜘蛛": "ハダニ",
    "葉に細かい白点": "ハダニ",
    # ヨトウ系
    "ヨトウ": "ハスモンヨトウ", "ヨトウムシ": "ハスモンヨトウ",
    "葉に穴": "ハスモンヨトウ", "葉に穴が開く": "ハスモンヨトウ",
    "葉に穴があいてる": "ハスモンヨトウ", "葉が欠ける": "ハスモンヨトウ",
    # タバコガ系
    "タバコガ": "オオタバコガ", "オオタバコガ": "オオタバコガ",
    # アザミウマ系
    "アザミウマ": "ミカンキイロアザミウマ", "アザミ": "ミカンキイロアザミウマ",
    "葉に銀色の跡": "ミカンキイロアザミウマ", "葉に透明感": "ミカンキイロアザミウマ",
    # アブラムシ系
    "アブラムシ": "アブラムシ", "ワタアブラムシ": "ワタアブラムシ",
    "葉がねばねば": "アブラムシ", "ハチミツみたいな": "アブラムシ",
    "葉っぱが黄色くなる": "アブラムシ", "葉が黄色くなる": "アブラムシ",
    "葉が丸まる": "アブラムシ", "葉が巻く": "アブラムシ",
    "葉が縮れる": "アブラムシ", "葉の裏に小さい虫": "アブラムシ",
    "葉の裏に虫": "アブラムシ",
    # コナジラミ系
    "コナジラミ": "コナジラミ", "白い虫": "コナジラミ",
    "葉の裏に白い虫": "コナジラミ",
}


def _estimate_diseases_from_text(text: str) -> tuple[list[str], float]:
    """
    自然言語の症状記述から病害虫名を推定する。

    アルゴリズム:
      1. 症状辞典パターンで直接マッチ（最優先）
      2. 症状辞典がヒットしない場合 → 別名辞典で部分一致
      3. それでもヒットしない場合 → Embedding+FAISSでセマンティック検索

    Args:
        text: 症状記述（自然言語）

    Returns:
        (推定病害虫名のリスト, 信頼度 0-1)
    """
    conn = get_db()
    try:
        # Step 1: 症状辞典でマッチ
        matched = _lookup_symptom_dict(text)

        if matched:
            # 症状辞典がマッチすればそれで完結（別名辞典は使わない）
            return sorted(matched), 0.9

        # Step 2: 症状辞典がヒットしなかった場合のみ別名辞典を試す
        text_lower = text.lower()
        for alias, disease in _DISEASE_ALIASES.items():
            if alias.lower() in text_lower:
                matched.append(disease)

        if matched:
            return sorted(matched), 0.7

        # Step 3: Embedding+FAISS セマンティック検索
        rag = get_rag_store()
        chunks = rag.search(text, limit=10, source_type="disease")

        if not chunks:
            return [], 0.0

        # 検索結果から病害虫名を抽出
        estimated = []
        for c in chunks:
            chunk = c["chunk"]
            if chunk["source_type"] == "disease":
                name = chunk["source_id"]
                if name not in estimated:
                    estimated.append(name)

        confidence = min(0.8, chunks[0]["score"] * 0.9) if chunks else 0.0
        return estimated, confidence
    finally:
        conn.close()


def _disease_names_to_vector(disease_names: list[str]) -> list[int]:
    """
    病害虫名のリストを10次元0/1ベクトルに変換する。

    Args:
        disease_names: 病害虫名のリスト（例: ["炭疽病", "アブラムシ"]）

    Returns:
        10次元の0/1ベクトル
    """
    vector = [0] * VECTOR_DIM
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name FROM diseases"
        ).fetchall()
        name_to_idx = {row["name"]: row["id"] for row in rows}

        for name in disease_names:
            # 完全一致
            if name in name_to_idx:
                vector[name_to_idx[name]] = 1
                continue
            # 前方一致
            for disease_name, idx in name_to_idx.items():
                if disease_name.startswith(name) or name.startswith(disease_name):
                    vector[idx] = 1
                    break
    finally:
        conn.close()
    return vector


def estimate_and_prescribe(symptoms: str) -> str:
    """
    統合パイプライン: 症状記述 → RAG推定 → ベクトル変換 → RBP処方

    フロー:
      1. 自然言語症状 → RAG(症状辞典+Embedding+FAISS) で病害虫を推定
      2. 推定病害虫名 → 10次元0/1 EntryVector に変換
      3. EntryVector → RBPエンジンで最適な薬剤組合せを計算
      4. 結果を構造化JSONで返す

    Args:
        symptoms: 症状記述（自然言語、例: "葉っぱが黄色くなっていて、裏に小さい虫がついている"）

    Returns:
        JSON文字列（推定結果 + RBP処方結果）
    """
    conn = get_db()
    try:
        # Step 1: RAG推定
        estimated_diseases, confidence = _estimate_diseases_from_text(symptoms)

        if not estimated_diseases:
            return json.dumps({
                "status": "NO_ESTIMATION",
                "symptoms": symptoms,
                "message": "症状から病害虫を特定できませんでした。より具体的な症状を入力してください。",
            }, ensure_ascii=False, indent=2)

        # Step 2: ベクトル変換
        entry_vector = _disease_names_to_vector(estimated_diseases)

        # Step 3: RBPエンジン実行
        rbp_result, enriched_drugs = _run_rbp_and_enrich(conn, entry_vector)

        # Step 4: 結果を統合
        response = {
            "status": "OK",
            "symptoms": symptoms,
            "estimated_diseases": estimated_diseases,
            "confidence": round(confidence, 3),
            "entry_vector": entry_vector,
            "rbp_status": rbp_result.get("status", "UNKNOWN"),
            "best_match": rbp_result.get("best", {}),
            "alternatives_count": len(rbp_result.get("alternatives", [])),
            "pesticide_details": enriched_drugs,
        }

        return json.dumps(response, ensure_ascii=False, indent=2)
    finally:
        conn.close()
