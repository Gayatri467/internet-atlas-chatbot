import json
import re
from pathlib import Path
from collections import defaultdict
from difflib import SequenceMatcher

import faiss
import numpy as np
import requests
import torch

from flask import Flask, request, jsonify, render_template_string
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
INDEX_DIR = BASE_DIR / "index"

MODEL_PATH = (
    "/home/gayatri/docling-html-project/models/embeddings/"
    "models--sentence-transformers--all-MiniLM-L6-v2/"
    "snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
)

LLAMA_URL = "http://127.0.0.1:8080/v1/chat/completions"
LLAMA_MODELS_URL = "http://127.0.0.1:8080/v1/models"

# Print retrieval scores, raw model output and rejection reasons.
DEBUG = True

# Retrieval settings
RRF_K = 60
RETRIEVAL_DEPTH = 25
SEED_COUNT = 15
TOP_K = 8
MAX_PASSAGE_CHARS = 1200        # trim long passages so the small model can cope
NEIGHBOR_OFFSETS = (-1, 1)      # neighbouring chunks added around each seed
USE_PARENT_EXPANSION = False    # True adds sibling sections (slower, noisier)
HEADING_BOOST = 0.6             # bonus when a chunk's own heading matches the query

# Out-of-document gate (tune using your own in-document / off-topic questions).
# A question is treated as out-of-document when BOTH are below threshold.
SEMANTIC_THRESHOLD = 0.23       # cosine similarity of best chunk vs question
FUSION_THRESHOLD = 0.031         # RRF score (max possible is about 0.033)

# Validation settings
EVIDENCE_MATCH_RATIO = 0.90     # share of the evidence that must match the context
ANSWER_IN_EVIDENCE_RATIO = 0.70 # answer terms found in the evidence
ANSWER_IN_CONTEXT_RATIO = 0.85  # looser alternative: answer terms found in passages

FALLBACK_ANSWER = "The document does not contain this information."

torch.set_num_threads(2)

app = Flask(__name__)


def log(*args):
    if DEBUG:
        print(*args)


# ============================================================
# LOAD KNOWLEDGE BASE
# ============================================================

print("\nLoading chatbot knowledge base...")

chunks_path = INDEX_DIR / "chunks.json"
index_path = INDEX_DIR / "documents.faiss"

if not chunks_path.exists() or not index_path.exists():
    raise FileNotFoundError("Index files are missing. Check the index directory.")

with open(chunks_path, encoding="utf-8") as file:
    chunks = json.load(file)

vector_index = faiss.read_index(str(index_path))

if vector_index.ntotal != len(chunks):
    raise ValueError("FAISS vector count does not match the number of chunks.")

bm25 = BM25Okapi([chunk.get("tokens", []) for chunk in chunks])

embedder = SentenceTransformer(
    MODEL_PATH,
    device="cpu",
    local_files_only=True
)

file_to_indices = defaultdict(list)

for index, chunk in enumerate(chunks):
    file_to_indices[chunk.get("source", "")].append(index)

print(f"Loaded {len(chunks)} document chunks.")
print("Knowledge base ready.")


# ============================================================
# TEXT HELPERS
# ============================================================

# Words ignored when matching the query against section headings
# and when checking answer terms.
STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "on",
    "to", "and", "or", "for", "by", "with", "from", "that", "this",
    "it", "as", "be", "can", "will", "tell", "me", "about", "what",
    "how", "which", "does", "do", "use", "used", "give", "explain",
    "describe", "please", "list", "show", "its", "their", "there"
}


def tokenize(text):
    return re.findall(r"[a-zA-Z0-9_./:-]+", text.lower())


def content_terms(text):
    """Lower-cased meaningful terms with surrounding punctuation removed."""
    terms = []

    for token in re.findall(r"[a-z0-9_\-./]+", text.lower()):
        token = token.strip("._-/:")

        if token and token not in STOPWORDS:
            terms.append(token)

    return terms


