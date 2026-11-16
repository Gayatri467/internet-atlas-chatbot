# Docling Document Chatbot

An offline document chatbot that converts HTML documents into Markdown using Docling and answers questions using a local Qwen 2.5 1.5B model through llama.cpp.

The initial example document contains BGP and MRT technical information. The input HTML can be replaced with another document without changing the chatbot backend.

## 1. Architecture

The application has four main components:

| Component | Purpose |
|---|---|
| Docling | Converts the input HTML document into Markdown |
| Markdown document | Stores the extracted document content |
| Qwen 2.5 1.5B | Generates answers using the local language model |
| llama.cpp | Runs the Qwen model locally and provides an API |
| Flask backend | Reads the Markdown document and handles chatbot requests |
| ChromaDB | Stores verified exact-question and answer cache entries |
| LibreChat | Provides the chat interface |

### Workflow

1. The user provides an HTML document.
2. Docling converts the HTML into `output.md`.
3. The Flask backend reads the generated Markdown file.
4. The backend sends the document context and question to the local Qwen model through llama.cpp.
5. The answer is returned through the backend.
6. LibreChat provides the user interface.

The chatbot is designed to answer using the supplied document. If the document does not contain the requested information, the backend returns:

```text
The document does not contain this information.
```

ChromaDB is used only for verified exact-question answer caching. It is not used for document retrieval or similarity-based search.

## 2. Project structure

```text
docling-chatbot/
├── README.md
├── main.py
├── document_backend.py
├── input.html
└── requirements.txt
```

Additional files and directories are generated or installed separately:

| File or directory | Description |
|---|---|
| `output.md` | Markdown generated from the HTML document |
| `chroma_db/` | Local verified-answer cache |
| `llama.cpp/` | llama.cpp source and build files |
| `models/` | Downloaded GGUF model |
| `venv/` | Python virtual environment |

The model, llama.cpp build, and Python environment are not included in this repository folder.

## 3. System requirements

The following software is required:

- Linux or WSL Ubuntu
- Python 3
- Git
- CMake and C/C++ build tools
- Docker and Docker Compose (for LibreChat)
- Internet connection for initial dependency and model downloads

The chatbot can run locally after the required packages, model, and application components have been downloaded.

## 4. Set up the Python environment

Open a terminal in the project directory:

```bash
cd docling-chatbot
```

Create a virtual environment:

```bash
python3 -m venv venv
```

Activate it:

```bash
source venv/bin/activate
```

Install the required Python packages:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

The dependencies are:

- `docling` – HTML document conversion
- `Flask` – chatbot backend
- `chromadb` – verified-answer cache

Check the installed packages:

```bash
pip show docling Flask chromadb
```

## 5. Install and build llama.cpp

llama.cpp runs the local GGUF language model and provides the API used by the chatbot backend.

Install the build tools:

```bash
sudo apt update
sudo apt install -y git cmake build-essential
```

Clone llama.cpp:

```bash
git clone https://github.com/ggml-org/llama.cpp.git
```

Build it:

```bash
cd llama.cpp
cmake -B build
cmake --build build --config Release -j2
```

Check that the server executable exists:

```bash
ls -lh build/bin/llama-server
```

Return to the chatbot project directory:

```bash
cd ..
```

## 6. Download the Qwen model

The chatbot uses:

- Model: Qwen 2.5 1.5B Instruct
- Format: GGUF
- Quantization: Q4_K_M

Create the model directory:

```bash
mkdir -p models
```

Download the model:

```bash
wget -O models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
"https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
```

Check the downloaded file:

```bash
ls -lh models/
```

The model is approximately 1 GB. It is not included in this repository and must be downloaded separately.

## 7. Convert HTML into Markdown

The initial input document is:

```text
input.html
```

The conversion script is `main.py`.

It uses Docling to:

1. Read the HTML document.
2. Extract and structure its content.
3. Export the extracted content into Markdown.
4. Save the result as `output.md`.

Run the conversion from the project directory:

```bash
python main.py
```

Check the generated file:

```bash
ls -lh output.md
```

To inspect the generated Markdown:

```bash
less output.md
```

The backend requires `output.md`, so complete this conversion before starting the chatbot backend.

## 8. Start the llama.cpp server

Open a separate terminal.

Navigate to the project directory:

```bash
cd docling-chatbot
```

Start the local model server:

```bash
./llama.cpp/build/bin/llama-server \
-m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
--host 127.0.0.1 \
--port 8080 \
-c 4096 \
-t 2
```

