# CartNova Smart Invoice Intake & Validation Pipeline

An automated invoice intake, OCR extraction, validation, and reporting pipeline built with **FastAPI**, **Flask**, **Pydantic v2**, **Azure Blob Storage & Storage Queue (Azurite)**, **PyMuPDF / Tesseract OCR**, and **Azure Cosmos DB**.

---

## Project Structure

```text
Milestone1_Assessment/
├── common/
│   ├── azure_clients.py     # Blob, Queue, Cosmos DB clients & 15-min SAS generator
│   ├── config.py            # YAML config & environment variable loader
│   ├── logger.py            # Structured logging with invoice_id
│   └── schemas.py           # Pydantic v2 nested schemas & validation rules
├── intake_service/
│   └── main.py              # FastAPI service (POST /invoices, GET /invoices/{id}, GET /invoices)
├── supplier_service/
│   ├── app.py               # Flask Supplier Master microservice (GET /suppliers/<gstin>)
│   └── suppliers.json       # Seed data (5 suppliers, active & inactive)
├── worker/
│   └── processor.py         # Queue worker: OCR, Pydantic validation, supplier check, Cosmos upsert
├── tests/
│   └── test_pipeline.py     # 11 mocked pytest unit & API tests
├── sample_invoices/         # 3 real sample PDF invoices (clean, mismatch, unknown supplier)
├── evidence/                # Screenshots of Swagger, SAS link, daily report & pytest output
├── config.yaml              # Non-secret configuration (containers, queue, limits, URLs)
├── daily_report.py          # Daily summary report script (one-time & scheduled mode)
├── generate_samples.py      # Script used to generate the 3 sample PDF invoices
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template (no secrets)
└── README.md
```

---

## 1. Installation & Setup

1. Create and activate a virtual environment:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Create your local `.env` file from the template:
   ```powershell
   copy .env.example .env
   ```
4. Populate `.env` with your local emulator values (or Azure cloud credentials):
   ```env
   AZURE_STORAGE_CONNECTION_STRING=<your-azurite-or-azure-storage-connection-string>
   COSMOS_ENDPOINT=<your-cosmos-db-endpoint>
   COSMOS_KEY=<your-cosmos-db-key>
   DOC_INTELLIGENCE_ENDPOINT=<optional-doc-intelligence-endpoint>
   DOC_INTELLIGENCE_KEY=<optional-doc-intelligence-key>
   ```

---

## 2. Start Local Emulators (Docker)

Run **Azurite** (Blob & Queue Storage) and the **Azure Cosmos DB Emulator** locally via Docker:

```powershell
docker run -d --name azurite -p 10000:10000 -p 10001:10001 -p 10002:10002 [mcr.microsoft.com/azure-storage/azurite](https://mcr.microsoft.com/azure-storage/azurite)

docker run -d --name cosmos-emulator -p 8081:8081 -p 1234:1234 [mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview](https://mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview)
```

> **Note:** On startup, the FastAPI intake service automatically creates the `invoices-raw` and `reports` Blob containers, the `invoice-processing-queue` Storage Queue, and the `cartnova` Cosmos DB database with the `invoices` container (partition key `/supplier_gstin`).

---

## 3. Start the Services & Worker

Open **three separate terminals** (with `venv` activated in each):

1. **Terminal 1 — Flask Supplier Master Microservice (Port 5001):**
   ```powershell
   python supplier_service/app.py
   ```
2. **Terminal 2 — FastAPI Intake Service (Port 8000):**
   ```powershell
   uvicorn intake_service.main:app --host 127.0.0.1 --port 8000 --reload
   ```
   - Swagger UI: `http://127.0.0.1:8000/docs`
3. **Terminal 3 — Queue Processing Worker:**
   ```powershell
   python -m worker.processor
   ```

---

## 4. Sample Invoices, Daily Report & Automated Tests

- **Generate sample invoices** (already included in `sample_invoices/`):
  ```powershell
  python generate_samples.py
  ```
- **Run the daily summary report** (uploads `daily_summary_YYYY-MM-DD.json` to the `reports` Blob container):
  ```powershell
  python daily_report.py
  ```
  To run continuously on a daily schedule (23:55 UTC using the `schedule` library):
  ```powershell
  python daily_report.py --schedule
  ```
- **Run the automated test suite** (11 mocked tests; does not require live Azure or emulators):
  ```powershell
  pytest -v
  ```

---

## 5. Architecture Note

To run two copies of the stateless FastAPI intake service in production, deploy both instances behind an **Azure Application Gateway** (for Layer-7 HTTP routing, TLS termination, and `/health` probe checks) or an **Azure Load Balancer**; because raw files, queue messages, and invoice records are externalised to Azure Storage and Cosmos DB, either instance can safely handle any upload or lookup request. To eliminate static keys and connection strings from `.env`, assign a **System-Assigned Managed Identity** to the intake and worker compute hosts, grant least-privilege RBAC roles (`Storage Blob Data Contributor`, `Storage Queue Data Contributor`, and `Cosmos DB Built-in Data Contributor`), and authenticate in Python using `DefaultAzureCredential` from `azure-identity`. Choose **Azure Service Bus** over **Azure Storage Queue** when the workload requires strict FIFO ordering (sessions), duplicate detection, native dead-letter queues, pub/sub topic subscriptions, or message payloads larger than 64 KB; **Storage Queue** is preferable for simple, high-volume, low-cost decoupling like basic invoice intake.