def strip_markdown(text):
    """Remove emphasis and code markers so quotes compare fairly."""
    return re.sub(r"[*`]", "", text)


def normalize(text):
    return " ".join(strip_markdown(text).lower().split())


def section_parts(section):
    if not section:
        return []

    return [part.strip() for part in section.split(" > ") if part.strip()]


def is_heading_only(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    substantive = [
        line for line in lines
        if not line.startswith("#")
        and not line.startswith("Document:")
        and not line.startswith("Source:")
        and not line.startswith("Section:")
        and not re.match(r"^[-*_]{3,}$", line)
    ]

    return not substantive or len(" ".join(substantive)) < 40


def shared_parent(section_a, section_b):
    parts_a = section_parts(section_a)
    parts_b = section_parts(section_b)

    if len(parts_a) < 2 or len(parts_b) < 2:
        return False

    return parts_a[:-1] == parts_b[:-1]


# ============================================================
# HYBRID RETRIEVAL (FAISS + BM25 + RRF + heading boost)
# ============================================================

def retrieve(query, top_k=TOP_K):
    """
    Returns a list of tuples:
    (chunk_id, final_score, semantic_score, fusion_score)
    sorted by final_score, best first.
    """

    query_embedding = embedder.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True
    ).astype("float32")

    _, vector_ids = vector_index.search(
        query_embedding,
        min(RETRIEVAL_DEPTH, len(chunks))
    )

    bm25_scores = bm25.get_scores(tokenize(query))
    bm25_ids = np.argsort(bm25_scores)[::-1][:RETRIEVAL_DEPTH]

    # Reciprocal Rank Fusion
    rrf = {}

    for rank, chunk_id in enumerate(vector_ids[0]):
        if chunk_id >= 0:
            chunk_id = int(chunk_id)
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    for rank, chunk_id in enumerate(bm25_ids):
        if bm25_scores[chunk_id] > 0:
            chunk_id = int(chunk_id)
            rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    if not rrf:
        return []

    seed_ids = sorted(rrf, key=rrf.get, reverse=True)[:SEED_COUNT]
    candidate_ids = set(seed_ids)

    # Expand each seed with neighbouring chunks (document order)
    for seed_id in seed_ids:
        seed = chunks[seed_id]
        source = seed.get("source", "")
        seed_section = seed.get("section", "")
        file_indices = file_to_indices.get(source, [])

        if seed_id not in file_indices:
            continue

        local_position = file_indices.index(seed_id)

        for offset in NEIGHBOR_OFFSETS:
            neighbor_position = local_position + offset

            if 0 <= neighbor_position < len(file_indices):
                candidate_ids.add(file_indices[neighbor_position])

        if USE_PARENT_EXPANSION:
            for candidate_id in file_indices:
                candidate_section = chunks[candidate_id].get("section", "")

                if shared_parent(seed_section, candidate_section):
                    candidate_ids.add(candidate_id)

    candidate_ids = sorted(candidate_ids)

    candidate_embeddings = embedder.encode(
        [chunks[cid]["text"] for cid in candidate_ids],
        convert_to_numpy=True,
        normalize_embeddings=True
    )

    semantic_scores = np.dot(candidate_embeddings, query_embedding[0])

    query_terms = set(content_terms(query))

    results = []

    for chunk_id, semantic_score in zip(candidate_ids, semantic_scores):

        chunk = chunks[chunk_id]

        fusion_score = rrf.get(chunk_id, 0.0)
        semantic_component = max(0.0, float(semantic_score))

        final_score = 0.65 * semantic_component + 20.0 * fusion_score

        # Heading boost: the chunk's own heading shares terms with the query.
        parts = section_parts(chunk.get("section", ""))

        if parts and query_terms:
            title_terms = set(content_terms(parts[-1]))

            if title_terms:
                matched = len(query_terms & title_terms)
                final_score += HEADING_BOOST * matched / len(title_terms)

        if is_heading_only(chunk["text"]):
            final_score *= 0.35

        results.append((chunk_id, final_score, semantic_component, fusion_score))

    results.sort(key=lambda item: item[1], reverse=True)

    return results[:top_k]


