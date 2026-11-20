from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUTPUT_DIR = Path(__file__).resolve().parent / "sample_invoices"
OUTPUT_DIR.mkdir(exist_ok=True)


def create_invoice_pdf(filename: str, lines: list[str]):
    filepath = OUTPUT_DIR / filename
    c = canvas.Canvas(str(filepath), pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 800, "TAX INVOICE")

    c.setFont("Helvetica", 11)
    y = 765
    for line in lines:
        c.drawString(50, y, line)
        y -= 22

    c.save()
    print(f"Created {filepath}")


if __name__ == "__main__":
    # 1. Clean valid invoice -> Expected: APPROVED
    create_invoice_pdf(
        "INV-1001-clean.pdf",
        [
            "Invoice No: INV-1001",
            "Invoice Date: 2026-11-20",
            "Supplier Name: Sri Lakshmi Traders",
            "Supplier GSTIN: 29ABCDE1234F1Z5",
            "------------------------------------------------------------",
            "ITEM | Barcode Scanner | 5 | 1200.00 | 6000.00",
            "ITEM | Thermal Paper Rolls | 20 | 100.00 | 2000.00",
            "------------------------------------------------------------",
            "Subtotal: 8000.00",
            "Tax: 1440.00",
            "Total: 9440.00",
        ],
    )

    # 2. Totals mismatch invoice -> Expected: NEEDS_REVIEW
    create_invoice_pdf(
        "INV-1002-mismatch.pdf",
        [
            "Invoice No: INV-1002",
            "Invoice Date: 2026-11-20",
            "Supplier Name: Deccan Electronics Pvt Ltd",
            "Supplier GSTIN: 27AABCU9603R1ZM",
            "------------------------------------------------------------",
            "ITEM | Packing Tape Carton | 10 | 450.00 | 4500.00",
            "------------------------------------------------------------",
            "Subtotal: 4500.00",
            "Tax: 810.00",
            "Total: 6000.00",
        ],
    )

    # 3. Unknown supplier invoice -> Expected: REJECTED
    create_invoice_pdf(
        "INV-1003-unknown.pdf",
        [
            "Invoice No: INV-1003",
            "Invoice Date: 2026-11-20",
            "Supplier Name: Phantom Supplies Co",
            "Supplier GSTIN: 29ZZZZZ9999Z1Z9",
            "------------------------------------------------------------",
            "ITEM | Shipping Labels | 15 | 200.00 | 3000.00",
            "------------------------------------------------------------",
            "Subtotal: 3000.00",
            "Tax: 540.00",
            "Total: 3540.00",
        ],
    )