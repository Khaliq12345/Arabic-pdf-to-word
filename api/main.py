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
import base64
from celery.result import AsyncResult
from fastapi.responses import PlainTextResponse, Response
from api.dependency import verify_api_key
from celery_app import run_pipeline_task
import celery_app

app = FastAPI(
    title="PDF to Word Processing API", dependencies=[Security(verify_api_key)]
)


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