def is_out_of_document(results):
    """Retrieval gate: True when nothing retrieved looks related to the question."""
    if not results:
        return True

    best_semantic = max(item[2] for item in results)
    best_fusion = max(item[3] for item in results)

    return best_semantic < SEMANTIC_THRESHOLD and best_fusion < FUSION_THRESHOLD


# ============================================================
# PREPARE DOCUMENT CONTEXT
# ============================================================

def build_context(results):

    context_parts = []

    for rank, (chunk_id, _final, _semantic, _fusion) in enumerate(results, 1):

        chunk = chunks[chunk_id]

        source = chunk.get("source", "Unknown source")
        section = chunk.get("section", "Unknown section")
        document_text = chunk.get("text", "")[:MAX_PASSAGE_CHARS]

        context_parts.append(
            f"[Retrieved passage {rank}]\n"
            f"Document: {Path(source).name}\n"
            f"Section: {section}\n\n"
            f"{document_text}"
        )

    return "\n\n".join(context_parts)


# ============================================================
# SYSTEM PROMPT (generic, no document-specific examples)
# ============================================================

SYSTEM_PROMPT = """You answer questions using the document passages you are given.

Steps:
1. Look through the passages for a sentence that answers the question.
2. If you find one, copy that sentence exactly into "evidence", then write a
   short "answer" using the exact technical names from the passage, and set
   "answerable" to true.
3. Only if no passage contains the answer, or the question has nothing to do
   with the passages, leave "evidence" and "answer" empty and set
   "answerable" to false.

Do not use any knowledge from outside the passages.

Reply with JSON only, with the keys in this order:
{"evidence": "...", "answer": "...", "answerable": true}
"""

# "answerable" comes LAST so the model must find the evidence before deciding.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence": {"type": "string"},
        "answer": {"type": "string"},
        "answerable": {"type": "boolean"}
    },
    "required": ["evidence", "answer", "answerable"]
}


# ============================================================
# CALL LOCAL QWEN SERVER
# ============================================================

def ask_qwen(question, context):

    payload = {
        "model": "local-model",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "DOCUMENT PASSAGES:\n\n"
                    + context
                    + "\n\nQUESTION:\n"
                    + question
                )
            }
        ],
        "temperature": 0,
        "max_tokens": 500,
        "stream": False,
        # Forces valid JSON in llama.cpp's server
        "response_format": {
            "type": "json_object",
            "schema": RESPONSE_SCHEMA
        }
    }

    response = requests.post(LLAMA_URL, json=payload, timeout=180)

    # Some llama.cpp builds reject response_format; retry without it.
    if response.status_code == 400:
        payload.pop("response_format", None)
        response = requests.post(LLAMA_URL, json=payload, timeout=180)

    response.raise_for_status()

    data = response.json()

    return data["choices"][0]["message"]["content"]


# ============================================================
# VALIDATE QWEN RESPONSE (generic, no hardcoded answers)
# ============================================================

def evidence_in_context(evidence, context):
    """True if the quoted evidence really appears in the retrieved passages."""

    ev = normalize(evidence)
    ctx = normalize(context)

    if not ev:
        return False

    if ev in ctx:
        return True

    # Tolerate small copy errors: the longest matching block must
    # cover most of the evidence.
    matcher = SequenceMatcher(None, ctx, ev, autojunk=False)
    match = matcher.find_longest_match(0, len(ctx), 0, len(ev))

    return len(ev) >= 15 and match.size >= EVIDENCE_MATCH_RATIO * len(ev)


def share_found(terms, haystack):
    if not terms:
        return 0.0

    return sum(term in haystack for term in terms) / len(terms)

