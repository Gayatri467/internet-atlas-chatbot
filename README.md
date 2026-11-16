# Internet Atlas Chatbot

An offline, document-grounded chatbot that answers questions using technical documentation from the Internet Atlas project.

The application uses a local Qwen 2.5 1.5B Instruct language model served through llama.cpp. It combines semantic and keyword-based document retrieval to generate answers grounded in the available project documentation.

## Features

- Answers questions using Internet Atlas documentation.
- Semantic document retrieval using FAISS.
- Keyword-based retrieval using BM25.
- Local answer generation using Qwen 2.5 1.5B Instruct.
- Answer validation to reduce unsupported responses.
- Flask-based chatbot interface.
- Evaluation and retrieval-testing scripts.
- No Ollama dependency.

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
├── requirements.txt
├── README.md
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

## Models and Runtime

The chatbot uses the following components:

1. **Qwen 2.5 1.5B Instruct (Q4_K_M GGUF)** – generates answers locally.
2. **all-MiniLM-L6-v2** – generates embeddings for document retrieval.
3. **llama.cpp** – runs the Qwen model through a local HTTP server.

The model files and llama.cpp runtime are maintained separately and are not included in this repository.

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/Gayatri467/internet-atlas-chatbot.git
cd internet-atlas-chatbot
```

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Obtain the models and llama.cpp

The Qwen GGUF model, embedding model, and llama.cpp runtime are maintained in the separate Docling HTML project.

Ensure these resources are available locally and that the model paths configured in the chatbot application point to their locations.

## Start the Qwen Server Using llama.cpp

Open a separate terminal and navigate to the Docling HTML project directory:

```bash
cd ~/docling-html-project
```

Start the Qwen 2.5 1.5B Instruct server:

```bash
./llama.cpp/build/bin/llama-server \
  -m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  -c 4096 \
  -t 2
```

**Server configuration:**

| Setting | Value |
|---|---|
| Model | Qwen 2.5 1.5B Instruct |
| Quantization | Q4_K_M |
| Host | 127.0.0.1 |
| Port | 8080 |
| Context size | 4096 |
| CPU threads | 2 |

The server provides an OpenAI-compatible chat completion endpoint:

```text
http://127.0.0.1:8080/v1/chat/completions
```

Keep this terminal running while using the chatbot.

## Run the Chatbot

Open another terminal:

```bash
cd ~/internet-atlas-chatbot
source .venv/bin/activate
python app.py
```

Open the local Flask address displayed in the terminal.

## Testing

Run document retrieval tests:

```bash
python test_retrieval.py
```

Run chatbot evaluation:

```bash
python eval.py
```

## Important Notes

- The chatbot uses local inference through llama.cpp.
- Ollama is not required.
- Model files and the llama.cpp runtime are maintained separately.
- The `index/` directory contains the existing searchable document index.
- The `documents/` directory contains Internet Atlas technical documentation.
- Answers are intended to be grounded in the available documentation.
- This repository is private and accessible only to invited collaborators.

## License

This repository is private and intended for project collaboration.
