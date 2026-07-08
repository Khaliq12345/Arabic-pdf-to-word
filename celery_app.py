import os
import shutil
import base64
from celery import Celery
from pipeline import split_pdf, process_images_to_docx, combine_docx_files
from docx import Document
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
is_ssl = redis_url.startswith("rediss://")
ssl_suffix = "?ssl_cert_reqs=CERT_NONE" if is_ssl else ""

celery_app = Celery(
    "pipeline",
    broker=f"{redis_url}/10{ssl_suffix}",
    backend=f"{redis_url}/11{ssl_suffix}",
)

celery_app.conf.update(
    task_track_started=True,  # clients see "STARTED" instead of just PENDING/SUCCESS
    result_extended=True,
)


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
@celery_app.task(bind=True, name="run_pipeline_task")
def run_pipeline_task(self, pdf_bytes: bytes):
    """The long-running worker task executed by Celery."""
    target_folder = None
    try:
        self.update_state(state="PROCESSING", meta={"stage": "splitting_pdf"})

        target_folder = split_pdf(binary_data=pdf_bytes)

        self.update_state(
            state="PROCESSING",
            meta={"stage": "extracting_text", "target_folder": target_folder},
        )

        created_docx_list = process_images_to_docx(target_folder)

        self.update_state(
            state="PROCESSING",
            meta={"stage": "merging_documents", "target_folder": target_folder},
        )

        combine_docx_files(created_docx_list, target_folder)

        final_docx_path = os.path.join(target_folder, "combined_final.docx")

        if not os.path.exists(final_docx_path):
            raise FileNotFoundError("Pipeline completed but final file was missing.")

        with open(final_docx_path, "rb") as f:
            result_binary = f.read()

        doc = Document(final_docx_path)

        full_text = [paragraph.text for paragraph in doc.paragraphs]

        result_text = "\n".join(full_text)

        return {
            "status": "completed",
            "result_text": result_text,
            "result_binary_b64": base64.b64encode(result_binary).decode("utf-8"),
        }

    except Exception as exc:

        # IMPORTANT: explicitly mark failure
        self.update_state(
            state="FAILURE",
            meta={
                "stage": "failed",
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise

    finally:
        if target_folder and os.path.exists(target_folder):
            shutil.rmtree(target_folder)


@celery_app.task(trail=True)
def pow2(i):
    return i**2