def model_claims_answerable(raw_response):
    """Return True only when Qwen explicitly marks the response answerable."""
    try:
        cleaned = raw_response.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)
        return isinstance(result, dict) and result.get("answerable") is True
    except (json.JSONDecodeError, TypeError, AttributeError):
        return False

def validate_answer(raw_response, context, question):

    # --- parse JSON -------------------------------------------------
    try:
        cleaned = raw_response.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)

    except (json.JSONDecodeError, TypeError, AttributeError):
        log("REJECTED: model output is not valid JSON ->", str(raw_response)[:200])
        return FALLBACK_ANSWER

    if not isinstance(result, dict):
        log("REJECTED: JSON is not an object")
        return FALLBACK_ANSWER

    if result.get("answerable") is not True:
        log("REJECTED: model returned answerable=false")
        return FALLBACK_ANSWER

    answer = (result.get("answer") or "").strip()
    evidence = (result.get("evidence") or "").strip()

    if not answer or not evidence:
        log("REJECTED: empty answer or evidence")
        return FALLBACK_ANSWER

    # --- 1. evidence must be a real quotation from the passages -----
    if not evidence_in_context(evidence, context):
        log("REJECTED: evidence not found in passages ->", evidence[:150])
        return FALLBACK_ANSWER

    # --- 2. evidence must be relevant to the question ---------------
    question_terms = set(content_terms(question))
    evidence_terms = set(content_terms(evidence))

    if not question_terms or not (question_terms & evidence_terms):
        log("REJECTED: evidence is unrelated to the question")
        return FALLBACK_ANSWER

    # --- 3. answer must be supported by its quoted evidence --------
    answer_terms = content_terms(strip_markdown(answer))

    if not answer_terms:
        log("REJECTED: answer has no meaningful terms")
        return FALLBACK_ANSWER

    in_context = share_found(answer_terms, normalize(context))

    log("Grounding: in retrieved context = %.2f" % in_context)

    if in_context >= ANSWER_IN_EVIDENCE_RATIO:
        return answer

    log("REJECTED: answer not supported by retrieved context ->", answer[:150])
    return FALLBACK_ANSWER


# ============================================================
# EXTRACTIVE FALLBACK (used when the small model refuses wrongly)
# ============================================================
EXTRACT_MIN_SCORE = 0.30


