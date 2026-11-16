
import json
import re
from pathlib import Path
from collections import defaultdict

import faiss
import numpy as np
import torch
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer


INDEX_DIR = Path("index")

MODEL_PATH = (
    "/home/gayatri/docling-html-project/models/embeddings/"
    "models--sentence-transformers--all-MiniLM-L6-v2/"
    "snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
)

RRF_K = 60
RETRIEVAL_DEPTH = 15
SEED_COUNT = 6


def tokenize(text):
    return re.findall(r"[a-zA-Z0-9_./:-]+", text.lower())


def section_parts(section):
    if not section:
        return []
    return [
        part.strip()
        for part in section.split(" > ")
        if part.strip()
    ]


def is_heading_only(text):
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    substantive = [
        line for line in lines
        if not line.startswith("#")
        and not line.startswith("Document:")
        and not line.startswith("Source:")
        and not line.startswith("Section:")
        and not re.match(r"^[-*_]{3,}$", line)
    ]

    return (
        not substantive
        or len(" ".join(substantive)) < 40
    )


def shared_parent(section_a, section_b):
    parts_a = section_parts(section_a)
    parts_b = section_parts(section_b)

    if len(parts_a) < 2 or len(parts_b) < 2:
        return False

    # Match sections with the same immediate parent.
    return parts_a[:-1] == parts_b[:-1]


def retrieve_single_query(
    query,
    chunks,
    vector_index,
    bm25,
    model,
    file_to_indices
):
    query_embedding = model.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True
    ).astype("float32")

    _, vector_ids = vector_index.search(
        query_embedding,
        min(RETRIEVAL_DEPTH, len(chunks))
    )

    bm25_scores = bm25.get_scores(tokenize(query))

    bm25_ids = np.argsort(bm25_scores)[::-1][
        :RETRIEVAL_DEPTH
    ]

    # Reciprocal Rank Fusion: combine semantic and BM25 rankings.
    rrf = {}

    for rank, chunk_id in enumerate(vector_ids[0]):
        if chunk_id >= 0:
            chunk_id = int(chunk_id)
            rrf[chunk_id] = (
                rrf.get(chunk_id, 0.0)
                + 1.0 / (RRF_K + rank + 1)
            )

    for rank, chunk_id in enumerate(bm25_ids):
        if bm25_scores[chunk_id] > 0:
            chunk_id = int(chunk_id)
            rrf[chunk_id] = (
                rrf.get(chunk_id, 0.0)
                + 1.0 / (RRF_K + rank + 1)
            )

    seed_ids = sorted(
        rrf,
        key=rrf.get,
        reverse=True
    )[:SEED_COUNT]

    candidate_ids = set(seed_ids)

    for seed_id in seed_ids:
        seed = chunks[seed_id]
        source = seed.get("source", "")
        seed_section = seed.get("section", "")

        file_indices = file_to_indices.get(source, [])

        if seed_id not in file_indices:
            continue

        local_position = file_indices.index(seed_id)

        # Include nearby chunks in document order.
        for offset in (-2, -1, 1, 2):
            neighbor_position = local_position + offset

            if 0 <= neighbor_position < len(file_indices):
                candidate_ids.add(
                    file_indices[neighbor_position]
                )

        # Include chunks with the same immediate parent section.
        for candidate_id in file_indices:
            candidate_section = chunks[candidate_id].get(
                "section", ""
            )

            if shared_parent(
                seed_section,
                candidate_section
            ):
                candidate_ids.add(candidate_id)

    candidate_ids = sorted(candidate_ids)

    candidate_texts = [
        chunks[cid]["text"]
        for cid in candidate_ids
    ]

    candidate_embeddings = model.encode(
        candidate_texts,
        convert_to_numpy=True,
        normalize_embeddings=True
    )

    semantic_scores = np.dot(
        candidate_embeddings,
        query_embedding[0]
    )

    scored = {}

    for chunk_id, semantic_score in zip(
        candidate_ids,
        semantic_scores
    ):
        chunk = chunks[chunk_id]

        fusion_score = rrf.get(chunk_id, 0.0)
        semantic_component = max(
            0.0,
            float(semantic_score)
        )

        final_score = (
            0.65 * semantic_component
            + 20.0 * fusion_score
        )

        if is_heading_only(chunk["text"]):
            final_score *= 0.35

        scored[chunk_id] = (
            final_score,
            semantic_component,
            fusion_score
        )

    return scored


