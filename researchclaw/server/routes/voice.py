"""Voice upload / transcription API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

MAX_AUDIO_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


def _content_length(file: UploadFile) -> int | None:
    raw_length = getattr(file, "headers", {}).get("content-length")
    if raw_length is None:
        return None
    try:
        return int(raw_length)
    except (TypeError, ValueError):
        return None


async def _read_limited_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        read_size = min(UPLOAD_READ_CHUNK_BYTES, MAX_AUDIO_UPLOAD_BYTES - total + 1)
        chunk = await file.read(read_size)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_AUDIO_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Audio upload is too large")
        chunks.append(chunk)


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = "zh",
) -> dict[str, Any]:
    """Transcribe uploaded audio using Whisper API."""
    try:
        from researchclaw.voice.transcriber import VoiceTranscriber
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Voice dependencies not installed. Run: pip install researchclaw[voice]",
        )

    from researchclaw.server.app import _app_state

    config = _app_state.get("config")
    if not config or not config.server.voice_enabled:
        raise HTTPException(status_code=403, detail="Voice is not enabled in config")

    declared_size = _content_length(file)
    if declared_size is not None and declared_size > MAX_AUDIO_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Audio upload is too large")

    audio_bytes = await _read_limited_upload(file)

    transcriber = VoiceTranscriber(config.server)
    text = await transcriber.transcribe(audio_bytes, language=language)

    return {"text": text, "language": language}