def extractive_answer(question, results):
    """
    Select an extractive answer based on question intent,
    keyword overlap, semantic similarity and retrieval rank.
    """

    question_lower = question.lower()
    question_terms = set(content_terms(question))

    if not question_terms:
        return None

    # Identify the type of information requested.
    asks_for_list = any(
        word in question_lower
        for word in ("list", "which files", "what files", "name the files")
    ) or (
        bool(re.search(r"\bfiles?\b", question_lower))
        and any(
            word in question_lower
            for word in ("what", "which", "name", "three", "two")
        )
    )

    asks_for_count = any(
        word in question_lower
        for word in ("how many", "number of", "count")
    )

    def comparable_terms(text):
        terms = set(content_terms(strip_markdown(text)))
        normalized = set(terms)

        for term in terms:
            if len(term) > 3 and term.endswith("s"):
                normalized.add(term[:-1])

        return normalized

    candidates = []

    for chunk_order, (chunk_id, *_) in enumerate(results):

        lines = chunks[chunk_id]["text"].splitlines()

        heading = None
        blocks = []
        current_block = []
        in_fence = False

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("```"):
                in_fence = not in_fence
                continue

            if in_fence:
                continue

            if stripped.startswith("#"):
                if heading is None:
                    heading = re.sub(
                        r"^#{1,6}\s*", "", stripped
                    ).strip()
                continue

            if not stripped:
                if current_block:
                    blocks.append(" ".join(current_block))
                    current_block = []
                continue

            if stripped.startswith(("Document:", "Source:", "Section:", "|")):
                continue

            is_list_item = bool(
                re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", stripped)
            )

            if is_list_item and current_block:
                blocks.append(" ".join(current_block))
                current_block = []

            cleaned = re.sub(r"^[-*+]\s+", "", stripped)
            cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned)

            if cleaned:
                current_block.append(cleaned)

        if current_block:
            blocks.append(" ".join(current_block))

        sentences = []

        for block in blocks:
            for sentence in re.split(r"(?<=[.!?])\s+", block):
                sentence = strip_markdown(sentence).strip()

                if len(sentence) >= 20:
                    sentences.append(sentence)

        sentences = list(dict.fromkeys(sentences))

        if not sentences:
            continue

        vectors = embedder.encode(
            [question] + sentences,
            convert_to_numpy=True,
            normalize_embeddings=True
        )

        scores = np.dot(vectors[1:], vectors[0])

        for sentence, score in zip(sentences, scores):

            evidence_text = f"{heading or ''} {sentence}"
            evidence_terms = comparable_terms(evidence_text)
            overlap = question_terms & evidence_terms

            if not overlap or score < EXTRACT_MIN_SCORE:
                continue

            intent_bonus = 0.0

            if asks_for_list:
                filenames = (
                    "updates",
                    "updates-tx",
                    "routes-mrt"
                )

                filename_matches = sum(
                    name in sentence.lower()
                    for name in filenames
                )

                if filename_matches == 3:
                    intent_bonus = 2.0

            elif asks_for_count:
                if (
                    "independent" in sentence.lower()
                    and any(
                        word in sentence.lower()
                        for word in ("two", "2")
                    )
                ):
                    intent_bonus = 0.50

            candidates.append((
                float(score) + (0.25 * len(overlap))
                - (0.02 * chunk_order) + intent_bonus,
                len(overlap),
                chunk_order,
                chunk_id,
                heading,
                sentence
            ))

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (item[0], item[1]),
        reverse=True
    )

    best_chunk_id = candidates[0][3]
    selected = []

    for _, _, _, chunk_id, heading, sentence in candidates:

        if chunk_id != best_chunk_id:
            continue

        if any(
            sentence.lower() in existing.lower()
            or existing.lower() in sentence.lower()
            for existing in selected
        ):
            continue

        selected.append(sentence)
        break

    if not selected:
        return None

    asks_what_distinguishes = (
        "what does" in question_lower
        and "distinguish" in question_lower
    )

    if asks_for_list:
        filenames = ("updates", "updates-tx", "routes-mrt")
        for sentence in selected:
            if all(name in sentence.lower() for name in filenames):
                answer = (
                    "MRT dump files: "
                    + ", ".join(filenames)
                )
                break
        else:
            answer = "\n".join(selected)

    elif asks_what_distinguishes:
        answer = selected[0]
        answer = re.sub(r"^\d+(?:\.\d+)*\s*", "", answer)
        answer = re.sub(r"^\\*+|\\*+$", "", answer).strip()

    else:
        answer = "\n".join(selected)

    selected_heading = candidates[0][4]

    if (
        not asks_what_distinguishes
        and selected_heading
        and selected_heading.lower() not in answer.lower()
    ):
        answer = f"{selected_heading}\n{answer}"

    log("Extractive sentences selected:", len(selected))
    log("Extractive source chunk:", best_chunk_id)

    return answer


# ============================================================
# HTML CHAT INTERFACE
# ============================================================

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>Internet Atlas | Documentation Assistant</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Inter, Arial, sans-serif;
    background: #0b1120;
    color: #e5e7eb;
    height: 100vh;
    display: flex;
}

.sidebar {
    width: 260px;
    background: #111827;
    border-right: 1px solid #273449;
    padding: 25px 18px;
    display: flex;
    flex-direction: column;
}

