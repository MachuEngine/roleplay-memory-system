"""Gemini Embedding 2의 한국어 L3 retrieval 소규모 평가.

BM25와 같은 합성 fixture에서 cosine ranking을 측정한다. similarity threshold는
이 작은 검증셋에서 양성 relevant score와 무관 질의 top-1 score가 분리될 때만
참고값으로 계산한다. 운영 threshold로 확정하지 않는다.

usage: .venv/bin/python scripts/evaluate_embedding_retrieval.py
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "cases" / "retrieval_eval.json"
RESULTS = ROOT / "tests" / "results"
TOP_K = 3


def embed(client: genai.Client, model: str, texts: list[str], task_type: str) -> list[list[float]]:
    contents = [types.Content(parts=[types.Part(text=text)]) for text in texts]
    response = client.models.embed_content(
        model=model,
        contents=contents,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=768,
        ),
    )
    return [embedding.values for embedding in response.embeddings]


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return dot / norm if norm else 0.0


def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY가 없습니다.")
    model = "gemini-embedding-2"
    suite = json.loads(CASES.read_text("utf-8"))
    documents, queries = suite["documents"], suite["queries"]
    client = genai.Client(api_key=api_key)
    document_vectors = embed(
        client, model, [row["text"] for row in documents], "RETRIEVAL_DOCUMENT"
    )
    query_vectors = embed(
        client, model, [row["text"] for row in queries], "RETRIEVAL_QUERY"
    )

    rankings: list[list[tuple[str, float]]] = []
    positive_relevant_scores: list[float] = []
    negative_top_scores: list[float] = []
    for query, vector in zip(queries, query_vectors):
        ranked = sorted(
            ((doc["id"], cosine(vector, doc_vector))
             for doc, doc_vector in zip(documents, document_vectors)),
            key=lambda item: (-item[1], item[0]),
        )
        rankings.append(ranked)
        if query["relevant"]:
            scores = dict(ranked)
            positive_relevant_scores.extend(scores[doc_id] for doc_id in query["relevant"])
        else:
            negative_top_scores.append(ranked[0][1])

    min_positive = min(positive_relevant_scores)
    max_negative = max(negative_top_scores)
    threshold = (min_positive + max_negative) / 2 if max_negative < min_positive else None
    rows = []
    recall_sum = precision_sum = reciprocal_sum = 0.0
    positive = empty_total = empty_ok = 0
    for query, ranked in zip(queries, rankings):
        filtered = [(doc_id, score) for doc_id, score in ranked
                    if threshold is None or score >= threshold][:TOP_K]
        got = [doc_id for doc_id, _ in filtered]
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
            "id": query["id"], "query": query["text"], "relevant": query["relevant"],
            "top_k": [{"id": doc_id, "score": round(score, 4)}
                      for doc_id, score in filtered],
            "raw_top_1": {"id": ranked[0][0], "score": round(ranked[0][1], 4)},
            "pass": bool(relevant & set(got)) if relevant else not got,
        })

    metrics = {
        "positive_queries": positive,
        "recall_at_3": round(recall_sum / positive, 3),
        "precision_at_3": round(precision_sum / positive, 3),
        "mrr": round(reciprocal_sum / positive, 3),
        "no_result_accuracy": round(empty_ok / empty_total, 3),
        "synthetic_threshold": round(threshold, 4) if threshold is not None else None,
        "min_positive_relevant_score": round(min_positive, 4),
        "max_negative_top_score": round(max_negative, 4),
    }
    result = {
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "method": model, "dimensions": 768, "k": TOP_K,
        "dataset": "synthetic Korean roleplay episodes", "metrics": metrics,
        "threshold_note": "검증셋 내부 참고값이며 운영 threshold가 아님",
        "queries": rows,
    }
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    out = RESULTS / f"embedding_retrieval_{stamp}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")

    print("## Gemini Embedding 2 retrieval 평가\n")
    print("| 평가 | 결과 |")
    print("|---|---:|")
    print(f"| Recall@3 | {metrics['recall_at_3']:.3f} |")
    print(f"| Precision@3 | {metrics['precision_at_3']:.3f} |")
    print(f"| MRR | {metrics['mrr']:.3f} |")
    print(f"| 무관 질의 결과 없음 | {empty_ok}/{empty_total} |")
    print(f"| 합성셋 참고 threshold | {metrics['synthetic_threshold']} |")
    failed = [row["id"] for row in rows if not row["pass"]]
    print(f"\n질의 {len(rows)}건, 실패 {len(failed)}건"
          + (f" ({', '.join(failed)})" if failed else ""))
    print(f"저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
