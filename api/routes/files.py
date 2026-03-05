"""File upload endpoint — returns base64 for vision use."""
from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter(tags=["files"])

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/chat/upload")
async def upload_file(file: UploadFile = File(...), session_id: str = "") -> dict:
    """Accept an image upload and return its base64 encoding for inline vision."""
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    file_id = str(uuid.uuid4())
    b64 = base64.b64encode(content).decode()
    return {
        "file_id": file_id,
        "mime_type": file.content_type or "image/jpeg",
        "data_b64": b64,
    }