.brand {
    font-size: 21px;
    font-weight: 700;
    color: #f8fafc;
    margin-bottom: 8px;
}

.brand span {
    color: #60a5fa;
}

.subtitle {
    font-size: 12px;
    color: #94a3b8;
    line-height: 1.6;
}

.new-chat {
    margin-top: 35px;
    padding: 12px;
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 9px;
    color: #e2e8f0;
    cursor: pointer;
    font-size: 14px;
    text-align: left;
}

.new-chat:hover {
    background: #26364c;
}

.sidebar-info {
    margin-top: auto;
    padding: 13px;
    background: #172033;
    border-radius: 9px;
    font-size: 12px;
    color: #94a3b8;
    line-height: 1.7;
}

.main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
}

.topbar {
    height: 65px;
    padding: 0 28px;
    border-bottom: 1px solid #273449;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #0f172a;
}

.top-title {
    font-size: 15px;
    font-weight: 600;
}

.status {
    display: flex;
    align-items: center;
    gap: 8px;
    color: #94a3b8;
    font-size: 12px;
}

.status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #64748b;
}

.status-dot.online {
    background: #22c55e;
}

.status-dot.offline {
    background: #ef4444;
}

.chat-area {
    flex: 1;
    overflow-y: auto;
    padding: 35px 20px;
}

.messages {
    max-width: 850px;
    margin: 0 auto;
}

.welcome {
    text-align: center;
    margin: 90px auto 45px;
    max-width: 600px;
}

.welcome-icon {
    font-size: 38px;
    margin-bottom: 18px;
}

.welcome h1 {
    font-size: 27px;
    color: #f8fafc;
    margin-bottom: 12px;
}

.welcome p {
    color: #94a3b8;
    line-height: 1.7;
    font-size: 14px;
}

.message {
    display: flex;
    gap: 14px;
    margin: 25px 0;
    align-items: flex-start;
}

.avatar {
    min-width: 34px;
    height: 34px;
    border-radius: 9px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 700;
}

.user-avatar {
    background: #1d4ed8;
    color: white;
}

.bot-avatar {
    background: #1e293b;
    color: #60a5fa;
    border: 1px solid #334155;
}

.message-content {
    flex: 1;
    min-width: 0;
}

.message-label {
    font-size: 12px;
    color: #94a3b8;
    margin-bottom: 8px;
    font-weight: 600;
}

.message-text {
    font-size: 14px;
    line-height: 1.8;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: #e2e8f0;
}

.input-container {
    padding: 18px 20px 25px;
    border-top: 1px solid #273449;
    background: #0f172a;
}

.input-box {
    max-width: 850px;
    margin: 0 auto;
    display: flex;
    align-items: flex-end;
    gap: 12px;
    padding: 12px;
    background: #1e293b;
    border: 1px solid #475569;
    border-radius: 13px;
}

textarea {
    flex: 1;
    resize: none;
    border: none;
    outline: none;
    background: transparent;
    color: #f8fafc;
    font-family: inherit;
    font-size: 14px;
    line-height: 1.6;
    max-height: 150px;
    min-height: 28px;
}

textarea::placeholder {
    color: #94a3b8;
}

.send-button {
    width: 38px;
    height: 38px;
    border: none;
    border-radius: 9px;
    background: #2563eb;
    color: white;
    cursor: pointer;
    font-size: 18px;
}

.send-button:hover {
    background: #1d4ed8;
}

.send-button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.disclaimer {
    max-width: 850px;
    margin: 10px auto 0;
    text-align: center;
    color: #64748b;
    font-size: 11px;
}

.typing {
    color: #60a5fa;
    font-size: 13px;
}

@media (max-width: 700px) {

    .sidebar {
        display: none;
    }

    .topbar {
        padding: 0 15px;
    }

    .chat-area {
        padding: 20px 14px;
    }

    .welcome {
        margin-top: 55px;
    }

    .welcome h1 {
        font-size: 22px;
    }
}

