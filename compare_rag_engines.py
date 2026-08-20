#!/usr/bin/env python3
"""
compare_rag_engines.py — TF-IDF vs Embedding+FAISS の比較テスト

設計書 RAG_Design.md Phase 1 (TF-IDF PoC) と Phase 3 (Embedding) の
検索精度・パフォーマンスを比較する。

使い方:
  python compare_rag_engines.py
"""

import json
import os
import sys
import time
from pathlib import Path

# Project root
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)

import numpy as np
import sqlite3
from sklearn.feature_extraction.text import TfidfVectorizer

# Import shared chunk extraction from mcp_tools
from mcp_tools import (
    _build_pesticide_chunks,
    _build_disease_chunks,
    _build_record_chunks,
    _extract_chunks,
    get_db,
)

# ============================================================================
# Test queries — 設計書 RAG_Design.md §4 テストケース + α
# ============================================================================

TEST_QUERIES = [
    # (クエリ, 期待される結果の説明, 期待されるsource_type)
    ("葉っぱが黄色くなってる", "アブラムシ関連", "disease"),
    ("葉に白い粉っぽいもの", "うどんこ病", "disease"),
    ("葉の裏に小さい虫", "アブラムシ/コナジラミ", "disease"),
    ("有機で使える殺菌剤", "有機JAS対応農薬", "pesticide"),
    ("葉が丸まってる", "アブラムシ/モザイク病", "disease"),
    ("実が腐る", "炭疽病/灰色かび病", "disease"),
    ("ストロビーと混ぜられる", "混用可能農薬", "pesticide"),
    ("カビに効く薬", "殺菌剤", "pesticide"),
    ("葉に穴が開く", "ヨトウムシ/アオムシ", "disease"),
    ("蜘蛛の巣みたいな糸", "ハダニ", "disease"),
    ("吸収してるって言われてる", "吸汁害虫", "disease"),
    ("葉が縮れてる", "モザイク病/アブラムシ", "disease"),
    ("去年の夏みたいにしたい", "過去の記録", "record"),
    ("PHIが短い薬剤", "PHIが短い農薬", "pesticide"),
    ("抵抗性管理が大事", "FRACグループ回転", "pesticide"),
]


# ============================================================================
# TF-IDF Engine (legacy)
# ============================================================================

class TfidfEngine:
    """TF-IDF ベースの検索エンジン（既存実装）"""

    def __init__(self):
        self.vectorizer = None
        self.tfidf_matrix = None
        self.chunks = []

    def build(self, chunks):
        texts = [c["text"] for c in chunks]
        self.chunks = chunks
        self.vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(2, 4),
            sublinear_tf=True,
        )
        self.tfidf_matrix = self.vectorizer.fit_transform(texts)

    def search(self, query, limit=5, source_type=None):
        if self.vectorizer is None:
            return []
        query_vec = self.vectorizer.transform([query])
        scores = (self.tfidf_matrix @ query_vec.T).toarray().flatten()

        if source_type and source_type != "all":
            filtered_scores = np.full_like(scores, -np.inf)
            for i, c in enumerate(self.chunks):
                if c["source_type"] == source_type:
                    filtered_scores[i] = scores[i]
            scores = filtered_scores

        top_idx = np.argsort(-scores)[:limit]
        results = []
        for idx in top_idx:
            if scores[idx] <= 0:
                break
            results.append({
                "chunk": self.chunks[int(idx)],
                "score": float(scores[idx]),
            })
        return results


# ============================================================================
# Embedding + FAISS Engine (new)
# ============================================================================

class EmbeddingEngine:
    """埋め込みモデル + FAISS ベースの検索エンジン（新実装）"""

    MODEL_ID = "sentence-transformers/LaBSE"
    EMBEDDING_DIM = 768

    def __init__(self):
        self.embedder = None
        self.embedding_cache = None
        self.index = None
        self.chunks = []

    def _ensure_embedder(self):
        if self.embedder is None:
            from sentence_transformers import SentenceTransformer
            self.embedder = SentenceTransformer(
                self.MODEL_ID,
                trust_remote_code=True,
            )
        return self.embedder

    def build(self, chunks):
        texts = [c["text"] for c in chunks]
        self.chunks = chunks

        embedder = self._ensure_embedder()
        print(f"  埋め込み計算中: {len(texts)}チャンク...")
        self.embedding_cache = embedder.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        dim = self.embedding_cache.shape[1]
        import faiss
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(self.embedding_cache)
        print(f"  FAISSインデックス構築完了: {self.index.ntotal}件")

    def search(self, query, limit=5, source_type=None):
        if self.index is None:
            return []

        embedder = self._ensure_embedder()
        query_emb = embedder.encode([query], normalize_embeddings=True)
        scores, indices = self.index.search(query_emb, k=len(self.chunks))

        if source_type and source_type != "all":
            filtered_scores = []
            filtered_indices = []
            for s, idx in zip(scores[0], indices[0]):
                if self.chunks[idx]["source_type"] == source_type:
                    filtered_scores.append(s)
                    filtered_indices.append(idx)
            scores = np.array(filtered_scores).reshape(1, -1)
            indices = np.array(filtered_indices).reshape(1, -1)

        top_idx = indices[0][:limit]
        top_scores = scores[0][:limit]

        results = []
        for idx, score in zip(top_idx, top_scores):
            if score <= 0:
                break
            results.append({
                "chunk": self.chunks[int(idx)],
                "score": float(score),
            })
        return results


