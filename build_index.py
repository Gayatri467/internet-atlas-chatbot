import json
import re
from pathlib import Path

import faiss
import numpy as np
import torch
from sentence_transformers import SentenceTransformer

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "documents"
INDEX_DIR = BASE_DIR / "index"

MODEL_PATH = (
    "/home/gayatri/docling-html-project/models/embeddings/"
    "models--sentence-transformers--all-MiniLM-L6-v2/"
    "snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
)

EXCLUDED_FILES = {
    "single-mrt-data-poc/graph/README.md"
}

MAX_CHARS = 2200


def get_title(content, file_path):
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return file_path.stem


def tokenize(text):
    return re.findall(r"[a-zA-Z0-9_./:-]+", text.lower())


def split_into_blocks(lines):
    blocks = []
    current = []
    in_code = False

    def save():
        nonlocal current
        text = "\n".join(current).strip()
        if text:
            blocks.append(text)
        current = []

    for line in lines:
        if line.strip().startswith("```"):
            current.append(line)
            in_code = not in_code
            if not in_code:
                save()
            continue

        if in_code:
            current.append(line)
            continue

        if not line.strip():
            save()
            continue

        is_table = line.strip().startswith("|")
        previous_is_table = (
            bool(current)
            and current[-1].strip().startswith("|")
        )

        if current and is_table != previous_is_table:
            save()

        current.append(line)

    save()
    return blocks


def split_into_chunks(content, document_title, relative_path):
    chunks = []
    heading_stack = []
    section_lines = []

    def save_section():
        if not section_lines:
            return

        section = " > ".join(heading_stack) or document_title
        blocks = split_into_blocks(section_lines)

        current_blocks = []
        current_length = 0

        def save_chunk():
            if not current_blocks:
                return

            body = "\n\n".join(current_blocks)

            embedding_text = (
                f"Document: {document_title}\n"
                f"Section: {section}\n\n"
                f"{body}"
            )

            chunks.append({
                "document": document_title,
                "source": relative_path,
                "section": section,
                "text": body,
                "embedding_text": embedding_text,
                "tokens": tokenize(embedding_text)
            })

        for block in blocks:
            block_length = len(block) + 2

            if (
                current_blocks
                and current_length + block_length > MAX_CHARS
            ):
                save_chunk()
                current_blocks = []
                current_length = 0

            current_blocks.append(block)
            current_length += block_length

        save_chunk()

    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)

        if match:
            save_section()
            section_lines.clear()

            level = len(match.group(1))
            heading = match.group(2).strip()

            heading_stack[:] = heading_stack[:level - 1]
            heading_stack.append(heading)

        section_lines.append(line)

    save_section()
    return chunks


def main():
    torch.set_num_threads(2)

    if not DOCS_DIR.exists():
        raise FileNotFoundError(
            f"Documents directory not found: {DOCS_DIR}"
        )

    if not Path(MODEL_PATH).exists():
        raise FileNotFoundError(
            f"Cached embedding model not found: {MODEL_PATH}"
        )

    markdown_files = sorted(DOCS_DIR.rglob("README.md"))

    all_chunks = []
    used_documents = []

    print("Reading copied README files...")

    for file_path in markdown_files:
        relative_path = file_path.relative_to(DOCS_DIR).as_posix()

        if relative_path in EXCLUDED_FILES:
            print(f"Skipping outdated document: {relative_path}")
            continue

        content = file_path.read_text(
            encoding="utf-8",
            errors="replace"
        ).strip()

        if not content:
            print(f"Skipping empty document: {relative_path}")
            continue

        title = get_title(content, file_path)

        document_chunks = split_into_chunks(
            content,
            title,
            relative_path
        )

        all_chunks.extend(document_chunks)
        used_documents.append(relative_path)

        print(
            f"Added: {relative_path} "
            f"({len(document_chunks)} chunks)"
        )

    if not all_chunks:
        raise RuntimeError("No document chunks were created.")

    print(f"\nDocuments included: {len(used_documents)}")
    print(f"Total chunks: {len(all_chunks)}")

    print("\nLoading cached embedding model on CPU...")

    model = SentenceTransformer(
        MODEL_PATH,
        device="cpu",
        local_files_only=True
    )

    print("Creating embeddings...")

    embedding_texts = [
        chunk["embedding_text"] for chunk in all_chunks
    ]

    embeddings = model.encode(
        embedding_texts,
        batch_size=8,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True
    )

    embeddings = np.asarray(embeddings, dtype="float32")

    print("Building FAISS index...")

    vector_index = faiss.IndexFlatIP(embeddings.shape[1])
    vector_index.add(embeddings)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    faiss.write_index(
        vector_index,
        str(INDEX_DIR / "documents.faiss")
    )

    with open(
        INDEX_DIR / "chunks.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            [
                {"id": i, **chunk}
                for i, chunk in enumerate(all_chunks)
            ],
            f,
            ensure_ascii=False,
            indent=2
        )

    with open(
        INDEX_DIR / "manifest.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            {
                "documents": used_documents,
                "document_count": len(used_documents),
                "chunk_count": len(all_chunks),
                "embedding_model": "all-MiniLM-L6-v2",
                "embedding_dimension": int(embeddings.shape[1]),
                "max_chunk_characters": MAX_CHARS
            },
            f,
            ensure_ascii=False,
            indent=2
        )

    print("\n=== INDEX BUILD COMPLETE ===")
    print(f"Documents: {len(used_documents)}")
    print(f"Chunks: {len(all_chunks)}")
    print(f"Embedding dimension: {embeddings.shape[1]}")
    print(f"FAISS index: {INDEX_DIR / 'documents.faiss'}")
    print(f"Chunk data: {INDEX_DIR / 'chunks.json'}")
    print(f"Manifest: {INDEX_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