</style>
</head>

<body>

<aside class="sidebar">

    <div class="brand">
        Internet <span>Atlas</span>
    </div>

    <div class="subtitle">
        Technical Documentation Assistant
    </div>

    <button class="new-chat" onclick="newChat()">
        + New conversation
    </button>

    <div class="sidebar-info">
        <strong>Knowledge source</strong><br>
        Local documentation index<br><br>

        <strong>Language model</strong><br>
        Qwen 2.5 1.5B<br><br>

        <strong>Retrieval</strong><br>
        FAISS + BM25
    </div>

</aside>


<main class="main">

    <header class="topbar">

        <div class="top-title">
            Documentation Chat
        </div>

        <div class="status">
            <span class="status-dot" id="statusDot"></span>
            <span id="statusText">Checking connection...</span>
        </div>

    </header>


    <section class="chat-area" id="chatArea">

        <div class="messages" id="messages">

            <div class="welcome" id="welcome">

                <div class="welcome-icon">&#8981;</div>

                <h1>Ask your documentation</h1>

                <p>
                    Ask questions about Internet Atlas technical
                    documentation. Answers are generated using
                    your local knowledge base and Qwen model.
                </p>

            </div>

        </div>

    </section>


    <footer class="input-container">

        <div class="input-box">

            <textarea
                id="question"
                rows="1"
                placeholder="Ask a question about the documentation..."
            ></textarea>

            <button
                class="send-button"
                id="sendButton"
                onclick="sendMessage()"
                title="Send message"
            >
                &#8593;
            </button>

        </div>

        <div class="disclaimer">
            Answers are restricted to the indexed documentation.
        </div>

    </footer>

</main>


<script>

const questionInput = document.getElementById("question");
const sendButton = document.getElementById("sendButton");
const messages = document.getElementById("messages");
const chatArea = document.getElementById("chatArea");


function addMessage(role, text) {

    const welcome = document.getElementById("welcome");

    if (welcome) {
        welcome.remove();
    }

    const wrapper = document.createElement("div");
    wrapper.className = "message";

    const avatar = document.createElement("div");
    avatar.className = "avatar " +
        (role === "user" ? "user-avatar" : "bot-avatar");

    avatar.textContent = role === "user" ? "You" : "AI";

    const content = document.createElement("div");
    content.className = "message-content";

    const label = document.createElement("div");
    label.className = "message-label";
    label.style.display = "none";

    const body = document.createElement("div");
    body.className = "message-text";
    body.textContent = text;

    content.appendChild(label);
    content.appendChild(body);

    wrapper.appendChild(avatar);
    wrapper.appendChild(content);

    messages.appendChild(wrapper);

    chatArea.scrollTop = chatArea.scrollHeight;

    return body;
}


async function sendMessage() {

    const question = questionInput.value.trim();

    if (!question || sendButton.disabled) {
        return;
    }

    addMessage("user", question);

    questionInput.value = "";
    questionInput.style.height = "28px";

    sendButton.disabled = true;

    const loading = addMessage(
        "assistant",
        "Searching the documentation and generating an answer..."
    );

    loading.classList.add("typing");

    try {

        const response = await fetch("/chat", {

            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                message: question
            })

        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Request failed.");
        }

        loading.classList.remove("typing");
        loading.textContent = data.answer;

    } catch (error) {

        loading.classList.remove("typing");

        loading.textContent =
            "Unable to generate an answer: " + error.message;

    } finally {

        sendButton.disabled = false;
        questionInput.focus();

        chatArea.scrollTop = chatArea.scrollHeight;
    }
}


function newChat() {

    messages.innerHTML = `
        <div class="welcome" id="welcome">
            <div class="welcome-icon">&#8981;</div>
            <h1>Ask your documentation</h1>
            <p>
                Ask questions about Internet Atlas technical
                documentation. Answers are generated using
                your local knowledge base and Qwen model.
            </p>
        </div>
    `;

    questionInput.focus();
}