The server listens at:

```text
http://127.0.0.1:8080
```

Keep this terminal running while using the chatbot.

## 9. Start the chatbot backend

Open another terminal.

Activate the Python environment:

```bash
cd docling-chatbot
source venv/bin/activate
```

Start the Flask backend:

```bash
python document_backend.py
```

The backend runs on:

```text
http://127.0.0.1:5001
```

The backend reads `output.md` and communicates with the llama.cpp API at port `8080`.

Keep this terminal running while using the chatbot.

## 10. LibreChat integration

LibreChat is used as the chat interface. It is installed separately and is not included in this project folder.

Clone and prepare LibreChat:

```bash
git clone https://github.com/danny-avila/LibreChat.git
cd LibreChat
cp .env.example .env
```

### 10.1 Configure the custom endpoint

Create `librechat.yaml` in the LibreChat directory:

```yaml
version: 1.3.18

cache: true

endpoints:
  custom:
    - name: "DocumentBot"
      apiKey: "local-no-key"
      baseURL: "http://host.docker.internal:5001/v1"
      models:
        default:
          - "local-model"
        fetch: false
      titleConvo: false
      modelDisplayLabel: "Document Chatbot"
```

### 10.2 Set the configuration path

In the LibreChat `.env` file, set:

```env
CONFIG_PATH=/app/librechat.yaml
```

### 10.3 Mount the configuration file

Create or update `docker-compose.override.yaml` in the LibreChat directory:

```yaml
services:
  api:
    volumes:
      - ./librechat.yaml:/app/librechat.yaml:ro
```

### 10.4 Start LibreChat

From the LibreChat directory, run:

```bash
docker compose up -d
```

Check the running containers:

```bash
docker compose ps
```

Open the interface in a browser:

```text
http://localhost:3080
```

Select the DocumentBot custom endpoint.

The llama.cpp server and Flask backend must be running before sending questions.

## 11. Test the chatbot

Use questions related to the information available in `input.html`.

Example questions:

```text
What is BGP?
What is MRT?
What is the purpose of FRR?
```

Also test a question that is not covered by the input document.

The chatbot should return the configured fallback response when the requested information is not present in the document.

## 12. Use another HTML document

The chatbot can be used with another HTML document.

1. Keep a copy of the original `input.html`.
2. Replace the contents of `input.html` with the new HTML document.
3. Keep the filename as `input.html`.
4. Run the conversion again:

   ```bash
   python main.py
   ```

5. Confirm that the new `output.md` has been generated.
6. Restart the backend if it is already running.
7. Ask questions about the new document.

The conversion script currently expects the filename `input.html`. The backend expects the generated filename `output.md`.

The Python backend does not need to be rewritten just to change the document.

## 13. Troubleshooting

### Docling module not found

Make sure the virtual environment is activated:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

### output.md not found

Run the HTML conversion first:

```bash
python main.py
```

### llama.cpp server executable not found

Check whether the build completed:

```bash
ls -lh llama.cpp/build/bin/llama-server
```

If it is missing, repeat the llama.cpp build steps.

### Model file not found

Check the model directory:

```bash
ls -lh models/
```

Make sure the model filename matches the filename used in the server command.

### Connection refused on port 8080

Check that the llama.cpp server is running and listening on port `8080`.

### Backend connection error

Check that the Flask backend is running on port `5001` and that the llama.cpp server is running on port `8080`.

### LibreChat cannot connect to the backend

Check the custom endpoint URL and confirm that `host.docker.internal` can reach the host backend from the LibreChat container.

Also verify that the backend is running before testing the LibreChat interface.

### Chatbot returns the fallback response

The fallback response means the backend did not find sufficient document evidence for the question. Check that the correct HTML document was converted and that `output.md` contains the expected information.

## 14. Stop the services

Stop the Flask backend and llama.cpp server using:

```text
Ctrl + C
```

Stop LibreChat from its installation directory:

```bash
docker compose down
```

The downloaded model, llama.cpp build, Python environment, generated Markdown, and local cache can be retained for the next run.

## 15. Important notes

- The input filename expected by `main.py` is `input.html`.
- The generated Markdown filename expected by the backend is `output.md`.
- The local Qwen model must be downloaded separately.
- llama.cpp must be built separately.
- LibreChat must be installed and configured separately.
- The backend uses the local document as its answer source.
- ChromaDB is used only for exact-question verified-answer caching, not document retrieval.
