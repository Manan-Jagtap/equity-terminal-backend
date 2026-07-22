"""
app/r2/client.py — Cloudflare R2 client (S3-compatible via boto3).

Env vars required:
  R2_ACCOUNT_ID
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_BUCKET

Key-naming convention used by callers:
  companies/{ticker}/{quarter}/{doc_type}.pdf
e.g. companies/MUTHOOTFIN/Q4FY26/IP.pdf
"""
from __future__ import annotations
import os
import logging
from functools import lru_cache
from typing import Optional

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

R2_BUCKET = os.environ.get("R2_BUCKET", "equity-terminal-docs")


@lru_cache(maxsize=1)
def get_r2_client():
    """Singleton boto3 S3 client pointed at R2."""
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    if not all([account_id, access_key, secret_key]):
        missing = [k for k, v in {
            "R2_ACCOUNT_ID": account_id,
            "R2_ACCESS_KEY_ID": access_key,
            "R2_SECRET_ACCESS_KEY": secret_key,
        }.items() if not v]
        raise RuntimeError(f"R2 env vars missing: {missing}")

    # OPS-05: allow an explicit endpoint override so the storage backend can move
    # to S3 (or another S3-compatible host) by setting R2_ENDPOINT — the
    # MIGRATION_AWS runbook claimed this was possible but the URL was hard-derived
    # from the account id, so that step wasn't executable. Default unchanged.
    endpoint = os.getenv("R2_ENDPOINT") or f"https://{account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def upload_bytes(key: str, data: bytes, content_type: str = "application/pdf") -> str:
    """Upload bytes to R2 under `key`. Returns the key."""
    client = get_r2_client()
    client.put_object(
        Bucket=R2_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    log.info("r2 upload: %s (%d bytes)", key, len(data))
    return key


def upload_file(key: str, local_path: str, content_type: str = "application/pdf") -> str:
    """Upload a local file to R2."""
    with open(local_path, "rb") as f:
        return upload_bytes(key, f.read(), content_type=content_type)


def download_bytes(key: str) -> Optional[bytes]:
    """Download bytes from R2. Returns None if key doesn't exist."""
    client = get_r2_client()
    try:
        resp = client.get_object(Bucket=R2_BUCKET, Key=key)
        return resp["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


def object_exists(key: str) -> bool:
    """Check if an object exists in R2 without downloading it."""
    client = get_r2_client()
    try:
        client.head_object(Bucket=R2_BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def delete_object(key: str) -> None:
    """Delete an object from R2."""
    client = get_r2_client()
    client.delete_object(Bucket=R2_BUCKET, Key=key)
    log.info("r2 delete: %s", key)


def list_objects(prefix: str = "", max_keys: int = 1000) -> list[dict]:
    """List objects under a prefix. Returns list of {Key, Size, LastModified}."""
    client = get_r2_client()
    resp = client.list_objects_v2(Bucket=R2_BUCKET, Prefix=prefix, MaxKeys=max_keys)
    return [
        {"key": o["Key"], "size": o["Size"], "modified": o["LastModified"]}
        for o in resp.get("Contents", [])
    ]


# --- key builders -----------------------------------------------------------

def build_doc_key(ticker: str, quarter: str, doc_type: str) -> str:
    """Standard key pattern for quarterly documents."""
    return f"companies/{ticker.upper()}/{quarter.upper()}/{doc_type.upper()}.pdf"
