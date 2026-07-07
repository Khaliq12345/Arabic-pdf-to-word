import sys


sys.path.append(".")

import os
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    Security,
)
from celery.result import AsyncResult
from fastapi.responses import PlainTextResponse, Response
from api.dependency import verify_api_key
from celery_app import run_pipeline_task
import celery_app

# from pipeline import split_pdf, process_images_to_docx, combine_docx_files

app = FastAPI(
    title="PDF to Word Processing API", dependencies=[Security(verify_api_key)]
)

# Simple in-memory database to store job state
# jobs_db = {}


# def run_pipeline_task(job_id: str, pdf_bytes: bytes):
#     """The long-running worker function executed in the background."""
#     jobs_db[job_id]["status"] = "processing"
#     target_folder = None
#
#     try:
#         # Step 1: Split PDF into images
#         target_folder = split_pdf(binary_data=pdf_bytes)
#         jobs_db[job_id]["target_folder"] = target_folder
#
#         # Step 2: Extract text via Gemini
#         created_docx_list = process_images_to_docx(target_folder)
#
#         # Step 3: Merge documents
#         combine_docx_files(created_docx_list, target_folder)
#
#         final_docx_path = os.path.join(target_folder, "combined_final.docx")
#
#         if os.path.exists(final_docx_path):
#             # Read the final binary data into memory so we can safely purge the folder
#             # 1. Read and save the raw binary bytes of the Word file
#             with open(final_docx_path, "rb") as f:
#                 jobs_db[job_id]["result_binary"] = f.read()
#
#             # 2. Extract the actual text paragraphs from the Word file
#             doc = Document(final_docx_path)
#             full_text = []
#             for paragraph in doc.paragraphs:
#                 full_text.append(paragraph.text)
#
#             # Join the paragraphs together with line breaks and save it
#             jobs_db[job_id]["result_text"] = "\n".join(full_text)
#
#             jobs_db[job_id]["status"] = "completed"
#         else:
#             jobs_db[job_id]["status"] = "failed"
#             jobs_db[job_id]["error"] = "Pipeline completed but final file was missing."
#
#     except Exception as e:
#         jobs_db[job_id]["status"] = "failed"
#         jobs_db[job_id]["error"] = str(e)
#
#     finally:
#         # Instantly clean up the local storage directory since the final binary is cached in jobs_db
#         if target_folder and os.path.exists(target_folder):
#             shutil.rmtree(target_folder)


@app.post("/jobs")
async def create_conversion_job(file: UploadFile = File(...)):
    """1. Submit Endpoint: Accepts file, hands off to Celery, returns Job ID instantly."""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    pdf_bytes = await file.read()
    filename = os.path.splitext(file.filename)[0]

    # Hand off execution to Celery — the task ID becomes our job_id
    async_result = run_pipeline_task.delay(pdf_bytes)

    return {
        "job_id": async_result.id,
        "status": "queued",
        "filename": filename,
        "poll_url": f"/jobs/{async_result.id}",
    }


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """2. Polling Endpoint: Client hits this periodically to check progress."""
    res = AsyncResult(job_id, app=celery_app.celery_app)

    if res.state == "FAILURE":
        return {
            "job_id": job_id,
            "status": "failed",
            "error": str(res.info),
            "download_ready": False,
        }

    return {
        "job_id": job_id,
        "status": res.state.lower(),  # pending / processing / success
        "error": None,
        "download_ready": res.state == "SUCCESS",
    }


@app.get("/jobs/{job_id}/content/binary")
async def get_job_binary(job_id: str):
    """Delivers the raw binary data (.docx)."""
    res = AsyncResult(job_id, app=celery_app.celery_app)

    if res.state != "SUCCESS":
        raise HTTPException(
            status_code=400,
            detail=f"File is not ready. Current state: {res.state.lower()}",
        )

    result_binary = base64.b64decode(res.result["result_binary_b64"])

    return Response(
        content=result_binary,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={job_id}.docx"},
    )


@app.get("/jobs/{job_id}/content/text")
async def get_job_text(job_id: str):
    """Delivers the raw extracted text string."""
    res = AsyncResult(job_id, app=celery_app.celery_app)

    if res.state != "SUCCESS":
        raise HTTPException(
            status_code=400,
            detail=f"File is not ready. Current state: {res.state.lower()}",
        )

    return PlainTextResponse(
        content=res.result["result_text"], media_type="text/plain; charset=utf-8"
    )
