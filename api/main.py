import sys

from docx import Document

sys.path.append(".")

import os
import shutil
import uuid
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
    Security,
)
from fastapi.responses import PlainTextResponse, Response
from api.dependency import verify_api_key
from pipeline import split_pdf, process_images_to_docx, combine_docx_files

app = FastAPI(
    title="PDF to Word Processing API", dependencies=[Security(verify_api_key)]
)

# Simple in-memory database to store job state
jobs_db = {}


def run_pipeline_task(job_id: str, pdf_bytes: bytes):
    """The long-running worker function executed in the background."""
    jobs_db[job_id]["status"] = "processing"
    target_folder = None

    try:
        # Step 1: Split PDF into images
        target_folder = split_pdf(binary_data=pdf_bytes)
        jobs_db[job_id]["target_folder"] = target_folder

        # Step 2: Extract text via Gemini
        created_docx_list = process_images_to_docx(target_folder)

        # Step 3: Merge documents
        combine_docx_files(created_docx_list, target_folder)

        final_docx_path = os.path.join(target_folder, "combined_final.docx")

        if os.path.exists(final_docx_path):
            # Read the final binary data into memory so we can safely purge the folder
            # 1. Read and save the raw binary bytes of the Word file
            with open(final_docx_path, "rb") as f:
                jobs_db[job_id]["result_binary"] = f.read()

            # 2. Extract the actual text paragraphs from the Word file
            doc = Document(final_docx_path)
            full_text = []
            for paragraph in doc.paragraphs:
                full_text.append(paragraph.text)

            # Join the paragraphs together with line breaks and save it
            jobs_db[job_id]["result_text"] = "\n".join(full_text)

            jobs_db[job_id]["status"] = "completed"
        else:
            jobs_db[job_id]["status"] = "failed"
            jobs_db[job_id]["error"] = "Pipeline completed but final file was missing."

    except Exception as e:
        jobs_db[job_id]["status"] = "failed"
        jobs_db[job_id]["error"] = str(e)

    finally:
        # Instantly clean up the local storage directory since the final binary is cached in jobs_db
        if target_folder and os.path.exists(target_folder):
            shutil.rmtree(target_folder)


@app.post("/jobs")
async def create_conversion_job(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    """1. Submit Endpoint: Accepts file, spins up worker, returns Job ID instantly."""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    job_id = str(uuid.uuid4())
    pdf_bytes = await file.read()

    # Register the initial status record
    jobs_db[job_id] = {
        "status": "queued",
        "filename": os.path.splitext(file.filename)[0],
        "result_data": None,
        "error": None,
    }

    # Hand off execution to FastAPI's internal background workers
    background_tasks.add_task(run_pipeline_task, job_id, pdf_bytes)

    return {"job_id": job_id, "status": "queued", "poll_url": f"/jobs/{job_id}"}


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """2. Polling Endpoint: Client hits this periodically to check progress."""
    job = jobs_db.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job entry not found.")

    return {
        "job_id": job_id,
        "status": job["status"],
        "error": job["error"],
        "download_ready": job["status"] == "completed",
    }


@app.get("/jobs/{job_id}/content/binary")
async def get_job_binary(job_id: str):
    """Delivers the raw binary data (.docx) and clears memory."""
    job = jobs_db.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job entry not found.")

    if job["status"] != "completed":
        raise HTTPException(
            status_code=400, detail=f"File is not ready. Current state: {job['status']}"
        )

    # Pop the job out of RAM once the response stream finishes
    # background_tasks.add_task(jobs_db.pop, job_id, None)

    # Return raw binary stream
    return Response(
        content=job.get("result_binary"), media_type="application/octet-stream"
    )


@app.get("/jobs/{job_id}/content/text")
async def get_job_text(job_id: str):
    """Delivers the raw extracted text string and clears memory."""
    job = jobs_db.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job entry not found.")

    if job["status"] != "completed":
        raise HTTPException(
            status_code=400, detail=f"File is not ready. Current state: {job['status']}"
        )

    # Pop the job out of RAM once the response stream finishes
    # background_tasks.add_task(jobs_db.pop, job_id, None)

    # Return plain UTF-8 encoded text directly without any JSON wrapping
    return PlainTextResponse(
        content=job.get("result_text"), media_type="text/plain; charset=utf-8"
    )