# ============================================================================
# Comparison logic
# ============================================================================

def evaluate_query(tfidf_results, emb_results, expected_source):
    """
    1クエリあたりの結果を比較評価する。

    Returns:
        dict: 各種メトリクス
    """
    tfidf_sources = {r["chunk"]["source_type"] for r in tfidf_results}
    emb_sources = {r["chunk"]["source_type"] for r in emb_results}

    # 期待source_typeがトップKに含まれるか
    tfidf_hit = expected_source in tfidf_sources
    emb_hit = expected_source in emb_sources

    # トップ1の結果
    tfidf_top1 = tfidf_results[0]["chunk"]["source_type"] if tfidf_results else None
    emb_top1 = emb_results[0]["chunk"]["source_type"] if emb_results else None

    # スコア分布
    tfidf_avg_score = np.mean([r["score"] for r in tfidf_results]) if tfidf_results else 0
    emb_avg_score = np.mean([r["score"] for r in emb_results]) if emb_results else 0

    return {
        "tfidf_hit": tfidf_hit,
        "emb_hit": emb_hit,
        "tfidf_top1": tfidf_top1,
        "emb_top1": emb_top1,
        "tfidf_avg_score": tfidf_avg_score,
        "emb_avg_score": emb_avg_score,
    }


def run_comparison(chunks):
    """TF-IDF vs Embedding の本比較を実行"""

    print("\n" + "=" * 70)
    print("TF-IDF vs Embedding+FAISS 比較テスト")
    print("=" * 70)
    print(f"\nチャンク数: {len(chunks)}")
    print(f"  農薬: {sum(1 for c in chunks if c['source_type'] == 'pesticide')}")
    print(f"  病害虫: {sum(1 for c in chunks if c['source_type'] == 'disease')}")
    print(f"  記録: {sum(1 for c in chunks if c['source_type'] == 'record')}")

    # --- Build engines ---
    print("\n--- インデックス構築 ---")

    print("\n[TF-IDF] 構築中...")
    t0 = time.perf_counter()
    tfidf = TfidfEngine()
    tfidf.build(chunks)
    tfidf_build_time = time.perf_counter() - t0
    print(f"  構築時間: {tfidf_build_time*1000:.1f}ms")

    print("\n[Embedding+FAISS] 構築中...")
    t0 = time.perf_counter()
    emb = EmbeddingEngine()
    emb.build(chunks)
    emb_build_time = time.perf_counter() - t0
    print(f"  構築時間: {emb_build_time*1000:.1f}ms")

    # --- Search benchmark ---
    print("\n--- 検索パフォーマンス ---")

    tfidf_search_times = []
    emb_search_times = []

    for query, _, _ in TEST_QUERIES:
        t0 = time.perf_counter()
        tfidf.search(query)
        tfidf_search_times.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        emb.search(query)
        emb_search_times.append(time.perf_counter() - t0)

    tfidf_avg_speed = np.mean(tfidf_search_times) * 1000
    emb_avg_speed = np.mean(emb_search_times) * 1000
    print(f"\n  TF-IDF 平均: {tfidf_avg_speed:.2f}ms/query")
    print(f"  Embedding 平均: {emb_avg_speed:.2f}ms/query")
    print(f"  比 (Emb/TF-IDF): {emb_avg_speed / tfidf_avg_speed:.1f}x")

    # --- Accuracy evaluation ---
    print("\n--- 検索精度評価 ---")

    evaluations = []
    for query, desc, expected_source in TEST_QUERIES:
        tfidf_res = tfidf.search(query, limit=5, source_type=expected_source)
        emb_res = emb.search(query, limit=5, source_type=expected_source)

        # source_type制約なしでも見る
        tfidf_res_all = tfidf.search(query, limit=5)
        emb_res_all = emb.search(query, limit=5)

        ev = evaluate_query(tfidf_res_all, emb_res_all, expected_source)
        ev["query"] = query
        ev["desc"] = desc
        ev["tfidf_count"] = len(tfidf_res_all)
        ev["emb_count"] = len(emb_res_all)
        evaluations.append(ev)

    # サマリー
    tfidf_hits = sum(1 for e in evaluations if e["tfidf_hit"])
    emb_hits = sum(1 for e in evaluations if e["emb_hit"])

    # Top1一致カウント
    tfidf_correct_top1 = 0
    emb_correct_top1 = 0
    for ev in evaluations:
        expected = next((s for q, _, s in TEST_QUERIES if q == ev["query"]), None)
        if expected and ev["tfidf_top1"] == expected:
            tfidf_correct_top1 += 1
        if expected and ev["emb_top1"] == expected:
            emb_correct_top1 += 1

    # 正しいTop1カウント（手動で確認しやすいよう個別に表示）
    print(f"\n{'クエリ':<30} {'TF-IDF Top1':<15} {'Emb Top1':<15} {'TF-IDF Hit':<10} {'Emb Hit':<10}")
    print("-" * 80)

    for ev in evaluations:
        tfidf_tag = "✅" if ev["tfidf_hit"] else "❌"
        emb_tag = "✅" if ev["emb_hit"] else "❌"
        print(
            f"{ev['query']:<30} "
            f"{ev['tfidf_top1'] or '-':<15} "
            f"{ev['emb_top1'] or '-':<15} "
            f"{tfidf_tag:<10} "
            f"{emb_tag:<10}"
        )

    print(f"\n=== サマリー ===")
    print(f"  TF-IDF ヒット率: {tfidf_hits}/{len(TEST_QUERIES)} ({tfidf_hits/len(TEST_QUERIES)*100:.0f}%)")
    print(f"  Embedding ヒット率: {emb_hits}/{len(TEST_QUERIES)} ({emb_hits/len(TEST_QUERIES)*100:.0f}%)")

    # Embが勝ったケース
    emb_only_wins = [e for e in evaluations if not e["tfidf_hit"] and e["emb_hit"]]
    if emb_only_wins:
        print(f"\n  【Embeddingのみがヒット】 ({len(emb_only_wins)}件)")
        for e in emb_only_wins:
            print(f"    \"{e['query']}\" → {e['emb_top1']}")

    # TF-IDFが勝ったケース
    tfidf_only_wins = [e for e in evaluations if e["tfidf_hit"] and not e["emb_hit"]]
    if tfidf_only_wins:
        print(f"\n  【TF-IDFのみがヒット】 ({len(tfidf_only_wins)}件)")
        for e in tfidf_only_wins:
            print(f"    \"{e['query']}\" → {e['tfidf_top1']}")

    # スコア分布比較
    tfidf_scores = [e["tfidf_avg_score"] for e in evaluations if e["tfidf_count"] > 0]
    emb_scores = [e["emb_avg_score"] for e in evaluations if e["emb_count"] > 0]

    print(f"\n  スコア分布:")
    print(f"    TF-IDF:  avg={np.mean(tfidf_scores):.4f}, "
          f"std={np.std(tfidf_scores):.4f}, "
          f"range=[{min(tfidf_scores):.4f}, {max(tfidf_scores):.4f}]")
    print(f"    Embedding: avg={np.mean(emb_scores):.4f}, "
          f"std={np.std(emb_scores):.4f}, "
          f"range=[{min(emb_scores):.4f}, {max(emb_scores):.4f}]")

    # 結果をJSONに保存
    output = {
        "build_times": {
            "tfidf_ms": round(tfidf_build_time * 1000, 1),
            "embedding_ms": round(emb_build_time * 1000, 1),
        },
        "search_speed": {
            "tfidf_avg_ms": round(tfidf_avg_speed, 3),
            "embedding_avg_ms": round(emb_avg_speed, 3),
            "ratio": round(emb_avg_speed / tfidf_avg_speed, 2),
        },
        "accuracy": {
            "tfidf_hits": tfidf_hits,
            "embedding_hits": emb_hits,
            "total_queries": len(TEST_QUERIES),
        },
        "per_query": [
            {
                "query": e["query"],
                "expected_source": next((s for q, _, s in TEST_QUERIES if q == e["query"]), "?"),
                "tfidf_hit": e["tfidf_hit"],
                "emb_hit": e["emb_hit"],
                "tfidf_top1": e["tfidf_top1"],
                "emb_top1": e["emb_top1"],
                "tfidf_score": round(e["tfidf_avg_score"], 4),
                "emb_score": round(e["emb_avg_score"], 4),
            }
            for e in evaluations
        ],
    }

    output_path = os.path.join(APP_ROOT, "data", "rag_comparison.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  詳細結果: {output_path}")

    return output


# ============================================================================
# Main
# ============================================================================

def main():
    print("STB RAG Engine Comparison")
    print("=" * 70)

    # Get DB connection
    conn = get_db()

    # Extract chunks
    print("\nチャンク抽出中...")
    chunks = _extract_chunks(conn)
    print(f"  抽出完了: {len(chunks)}チャンク")

    # Run comparison
    result = run_comparison(chunks)

    # Summary verdict
    emb_wins = result["accuracy"]["embedding_hits"]
    tfidf_wins = result["accuracy"]["tfidf_hits"]

    print("\n" + "=" * 70)
    if emb_wins > tfidf_wins:
        print(f"🏆 総合勝利: Embedding+FAISS ({emb_wins} vs {tfidf_wins})")
    elif tfidf_wins > emb_wins:
        print(f"🏆 総合勝利: TF-IDF ({tfidf_wins} vs {emb_wins})")
    else:
        print(f"🤝 引き分け ({emb_wins} vs {emb_wins})")
    print("=" * 70)


if __name__ == "__main__":
    main()