def retrieve(
    query,
    chunks,
    vector_index,
    bm25,
    model,
    file_to_indices,
    top_k=5
):
    queries = [query]
    lower_query = query.lower()

    # Multi-query expansion for FRR / routing questions.
    if "frr" in lower_query:
        queries.extend([
            "Free Range Routing bgpd mrt-tx dump bgp updates",
            "frr-local:mrt-tx operator routers patch diff"
        ])

    # Multi-query expansion for MRT-to-Parquet questions.
    elif "mrt" in lower_query and "parquet" in lower_query:
        queries.extend([
            "mrt-reader decode MRT updates.gz docker compose run",
            "Steps to build and run mrt-decode download MRT dump",
            "Get the input from mrt-reader parquet-converter parsed_mrt.txt",
            "Convert decoded MRT updates into a Parquet file"
        ])

    aggregated = defaultdict(
        lambda: [0.0, 0.0, 0.0]
    )

    for q in queries:
        scores = retrieve_single_query(
            q,
            chunks,
            vector_index,
            bm25,
            model,
            file_to_indices
        )

        for cid, (
            final_score,
            semantic_score,
            fusion_score
        ) in scores.items():

            aggregated[cid][0] += final_score

            aggregated[cid][1] = max(
                aggregated[cid][1],
                semantic_score
            )

            aggregated[cid][2] = max(
                aggregated[cid][2],
                fusion_score
            )

    results = [
        (
            cid,
            values[0],
            values[1],
            values[2]
        )
        for cid, values in aggregated.items()
    ]

    results.sort(
        key=lambda item: item[1],
        reverse=True
    )

    return results[:top_k]


def main():
    torch.set_num_threads(2)

    chunks_path = INDEX_DIR / "chunks.json"
    index_path = INDEX_DIR / "documents.faiss"

    if not chunks_path.exists() or not index_path.exists():
        raise FileNotFoundError(
            "Index files are missing. Check the index directory."
        )

    with open(
        chunks_path,
        encoding="utf-8"
    ) as file:
        chunks = json.load(file)

    vector_index = faiss.read_index(
        str(index_path)
    )

    if vector_index.ntotal != len(chunks):
        raise ValueError(
            "FAISS vector count does not match "
            "the number of chunks."
        )

    bm25 = BM25Okapi([
        chunk.get("tokens", [])
        for chunk in chunks
    ])

    model = SentenceTransformer(
        MODEL_PATH,
        device="cpu",
        local_files_only=True
    )

    file_to_indices = defaultdict(list)

    for index, chunk in enumerate(chunks):
        file_to_indices[
            chunk.get("source", "")
        ].append(index)

    queries = [
        "How is MRT data converted into Parquet?",
        "What is FRR and how is it used?",
        "How does the Internet Mapper API work?"
    ]

    for query in queries:
        print("\n" + "=" * 85)
        print("QUESTION:", query)
        print("=" * 85)

        results = retrieve(
            query,
            chunks,
            vector_index,
            bm25,
            model,
            file_to_indices,
            top_k=5
        )

        for rank, (
            chunk_id,
            final_score,
            semantic_score,
            rrf_score
        ) in enumerate(results, 1):

            chunk = chunks[chunk_id]

            print(
                f"\n--- Result {rank} "
                f"(Chunk {chunk_id} | "
                f"Final: {final_score:.4f} | "
                f"Semantic: {semantic_score:.4f} | "
                f"RRF: {rrf_score:.4f}) ---"
            )

            print(
                f"Source: {chunk.get('source', '')} | "
                f"Section: {chunk.get('section', '')}"
            )

            print(
                chunk.get("text", "")[:600]
            )


if __name__ == "__main__":
    main()
