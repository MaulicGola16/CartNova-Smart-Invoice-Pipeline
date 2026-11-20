from datetime import datetime, timedelta, timezone
import urllib3
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.storage.queue import QueueServiceClient
from azure.cosmos import CosmosClient, PartitionKey

from common.config import (
    CONFIG,
    AZURE_STORAGE_CONNECTION_STRING,
    COSMOS_ENDPOINT,
    COSMOS_KEY,
)

# Suppress self-signed cert warnings for local Cosmos DB Emulator
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_blob_service_client() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)


def get_queue_client():
    queue_name = CONFIG["storage"]["queue_name"]
    q_service = QueueServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    q_client = q_service.get_queue_client(queue_name)
    try:
        q_client.create_queue()
    except Exception:
        pass
    return q_client


def get_cosmos_container():
    is_local = "localhost" in COSMOS_ENDPOINT or "127.0.0.1" in COSMOS_ENDPOINT
    client = CosmosClient(
        COSMOS_ENDPOINT,
        credential=COSMOS_KEY,
        connection_verify=not is_local,
    )
    db_name = CONFIG["cosmos"]["database_name"]
    container_name = CONFIG["cosmos"]["container_name"]
    partition_key = CONFIG["cosmos"]["partition_key"]

    database = client.create_database_if_not_exists(id=db_name)
    container = database.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path=partition_key),
    )
    return container


def init_storage_resources():
    blob_service = get_blob_service_client()
    for key in ("raw_container", "reports_container"):
        container_name = CONFIG["storage"][key]
        try:
            blob_service.create_container(container_name)
        except Exception:
            pass
    get_queue_client()
    get_cosmos_container()


def generate_read_only_sas_url(blob_name: str, container_name: str = None) -> str:
    container_name = container_name or CONFIG["storage"]["raw_container"]
    expiry_minutes = CONFIG["storage"]["sas_expiry_minutes"]

    blob_service = get_blob_service_client()
    account_name = blob_service.account_name
    account_key = blob_service.credential.account_key

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
    )
    blob_client = blob_service.get_blob_client(container=container_name, blob=blob_name)
    return f"{blob_client.url}?{sas_token}"