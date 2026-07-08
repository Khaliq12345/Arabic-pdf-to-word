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
    """Polling endpoint: Client checks job progress."""

    res = AsyncResult(job_id, app=celery_app.celery_app)

    response = {
        "job_id": job_id,
        "status": res.state.lower(),
        "stage": None,
        "error": None,
        "download_ready": False,
    }

    # Processing state metadata
    if res.state == "PROCESSING":
        if isinstance(res.info, dict):
            response["stage"] = res.info.get("stage")

    # Failure state
    elif res.state == "FAILURE":
        response["status"] = "failed"

        if isinstance(res.info, dict):
            response["error"] = res.info.get("error", "Unknown error occurred")
            response["stage"] = res.info.get("stage")
        else:
            response["error"] = str(res.info)

    # Success state
    elif res.state == "SUCCESS":
        response["download_ready"] = True
        response["status"] = "success"

    return response


@app.get("/jobs/{job_id}/content/binary")
async def get_job_binary(job_id: str):
    """Delivers the raw binary data (.docx)."""

    res = AsyncResult(job_id, app=celery_app.celery_app)

    if res.state != "SUCCESS":
        raise HTTPException(
            status_code=400,
            detail={
                "message": "File is not ready",
                "current_state": res.state.lower(),
            },
        )

    result_binary = base64.b64decode(res.result["result_binary_b64"])

    return Response(
        content=result_binary,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={job_id}.docx"},
    )


@app.get("/jobs/{job_id}/content/text")
async def get_job_text(job_id: str):
    """Delivers the extracted text."""

    res = AsyncResult(job_id, app=celery_app.celery_app)

    if res.state != "SUCCESS":
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Text is not ready",
                "current_state": res.state.lower(),
            },
        )

    return PlainTextResponse(
        content=res.result["result_text"],
        media_type="text/plain; charset=utf-8",
    )
