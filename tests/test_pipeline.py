from datetime import date, timedelta
from unittest.mock import MagicMock, patch
import pytest
import requests
from fastapi.testclient import TestClient
from pydantic import ValidationError

from common.schemas import InvoiceData
from intake_service.main import app
from worker.processor import check_supplier_master

client = TestClient(app)


def valid_invoice_payload():
    return {
        "invoice_number": "INV-1001",
        "invoice_date": date.today().isoformat(),
        "supplier": {"name": "Sri Lakshmi Traders", "gstin": "29ABCDE1234F1Z5"},
        "line_items": [
            {"description": "Scanner", "quantity": 2, "unit_price": 1000.0, "amount": 2000.0}
        ],
        "subtotal": 2000.0,
        "tax": 360.0,
        "total": 2360.0,
    }


# 1. Pydantic: valid invoice passes
def test_pydantic_valid_invoice():
    inv = InvoiceData(**valid_invoice_payload())
    assert inv.total == 2360.0


# 2. Pydantic: invalid GSTIN fails
def test_pydantic_invalid_gstin():
    data = valid_invoice_payload()
    data["supplier"]["gstin"] = "INVALID123"
    with pytest.raises(ValidationError):
        InvoiceData(**data)


# 3. Pydantic: future date fails
def test_pydantic_future_date():
    data = valid_invoice_payload()
    data["invoice_date"] = (date.today() + timedelta(days=5)).isoformat()
    with pytest.raises(ValidationError):
        InvoiceData(**data)


# 4. Pydantic: totals mismatch (> ±1 rupee) fails
def test_pydantic_totals_mismatch():
    data = valid_invoice_payload()
    data["total"] = 2500.0
    with pytest.raises(ValidationError):
        InvoiceData(**data)


# 5. Pydantic: quantity <= 0 fails
def test_pydantic_negative_quantity():
    data = valid_invoice_payload()
    data["line_items"][0]["quantity"] = 0
    with pytest.raises(ValidationError):
        InvoiceData(**data)


# 6. Upload valid PDF returns 202 Accepted (mocked Azure)
@patch("intake_service.main.get_queue_client")
@patch("intake_service.main.get_cosmos_container")
@patch("intake_service.main.get_blob_service_client")
def test_upload_invoice_202(mock_blob, mock_cosmos, mock_queue):
    mock_blob.return_value.get_blob_client.return_value = MagicMock()
    mock_cosmos.return_value = MagicMock()
    mock_queue.return_value = MagicMock()

    resp = client.post(
        "/invoices",
        files={"file": ("test.pdf", b"%PDF-1.4 dummy content", "application/pdf")},
        data={"supplier_gstin": "29ABCDE1234F1Z5"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "RECEIVED"
    assert body["invoice_id"].startswith("inv_")


# 7. Upload unsupported file type returns 400
def test_upload_invalid_file_type():
    resp = client.post(
        "/invoices",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"supplier_gstin": "29ABCDE1234F1Z5"},
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


# 8. Upload missing required form field returns 422
def test_upload_missing_gstin_422():
    resp = client.post(
        "/invoices",
        files={"file": ("test.pdf", b"%PDF-1.4 dummy", "application/pdf")},
    )
    assert resp.status_code == 422


# 9. GET unknown invoice ID returns 404
@patch("intake_service.main.get_cosmos_container")
def test_get_invoice_not_found_404(mock_cosmos):
    mock_cosmos.return_value.query_items.return_value = []
    resp = client.get("/invoices/inv_doesnotexist")
    assert resp.status_code == 404


# 10. GET /invoices pagination works
@patch("intake_service.main.get_cosmos_container")
def test_list_invoices_pagination(mock_cosmos):
    mock_cosmos.return_value.query_items.return_value = [
        {"invoice_id": f"inv_{i}", "supplier_gstin": "29ABCDE1234F1Z5", "status": "APPROVED", "reasons": [], "created_at": "2026-11-20T00:00:00Z"}
        for i in range(5)
    ]
    resp = client.get("/invoices?status=APPROVED&page=2&size=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["page"] == 2
    assert len(body["items"]) == 2


# 11. Supplier service down -> PENDING_SUPPLIER_CHECK without crashing
@patch("worker.processor.requests.get", side_effect=requests.ConnectionError("Connection refused"))
def test_supplier_service_down(mock_get):
    outcome, data, reason = check_supplier_master("29ABCDE1234F1Z5", "inv_test1")
    assert outcome == "PENDING_SUPPLIER_CHECK"
    assert "unavailable" in reason.lower()