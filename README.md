# AR-OCR

Arabic PDF OCR and conversion service. Submits a PDF, runs it through an
extraction/OCR pipeline (powered by Gemini), and returns the result as a
`.docx` file or plain text. Built with FastAPI, managed with `uv`.

## Requirements

- Python (version per `pyproject.toml`)
- [`uv`](https://docs.astral.sh/uv/) for dependency management and running the app
- A Gemini API key

## Installation

1. **Install `uv`** (skip if already installed):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone the repo and move into it:**

   ```bash
   git clone <your-repo-url> AR-ocr
   cd AR-ocr
   ```

3. **Install dependencies** (creates `.venv` and installs from `uv.lock`):

   ```bash
   uv sync
   ```

4. **Create your `.env` file** in the project root:

   ```bash
   cp .env.example .env   # if an example file exists, otherwise create it manually
   ```

   Add the following to `.env`:

   ```env
   GEMINI_API_KEY=your-gemini-api-key-here
   API_KEY=your-own-secret-string-here
   ```

   - `GEMINI_API_KEY` — used by the pipeline to call Gemini for OCR/extraction.
   - `API_KEY` — used to authenticate requests to this service's own endpoints
     (sent as the `X-API-Key` header). Pick any long random string, e.g.:

     ```bash
     python3 -c "import secrets; print(secrets.token_urlsafe(32))"
     ```

   `.env` is already excluded via `.gitignore` — never commit it.

## Running the server

```bash
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

> Adjust `api.main:app` to match wherever your `FastAPI()` instance is
> actually defined, if it differs (e.g. `app:app` or `main:app`).

The API will be available at `http://localhost:8000`, with interactive docs
at `http://localhost:8000/docs`.

## Authentication

Every endpoint requires an `X-API-Key` header matching the `API_KEY` value
in your `.env`. Requests without a valid key receive `401 Unauthorized`.

## API Reference

### Submit a PDF for conversion

```
POST /jobs
```

Multipart form upload, field name `file`, must be a `.pdf`.

```bash
curl -X POST http://localhost:8000/jobs \
  -H "X-API-Key: $API_KEY" \
  -F "file=@Ar.pdf"
```

Response:

```json
{ "job_id": "...", "status": "queued", "poll_url": "/jobs/..." }
```

### Poll job status

```
GET /jobs/{job_id}
```

```bash
curl http://localhost:8000/jobs/<job_id> \
  -H "X-API-Key: $API_KEY"
```

Response:

```json
{ "job_id": "...", "status": "completed", "error": null, "download_ready": true }
```

### Download the result as .docx

```
GET /jobs/{job_id}/content/binary
```

```bash
curl http://localhost:8000/jobs/<job_id>/content/binary \
  -H "X-API-Key: $API_KEY" \
  -o output.docx
```

### Get the result as plain text

```
GET /jobs/{job_id}/content/text
```

```bash
curl http://localhost:8000/jobs/<job_id>/content/text \
  -H "X-API-Key: $API_KEY"
```

## Project structure

```
AR-ocr/
├── api/                  # FastAPI app and route handlers
├── arabic-template.docx  # DOCX template used for output generation
├── pipeline.py           # OCR / conversion pipeline logic
├── pyproject.toml
├── uv.lock
└── .env                  # not committed — GEMINI_API_KEY, API_KEY
```

## Notes

- Jobs are processed as FastAPI `BackgroundTasks` and stored in memory
  (`jobs_db`); restarting the server clears all job state.
- Only `.pdf` uploads are accepted at the moment.
