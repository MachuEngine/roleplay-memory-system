"""L3 retrieval의 키 없는 로컬 기준선 평가.

합성 한국어 에피소드에 대해 표준 라이브러리만으로 BM25를 실행한다. 이 평가는
Gemini Embedding 2의 품질을 대신하지 않는다. 외부 embedding 후보가 같은 fixture에서
이 기준선을 넘는지 판단하기 위한 재현 가능한 baseline이다.

usage: .venv/bin/python scripts/evaluate_retrieval.py
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "cases" / "retrieval_eval.json"
OUT = ROOT / "tests" / "results" / "retrieval_eval.json"
TOP_K = 3


def tokenize(text: str) -> list[str]:
    """어절과 한글 2-gram을 함께 사용해 조사·활용에 덜 민감하게 만든다."""
    words = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    tokens = [f"w:{word}" for word in words]
    for word in words:
        if re.fullmatch(r"[가-힣]+", word):
            tokens.extend(f"b:{word[i:i + 2]}" for i in range(len(word) - 1))
    return tokens


def bm25_rank(documents: list[dict], query: str, k: int = TOP_K) -> list[tuple[str, float]]:
    corpus = [tokenize(row["text"]) for row in documents]
    q = set(tokenize(query))
    n = len(corpus)
    avgdl = sum(map(len, corpus)) / n
    df = Counter(token for doc in corpus for token in set(doc))
    scored: list[tuple[str, float]] = []
    k1, b = 1.5, 0.75
    for row, doc in zip(documents, corpus):
        tf = Counter(doc)
        score = 0.0
        for token in q:
            if not tf[token]:
                continue
            idf = math.log(1 + (n - df[token] + 0.5) / (df[token] + 0.5))
            denom = tf[token] + k1 * (1 - b + b * len(doc) / avgdl)
            score += idf * tf[token] * (k1 + 1) / denom
        if score > 0:
            scored.append((row["id"], score))
    return sorted(scored, key=lambda item: (-item[1], item[0]))[:k]


def main() -> None:
    suite = json.loads(CASES.read_text("utf-8"))
    docs, queries = suite["documents"], suite["queries"]
    rows = []
    recall_sum = precision_sum = reciprocal_sum = 0.0
    positive = 0
    empty_ok = empty_total = 0

    for query in queries:
        ranked = bm25_rank(docs, query["text"])
        got = [doc_id for doc_id, _ in ranked]
        relevant = set(query["relevant"])
        if relevant:
            positive += 1
            hits = len(relevant & set(got))
            recall_sum += hits / len(relevant)
            precision_sum += hits / TOP_K
            rank = next((i for i, doc_id in enumerate(got, 1) if doc_id in relevant), None)
            reciprocal_sum += 1 / rank if rank else 0.0
        else:
            empty_total += 1
            empty_ok += not got
        rows.append({
            "id": query["id"], "query": query["text"],
            "relevant": query["relevant"], "top_k": got,
            "pass": bool(relevant & set(got)) if relevant else not got,
        })

    metrics = {
        "positive_queries": positive,
        "recall_at_3": round(recall_sum / positive, 3),
        "precision_at_3": round(precision_sum / positive, 3),
        "mrr": round(reciprocal_sum / positive, 3),
        "no_result_accuracy": round(empty_ok / empty_total, 3) if empty_total else None,
    }
    result = {"method": "BM25(word + Hangul bigram)", "k": TOP_K,
              "dataset": "synthetic Korean roleplay episodes", "metrics": metrics,
              "queries": rows}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")

    print("## L3 retrieval 로컬 baseline\n")
    print("| 평가 | 결과 |")
    print("|---|---:|")
    print(f"| Recall@3 | {metrics['recall_at_3']:.3f} |")
    print(f"| Precision@3 | {metrics['precision_at_3']:.3f} |")
    print(f"| MRR | {metrics['mrr']:.3f} |")
    print(f"| 무관 질의 결과 없음 | {empty_ok}/{empty_total} |")
    failed = [row["id"] for row in rows if not row["pass"]]
    print(f"\n질의 {len(rows)}건, 실패 {len(failed)}건"
          + (f" ({', '.join(failed)})" if failed else ""))
    print(f"저장: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
