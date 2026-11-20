import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Tuple, List

import fitz  # PyMuPDF
import pytesseract
import requests
from PIL import Image
from pydantic import ValidationError

from common.azure_clients import (
    get_blob_service_client,
    get_cosmos_container,
    get_queue_client,
)
from common.config import CONFIG
from common.logger import get_logger, log_invoice
from common.schemas import InvoiceData

logger = get_logger("worker")


def extract_text_from_bytes(file_bytes: bytes, filename: str) -> str:
    """Extract text from PDF or image using PyMuPDF + Tesseract OCR."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text_parts = []
        for page in doc:
            page_text = page.get_text("text").strip()
            if page_text:
                text_parts.append(page_text)
            else:
                # Fallback to Tesseract OCR for scanned PDF pages
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text_parts.append(pytesseract.image_to_string(img))
        return "\n".join(text_parts)
    else:
        img = Image.open(io.BytesIO(file_bytes))
        return pytesseract.image_to_string(img)


def parse_invoice_fields(raw_text: str) -> Tuple[Dict[str, Any], List[str]]:
    """Parse structured invoice fields from OCR text without guessing missing values."""
    reasons: List[str] = []

    def find_field(pattern: str, field_label: str):
        match = re.search(pattern, raw_text, re.IGNORECASE | re.MULTILINE)
        if not match:
            reasons.append(f"Could not extract {field_label} from document")
            return None
        return match.group(1).strip()

    invoice_number = find_field(r"Invoice\s*(?:No|Number|#)\s*[:\-]\s*([A-Z0-9\-\/]+)", "invoice_number")
    invoice_date = find_field(r"Invoice\s*Date\s*[:\-]\s*(\d{4}-\d{2}-\d{2})", "invoice_date")
    supplier_name = find_field(r"Supplier\s*Name\s*[:\-]\s*(.+)", "supplier_name")
    supplier_gstin = find_field(r"(?:Supplier\s*)?GSTIN\s*[:\-]\s*([A-Z0-9]{15})", "supplier_gstin")

    subtotal_str = find_field(r"Subtotal\s*[:\-]\s*(?:INR|Rs\.?|₹)?\s*([\d,]+\.?\d*)", "subtotal")
    tax_str = find_field(r"Tax\s*[:\-]\s*(?:INR|Rs\.?|₹)?\s*([\d,]+\.?\d*)", "tax")
    total_str = find_field(r"\bTotal\s*[:\-]\s*(?:INR|Rs\.?|₹)?\s*([\d,]+\.?\d*)", "total")

    # Parse line items formatted as: ITEM | <desc> | <qty> | <unit_price> | <amount>
    line_items = []
    item_pattern = re.compile(
        r"ITEM\s*\|\s*(.+?)\s*\|\s*([\d\.]+)\s*\|\s*([\d\.]+)\s*\|\s*([\d\.]+)",
        re.IGNORECASE,
    )
    for m in item_pattern.finditer(raw_text):
        line_items.append({
            "description": m.group(1).strip(),
            "quantity": float(m.group(2)),
            "unit_price": float(m.group(3)),
            "amount": float(m.group(4)),
        })

    if not line_items:
        reasons.append("Could not extract line_items from document")

    def to_float(val):
        if val is None:
            return None
        return float(val.replace(",", ""))

    extracted = {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "supplier": {
            "name": supplier_name,
            "gstin": supplier_gstin,
        },
        "line_items": line_items,
        "subtotal": to_float(subtotal_str),
        "tax": to_float(tax_str),
        "total": to_float(total_str),
    }
    return extracted, reasons


def check_supplier_master(gstin: str, invoice_id: str) -> Tuple[str, Dict[str, Any], str]:
    """
    Returns (outcome, supplier_data, reason)
    outcome is one of: 'OK', 'REJECTED', 'PENDING_SUPPLIER_CHECK'
    """
    base_url = CONFIG["supplier_service"]["base_url"]
    timeout = CONFIG["supplier_service"]["timeout_seconds"]
    url = f"{base_url}/suppliers/{gstin}"

    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 404:
            return "REJECTED", {}, f"Supplier GSTIN {gstin} is unknown in Supplier Master"
        if resp.status_code != 200:
            return "PENDING_SUPPLIER_CHECK", {}, f"Supplier Master returned HTTP {resp.status_code}"

        data = resp.json()
        if not data.get("active", False):
            return "REJECTED", data, f"Supplier '{data.get('name')}' ({gstin}) is inactive"
        return "OK", data, ""

    except requests.RequestException as exc:
        log_invoice(logger, invoice_id, f"Supplier service unreachable: {exc}", "warning")
        return "PENDING_SUPPLIER_CHECK", {}, "Supplier Master service unavailable; pending verification"


def process_message_payload(payload: Dict[str, Any]):
    invoice_id = payload["invoice_id"]
    blob_name = payload["blob_name"]
    supplier_gstin = payload["supplier_gstin"]
    raw_container = CONFIG["storage"]["raw_container"]

    log_invoice(logger, invoice_id, f"Starting processing for blob '{blob_name}'")

    # 1. Download file from Blob Storage
    blob_service = get_blob_service_client()
    blob_client = blob_service.get_blob_client(container=raw_container, blob=blob_name)
    file_bytes = blob_client.download_blob().readall()

    # 2. Extract text & parse fields
    raw_text = extract_text_from_bytes(file_bytes, blob_name)
    extracted, reasons = parse_invoice_fields(raw_text)
    log_invoice(logger, invoice_id, "OCR extraction completed")

    # 3. Validate with Pydantic v2 schema
    validated_data = None
    if not reasons:
        try:
            validated_obj = InvoiceData(**extracted)
            validated_data = validated_obj.model_dump(mode="json")
        except ValidationError as ve:
            for err in ve.errors():
                loc = ".".join(str(x) for x in err["loc"])
                reasons.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    else:
        # Still run Pydantic if possible to surface rule errors alongside missing fields
        validated_data = extracted

    # 4. Verify supplier via Flask microservice
    gstin_to_check = (extracted.get("supplier", {}).get("gstin") or supplier_gstin).upper()
    supplier_outcome, supplier_data, supplier_reason = check_supplier_master(gstin_to_check, invoice_id)

    if supplier_data and isinstance(validated_data, dict):
        validated_data.setdefault("supplier", {})
        validated_data["supplier"]["city"] = supplier_data.get("city")
        validated_data["supplier"]["active"] = supplier_data.get("active")

    # 5. Determine final status
    if supplier_outcome == "REJECTED":
        status = "REJECTED"
        reasons.append(supplier_reason)
    elif supplier_outcome == "PENDING_SUPPLIER_CHECK":
        status = "PENDING_SUPPLIER_CHECK"
        reasons.append(supplier_reason)
    elif reasons:
        status = "NEEDS_REVIEW"
    else:
        status = "APPROVED"

    # 6. Upsert into Cosmos DB (partition key: /supplier_gstin)
    container = get_cosmos_container()
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        "id": invoice_id,
        "invoice_id": invoice_id,
        "supplier_gstin": supplier_gstin,
        "original_filename": payload.get("original_filename", blob_name),
        "blob_name": blob_name,
        "status": status,
        "extracted_data": validated_data,
        "reasons": reasons,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    container.upsert_item(record)
    log_invoice(logger, invoice_id, f"Saved to Cosmos DB with status={status}, reasons={reasons}")


def mark_invoice_failed(payload: Dict[str, Any], error_msg: str):
    invoice_id = payload.get("invoice_id", "unknown")
    supplier_gstin = payload.get("supplier_gstin", "UNKNOWN")
    container = get_cosmos_container()
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {
        "id": invoice_id,
        "invoice_id": invoice_id,
        "supplier_gstin": supplier_gstin,
        "original_filename": payload.get("original_filename", ""),
        "blob_name": payload.get("blob_name", ""),
        "status": "FAILED",
        "extracted_data": None,
        "reasons": [f"Processing failed after max retries: {error_msg}"],
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    container.upsert_item(record)
    log_invoice(logger, invoice_id, f"Marked FAILED after max retries: {error_msg}", "error")


def run_worker():
    queue_client = get_queue_client()
    poll_interval = CONFIG["worker"]["poll_interval_seconds"]
    max_retries = CONFIG["worker"]["max_retries"]
    logger.info("Invoice processing worker started. Polling queue...")

    while True:
        messages = queue_client.receive_messages(messages_per_page=5, visibility_timeout=30)
        found = False
        for msg in messages:
            found = True
            payload = json.loads(msg.content)
            invoice_id = payload.get("invoice_id", "unknown")
            try:
                process_message_payload(payload)
                queue_client.delete_message(msg)
                log_invoice(logger, invoice_id, "Deleted message from queue")
            except Exception as exc:
                log_invoice(
                    logger,
                    invoice_id,
                    f"Error processing message (attempt {msg.dequeue_count}/{max_retries}): {exc}",
                    "error",
                )
                if msg.dequeue_count >= max_retries:
                    mark_invoice_failed(payload, str(exc))
                    queue_client.delete_message(msg)

        if not found:
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_worker()