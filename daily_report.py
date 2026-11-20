import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone

import schedule

from common.azure_clients import get_blob_service_client, get_cosmos_container
from common.config import CONFIG
from common.logger import get_logger

logger = get_logger("daily_report")


def generate_daily_report() -> dict:
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info(f"Generating daily invoice summary for {today_str}")

    container = get_cosmos_container()
    query = "SELECT * FROM c WHERE STARTSWITH(c.created_at, @today)"
    items = list(
        container.query_items(
            query=query,
            parameters=[{"name": "@today", "value": today_str}],
            enable_cross_partition_query=True,
        )
    )

    status_counts = defaultdict(int)
    supplier_stats = defaultdict(lambda: {"invoice_count": 0, "approved_amount": 0.0})
    total_approved_amount = 0.0

    for item in items:
        status = item.get("status", "UNKNOWN")
        gstin = item.get("supplier_gstin", "UNKNOWN")
        status_counts[status] += 1
        supplier_stats[gstin]["invoice_count"] += 1

        if status == "APPROVED":
            extracted = item.get("extracted_data") or {}
            total = float(extracted.get("total") or 0.0)
            total_approved_amount += total
            supplier_stats[gstin]["approved_amount"] += total

    top_suppliers = sorted(
        [
            {
                "supplier_gstin": gstin,
                "invoice_count": stats["invoice_count"],
                "approved_amount": round(stats["approved_amount"], 2),
            }
            for gstin, stats in supplier_stats.items()
        ],
        key=lambda x: (x["approved_amount"], x["invoice_count"]),
        reverse=True,
    )[:5]

    report = {
        "report_date": today_str,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_invoices": len(items),
        "count_per_status": dict(status_counts),
        "total_approved_amount": round(total_approved_amount, 2),
        "top_suppliers": top_suppliers,
    }

    reports_container = CONFIG["storage"]["reports_container"]
    blob_name = f"daily_summary_{today_str}.json"
    blob_service = get_blob_service_client()
    blob_client = blob_service.get_blob_client(container=reports_container, blob=blob_name)
    blob_client.upload_blob(json.dumps(report, indent=2), overwrite=True)

    logger.info(f"Uploaded report '{blob_name}' to Blob container '{reports_container}'")
    print(json.dumps(report, indent=2))
    return report


def run_scheduler():
    # Scheduled to run daily at 23:55 UTC (can also be triggered via cron / Windows Task Scheduler)
    schedule.every().day.at("23:55").do(generate_daily_report)
    logger.info("Daily report scheduler started (runs daily at 23:55 UTC). Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CartNova Daily Invoice Summary Report")
    parser.add_argument("--schedule", action="store_true", help="Run continuously on a daily schedule")
    args = parser.parse_args()

    if args.schedule:
        run_scheduler()
    else:
        generate_daily_report()