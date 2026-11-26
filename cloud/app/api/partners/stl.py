"""Silicon Wireless STL040 public URL-push endpoints (Port 80 / HTTPS).

Configure the device host URL (SMS ``*555#001#…#80#``) to one of:
  - ``connector.brixigo.com/attendance?``
  - ``connector.brixigo.com/attservice?``
  - ``connector.brixigo.com/rfid/attendance.aspx?``
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.stl_attendance import (
    GET_ACK_OK,
    POST_ACK_ERR,
    POST_ACK_OK,
    ingest_get_query,
    ingest_post_json,
)

logger = logging.getLogger("brixigo.cloud.stl.api")

router = APIRouter(tags=["Cloud Missions / STL040"])


async def _handle_get(request: Request, db: Session) -> PlainTextResponse:
    raw = request.url.query or ""
    try:
        result = ingest_get_query(db, raw)
        db.commit()
        if result.error and result.error not in {"no_records"}:
            logger.warning(
                "STL GET ingest issue error=%s accepted=%s rejected=%s",
                result.error,
                result.accepted,
                result.rejected,
            )
    except Exception:
        db.rollback()
        logger.exception("STL GET ingest failed")
    # Manual requires this acknowledgement once data is received.
    return PlainTextResponse(GET_ACK_OK, media_type="text/plain; charset=utf-8")


async def _handle_post(request: Request, db: Session) -> JSONResponse:
    body: dict[str, Any] = {}
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            body = payload
    except Exception:
        body = {}
    ok = True
    try:
        result = ingest_post_json(db, body)
        db.commit()
        if result.error and result.error not in {"no_records"}:
            ok = False
            logger.warning(
                "STL POST ingest issue error=%s accepted=%s rejected=%s",
                result.error,
                result.accepted,
                result.rejected,
            )
    except Exception:
        db.rollback()
        ok = False
        logger.exception("STL POST ingest failed")
    # Manual: respond with DeviceResponse irrespective of validity.
    return JSONResponse(POST_ACK_OK if ok else POST_ACK_ERR)


@router.get("/attendance")
@router.get("/rfid/attendance.aspx")
@router.get("/api/stl/attendance")
async def stl_attendance_get(request: Request, db: Session = Depends(get_db)):
    return await _handle_get(request, db)


@router.post("/attservice")
@router.post("/attendance")
@router.post("/api/stl/attservice")
@router.post("/api/stl/attendance")
async def stl_attendance_post(request: Request, db: Session = Depends(get_db)):
    return await _handle_post(request, db)
