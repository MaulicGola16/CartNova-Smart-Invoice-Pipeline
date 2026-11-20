import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from common.azure_clients import (
    generate_read_only_sas_url,
    get_blob_service_client,
    get_cosmos_container,
    get_queue_client,
    init_storage_resources,
)
from common.config import CONFIG
from common.logger import get_logger, log_invoice

logger = get_logger("intake_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_storage_resources()
        logger.info("Storage containers, queue, and Cosmos DB container initialized.")
    except Exception as e:
        logger.error(f"Startup resource initialization failed: {e}")
    yield


app = FastAPI(title="CartNova Smart Invoice Intake API", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "REQUEST_ERROR", "detail": str(exc.detail)},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_SERVER_ERROR", "detail": "An unexpected error occurred."},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/invoices", status_code=202)
async def upload_invoice(
    file: UploadFile = File(...),
    supplier_gstin: str = Form(...),
):
    invoice_id = f"inv_{uuid.uuid4().hex[:8]}"
    gstin = supplier_gstin.strip().upper()
    log_invoice(logger, invoice_id, f"Received upload request for supplier {gstin}")

    # 1. Validate file extension
    allowed_exts = CONFIG["upload"]["allowed_extensions"]
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_exts:
        log_invoice(logger, invoice_id, f"Rejected invalid file type: {ext}", "warning")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed types: {', '.join(allowed_exts)}",
        )

    # 2. Validate file size (max 5 MB)
    max_bytes = CONFIG["upload"]["max_file_size_mb"] * 1024 * 1024
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > max_bytes:
        log_invoice(logger, invoice_id, f"Rejected file size: {len(content)} bytes", "warning")
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size of {CONFIG['upload']['max_file_size_mb']} MB.",
        )

    blob_name = f"{invoice_id}{ext}"
    raw_container = CONFIG["storage"]["raw_container"]

    # 3. Upload raw file to Blob Storage
    blob_service = get_blob_service_client()
    blob_client = blob_service.get_blob_client(container=raw_container, blob=blob_name)
    blob_client.upload_blob(content, overwrite=True)
    log_invoice(logger, invoice_id, f"Uploaded raw file to Blob container '{raw_container}' as '{blob_name}'")

    # 4. Save initial RECEIVED record in Cosmos DB
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        "id": invoice_id,
        "invoice_id": invoice_id,
        "supplier_gstin": gstin,
        "original_filename": file.filename,
        "blob_name": blob_name,
        "status": "RECEIVED",
        "extracted_data": None,
        "reasons": [],
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    container = get_cosmos_container()
    container.upsert_item(record)

    # 5. Send message to Azure Storage Queue
    queue_client = get_queue_client()
    message_payload = {
        "invoice_id": invoice_id,
        "blob_name": blob_name,
        "supplier_gstin": gstin,
        "original_filename": file.filename,
    }
    queue_client.send_message(json.dumps(message_payload))
    log_invoice(logger, invoice_id, "Enqueued invoice for worker processing")

    return {"invoice_id": invoice_id, "status": "RECEIVED"}


@app.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str):
    log_invoice(logger, invoice_id, "Fetching invoice status and details")
    container = get_cosmos_container()
    query = "SELECT * FROM c WHERE c.id = @id"
    items = list(
        container.query_items(
            query=query,
            parameters=[{"name": "@id", "value": invoice_id}],
            enable_cross_partition_query=True,
        )
    )

    if not items:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found.")

    item = items[0]
    sas_url = generate_read_only_sas_url(item["blob_name"])

    return {
        "invoice_id": item["invoice_id"],
        "supplier_gstin": item["supplier_gstin"],
        "status": item["status"],
        "extracted_data": item.get("extracted_data"),
        "reasons": item.get("reasons", []),
        "document_url": sas_url,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


@app.get("/invoices")
def list_invoices(
    status: Optional[str] = Query(default=None),
    supplier: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
):
    container = get_cosmos_container()
    conditions = []
    params = []

    if status:
        conditions.append("c.status = @status")
        params.append({"name": "@status", "value": status.strip().upper()})
    if supplier:
        conditions.append("c.supplier_gstin = @supplier")
        params.append({"name": "@supplier", "value": supplier.strip().upper()})

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM c{where_clause} ORDER BY c.created_at DESC"

    all_items = list(
        container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        )
    )

    total = len(all_items)
    start = (page - 1) * size
    end = start + size
    paged_items = all_items[start:end]

    results = [
        {
            "invoice_id": item["invoice_id"],
            "supplier_gstin": item["supplier_gstin"],
            "status": item["status"],
            "reasons": item.get("reasons", []),
            "created_at": item.get("created_at"),
        }
        for item in paged_items
    ]

    return {
        "page": page,
        "size": size,
        "total": total,
        "items": results,
    }