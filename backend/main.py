

import os
import json
import io
from typing import Optional

from dotenv import load_dotenv
load_dotenv()  # loads .env in local/VS Code dev; no-op if the file doesn't exist

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from groq import Groq
from pypdf import PdfReader

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
MODEL_NAME = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "120000"))  # guard rail vs. huge docs
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY is not set. /api/analyze will fail until it is.")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app = FastAPI(title="Document Analyzer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Prompt templates per analysis mode
# --------------------------------------------------------------------------

MODE_PROMPTS = {
    "summarize": (
        "You are an expert document analyst. Read the document below and produce a "
        "clear, well-structured summary. Include: (1) a 2-3 sentence executive "
        "summary, (2) key points as bullets, (3) any notable numbers, dates, or "
        "names worth remembering. Use Markdown formatting."
    ),
    "extract": (
        "You are an expert data-extraction assistant. Read the document below and "
        "extract all structured, factual information you can find: names, dates, "
        "figures, obligations, definitions, and action items. Present the result as "
        "clearly labeled Markdown sections and tables where useful. Do not "
        "editorialize or add information that is not in the document."
    ),
    "rewrite": (
        "You are an expert editor. Rewrite the document below to be clearer, more "
        "concise, and more professional, while preserving all factual meaning. "
        "Fix grammar and awkward phrasing. Return only the rewritten text, in "
        "Markdown, followed by a short bullet list of the main changes you made."
    ),
    "analyze": (
        "You are an expert analyst. Critically analyze the document below: identify "
        "its purpose, intended audience, strengths, weaknesses, potential risks or "
        "ambiguities, and any missing information. Structure your answer with "
        "Markdown headings."
    ),
}

DEFAULT_MODE = "summarize"


class TextAnalyzeRequest(BaseModel):
    text: str
    mode: Optional[str] = DEFAULT_MODE
    custom_instructions: Optional[str] = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def extract_pdf_text(file_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {exc}")

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise HTTPException(status_code=400, detail="PDF is password protected.")

    pages_text = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:
            continue

    text = "\n\n".join(pages_text).strip()
    if not text:
        raise HTTPException(
            status_code=422,
            detail="No extractable text found in this PDF (it may be a scanned image).",
        )
    return text


def build_prompt(document_text: str, mode: str, custom_instructions: Optional[str]) -> str:
    mode = mode if mode in MODE_PROMPTS else DEFAULT_MODE
    instructions = MODE_PROMPTS[mode]
    if custom_instructions:
        instructions += (
            f"\n\nAdditional user instructions (follow these too): {custom_instructions.strip()}"
        )

    truncated_note = ""
    if len(document_text) > MAX_CHARS:
        document_text = document_text[:MAX_CHARS]
        truncated_note = "\n\n[NOTE: The document was truncated to fit length limits.]"

    return (
        f"{instructions}\n\n"
        f"--- DOCUMENT START ---\n{document_text}\n--- DOCUMENT END ---"
        f"{truncated_note}"
    )


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def stream_claude_response(prompt: str):
    """Yields Server-Sent Events as Groq streams its response."""
    if client is None:
        yield sse_event({
            "type": "error",
            "message": "Server is missing GROQ_API_KEY."
        })
        return

    try:
        stream = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "user", "content": prompt}
            ],
            max_tokens=4096,
            stream=True,
        )

        for chunk in stream:
            text = chunk.choices[0].delta.content
            if text:
                yield sse_event({
                    "type": "delta",
                    "text": text
                })

        yield sse_event({"type": "done"})

    except Exception as e:
        yield sse_event({
            "type": "error",
            "message": f"Groq API error: {str(e)}"
        })


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "key_configured": bool(GROQ_API_KEY)}


@app.post("/api/analyze/text")
async def analyze_text(payload: TextAnalyzeRequest):
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=400, detail="No text provided.")
    prompt = build_prompt(payload.text, payload.mode or DEFAULT_MODE, payload.custom_instructions)
    return StreamingResponse(stream_claude_response(prompt), media_type="text/event-stream")


@app.post("/api/analyze/pdf")
async def analyze_pdf(
    file: UploadFile = File(...),
    mode: str = Form(DEFAULT_MODE),
    custom_instructions: str = Form(""),
):
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024:  # 20 MB
        raise HTTPException(status_code=413, detail="File too large (20 MB limit).")

    document_text = extract_pdf_text(file_bytes)
    prompt = build_prompt(document_text, mode, custom_instructions)
    return StreamingResponse(stream_claude_response(prompt), media_type="text/event-stream")


# --------------------------------------------------------------------------
# Serve the frontend (single-container deployment)
# --------------------------------------------------------------------------

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
