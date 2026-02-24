"""File upload endpoint — returns base64 for vision use."""
from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, File, UploadFile

router = APIRouter(tags=["files"])


@router.post("/chat/upload")
async def upload_file(file: UploadFile = File(...), session_id: str = "") -> dict:
    """Accept an image upload and return its base64 encoding for inline vision."""
    content = await file.read()
    file_id = str(uuid.uuid4())
    b64 = base64.b64encode(content).decode()
    return {
        "file_id": file_id,
        "mime_type": file.content_type or "image/jpeg",
        "data_b64": b64,
    }