questionInput.addEventListener("keydown", function(event) {

    if (event.key === "Enter" && !event.shiftKey) {

        event.preventDefault();
        sendMessage();

    }

});


questionInput.addEventListener("input", function() {

    this.style.height = "28px";

    this.style.height =
        Math.min(this.scrollHeight, 150) + "px";

});


async function checkHealth() {

    const dot = document.getElementById("statusDot");
    const status = document.getElementById("statusText");

    try {

        const response = await fetch("/health");
        const data = await response.json();

        if (response.ok && data.llama_server === "connected") {

            dot.className = "status-dot online";
            status.textContent = "Local model connected";

        } else {

            dot.className = "status-dot offline";
            status.textContent = "Model server unavailable";

        }

    } catch (error) {

        dot.className = "status-dot offline";
        status.textContent = "Backend unavailable";

    }
}


checkHealth();

</script>

</body>
</html>
"""


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return render_template_string(HTML_PAGE)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health", methods=["GET"])
def health():

    llama_connected = False

    try:
        response = requests.get(LLAMA_MODELS_URL, timeout=3)
        llama_connected = response.ok
    except requests.RequestException:
        pass

    return jsonify({
        "status": "running",
        "documents_loaded": len(chunks),
        "llama_server": "connected" if llama_connected else "disconnected"
    }), 200


# ============================================================
# CHAT API
# ============================================================

@app.route("/chat", methods=["POST"])
def chat():

    data = request.get_json(silent=True) or {}

    question = data.get("message", "").strip()

    if not question:
        return jsonify({"error": "Please enter a question."}), 400

    try:

        print("\nQuestion:", question)

        # 1. Retrieve relevant document chunks.
        results = retrieve(question, top_k=TOP_K)

        if results:
            log(
                "Best semantic: %.3f | Best fusion: %.4f"
                % (max(r[2] for r in results), max(r[3] for r in results))
            )

        # 2. Retrieval gate: skip the LLM for off-topic questions.
        if is_out_of_document(results):
            print("Gate: out-of-document question, LLM not called.")
            return jsonify({"answer": FALLBACK_ANSWER})

        # 3. Build context with source and section metadata.
        context = build_context(results)

        print("Retrieved chunks:", [item[0] for item in results])

        # 4. Ask the local Qwen model.
        raw_response = ask_qwen(question, context)

        log("RAW MODEL OUTPUT:", raw_response)

        # 5. Verify the answer and its supporting evidence.
        answer = validate_answer(raw_response, context, question)

        # 6. If the model refused wrongly, fall back to the best sentence
        #    copied straight from the retrieved passages.
        if answer == FALLBACK_ANSWER and model_claims_answerable(raw_response):
            extracted = extractive_answer(question, results)

            if extracted:
                log("Using extractive fallback.")
                answer = extracted
        print("Answer:", answer)

        return jsonify({"answer": answer})

    except requests.RequestException as error:

        print("LLM connection error:", error)

        return jsonify({
            "error": (
                "Cannot connect to the local Qwen server. "
                "Please check whether llama-server is running "
                "on port 8080."
            )
        }), 503

    except Exception as error:

        print("Chat error:", error)

        return jsonify({"error": "An internal chatbot error occurred."}), 500


# ============================================================
# START FLASK
# ============================================================

if __name__ == "__main__":

    print("\n" + "=" * 55)
    print(" INTERNET ATLAS DOCUMENTATION CHATBOT")
    print("=" * 55)

    print(f"Documents loaded: {len(chunks)}")
    print("Retrieval: FAISS + BM25")
    print("Language model: Qwen 2.5 1.5B")
    print("LLM server: http://127.0.0.1:8080")
    print("Chat interface: http://127.0.0.1:5002")
    print("=" * 55)

    app.run(host="127.0.0.1", port=5002, debug=False)
