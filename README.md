# Internet Atlas Chatbot

An offline, document-grounded chatbot that answers questions using technical documentation from the Internet Atlas project.

The chatbot uses a local language model through **llama.cpp** and a document search index to retrieve relevant information and generate answers based on the available documentation.

## Features

- Answers questions using Internet Atlas project documentation.
- Supports semantic and keyword-based document retrieval.
- Uses a FAISS index for efficient document search.
- Runs a local Qwen 2.5 1.5B Instruct model through llama.cpp.
- Includes answer validation to reduce unsupported responses.
- Provides evaluation and retrieval-testing scripts.
- Uses a Flask application for the chatbot interface and backend.

## Technology Stack

| Component | Technology |
|---|---|
| Programming language | Python |
| Web framework | Flask |
| Language model | Qwen 2.5 1.5B Instruct |
| Model runtime | llama.cpp |
| Vector search | FAISS |
| Embedding model | all-MiniLM-L6-v2 |
| Keyword search | BM25 |

## Project Structure

```text
internet-atlas-chatbot/
│
├── app.py
├── build_index.py
├── eval.py
├── test_retrieval.py
├── .gitignore
│
├── documents/
│   ├── docling-chatbot/
│   ├── frr-mrt/
│   ├── multi-mrt-data-poc/
│   └── single-mrt-data-poc/
│
└── index/
    ├── chunks.json
    ├── documents.faiss
    └── manifest.json
```

## Project Files

| File | Description |
|---|---|
| `app.py` | Main chatbot application, retrieval, and answer generation |
| `build_index.py` | Builds the searchable document index |
| `eval.py` | Evaluates chatbot performance |
| `test_retrieval.py` | Tests document retrieval |
| `documents/` | Contains Internet Atlas project README documents |
| `index/` | Contains the generated FAISS index and document chunks |

## Models Required

The following models are used separately from the source code:

1. **Qwen 2.5 1.5B Instruct (GGUF)** – used for local answer generation through llama.cpp.
2. **all-MiniLM-L6-v2** – used to generate document embeddings for retrieval.

These model files are not included in this repository and must be obtained separately.

## Setup

### 1. Clone the repository

```bash
git clone <YOUR_PRIVATE_REPOSITORY_URL>
cd internet-atlas-chatbot
```

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

Install the Python dependencies listed in `requirements.txt` (to be added).

### 4. Configure the models

Obtain the required Qwen GGUF model and embedding model.

Configure their local paths according to the application settings.

### 5. Start the llama.cpp server

Start the local llama.cpp server using the downloaded Qwen model and the server configuration expected by the application.

### 6. Run the chatbot

```bash
python app.py
```

Open the local address displayed by Flask in your browser.

## Testing

Run the retrieval tests:

```bash
python test_retrieval.py
```

Run the evaluation script:

```bash
python eval.py
```

## Important Notes

- The chatbot uses local model inference and does not require Ollama.
- Model files and the Python virtual environment are maintained separately from this repository.
- The `index/` directory contains the current generated document index.
- Answers are intended to be grounded in the documentation included in the project.
- Access to this repository is restricted to invited collaborators.

## License

This repository is private and intended for project collaboration.

