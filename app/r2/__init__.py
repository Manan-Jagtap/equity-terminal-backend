"""R2 storage client. Thin boto3 wrapper around Cloudflare R2's S3-compatible API."""
from .client import (
    get_r2_client,
    upload_bytes,
    upload_file,
    download_bytes,
    object_exists,
    delete_object,
    list_objects,
    R2_BUCKET,
    build_doc_key,
)

__all__ = [
    "get_r2_client",
    "upload_bytes",
    "upload_file",
    "download_bytes",
    "object_exists",
    "delete_object",
    "list_objects",
    "R2_BUCKET",
    "build_doc_key",
]
