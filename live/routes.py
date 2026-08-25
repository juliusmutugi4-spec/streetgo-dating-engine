from datetime import datetime, timezone
from uuid import uuid4

from fastapi import (
    APIRouter,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)

from pydantic import BaseModel

from .websocket import manager
from .models import LiveSession


router = APIRouter(
    prefix="/live",
    tags=["Live Coverage"],
)


# =========================================================
# TEMPORARY LIVE SESSION STORAGE
# =========================================================

live_sessions: dict[str, LiveSession] = {}


# =========================================================
# REQUEST MODEL
# =========================================================

class CreateLiveRequest(BaseModel):
    title: str
    description: str | None = None

    host_id: str
    host_name: str

    location: str | None = None


# =========================================================
# CREATE LIVE SESSION
# =========================================================

@router.post("/create")
def create_live(request: CreateLiveRequest):

    live_id = f"live_{uuid4().hex[:12]}"

    session = LiveSession(
        live_id=live_id,
        title=request.title,
        description=request.description,
        host_id=request.host_id,
        host_name=request.host_name,
        location=request.location,
        status="created",
    )

    live_sessions[live_id] = session

    return {
        "success": True,
        "message": "Live session created",
        "live": session,
    }


# =========================================================
# GET ALL LIVE SESSIONS
# =========================================================

@router.get("")
def get_live_sessions():

    sessions = list(
        live_sessions.values()
    )

    return {
        "success": True,
        "count": len(sessions),
        "live": sessions,
    }


# =========================================================
# GET ONE LIVE SESSION
# =========================================================

@router.get("/{live_id}")
def get_live_session(
    live_id: str,
):

    session = live_sessions.get(
        live_id
    )

    if not session:

        raise HTTPException(
            status_code=404,
            detail="Live session not found",
        )

    return {
        "success": True,
        "live": session,
    }


# =========================================================
# START LIVE SESSION
# =========================================================

@router.post("/{live_id}/start")
def start_live(
    live_id: str,
):

    session = live_sessions.get(
        live_id
    )

    if not session:

        raise HTTPException(
            status_code=404,
            detail="Live session not found",
        )

    if session.status == "live":

        raise HTTPException(
            status_code=400,
            detail="Live session is already live",
        )

    if session.status == "ended":

        raise HTTPException(
            status_code=400,
            detail="Live session has already ended",
        )

    session.status = "live"

    session.started_at = (
        datetime.now(timezone.utc)
    )

    return {
        "success": True,
        "message": "Live session started",
        "live": session,
    }


# =========================================================
# STOP LIVE SESSION
# =========================================================

@router.post("/{live_id}/stop")
def stop_live(
    live_id: str,
):

    session = live_sessions.get(
        live_id
    )

    if not session:

        raise HTTPException(
            status_code=404,
            detail="Live session not found",
        )

    if session.status != "live":

        raise HTTPException(
            status_code=400,
            detail="Live session is not currently live",
        )

    session.status = "ended"

    session.ended_at = (
        datetime.now(timezone.utc)
    )

    return {
        "success": True,
        "message": "Live session ended",
        "live": session,
    }


# =========================================================
# LIVE WEBSOCKET
#
# IMPORTANT:
#
# Broadcaster:
# /live/{live_id}/ws?role=broadcaster
#
# Viewer:
# /live/{live_id}/ws?role=viewer
#
# The broadcaster is NOT counted as a viewer.
# =========================================================

@router.websocket("/{live_id}/ws")
async def live_websocket(
    websocket: WebSocket,
    live_id: str,
):

    # =====================================================
    # FIND LIVE SESSION
    # =====================================================

    session = live_sessions.get(
        live_id
    )

    if not session:

        await websocket.close(
            code=1008
        )

        return

    # =====================================================
    # GET CONNECTION ROLE
    # =====================================================

    role = websocket.query_params.get(
        "role",
        "viewer",
    )

    # =====================================================
    # VALIDATE ROLE
    # =====================================================

    if role not in {
        "broadcaster",
        "viewer",
    }:

        role = "viewer"

    # =====================================================
    # CONNECT
    # =====================================================

    await manager.connect(
        live_id,
        websocket,
        role,
    )

    # =====================================================
    # UPDATE VIEWER COUNT
    #
    # Broadcaster is excluded automatically.
    # =====================================================

    session.viewer_count = (
        manager.viewer_count(
            live_id
        )
    )

    # =====================================================
    # BROADCAST NEW VIEWER COUNT
    # =====================================================

    await manager.broadcast(
        live_id,
        {
            "type": "viewer_count",
            "live_id": live_id,
            "viewer_count": (
                session.viewer_count
            ),
        },
    )

    try:

        # =================================================
        # TELL CLIENT IT IS CONNECTED
        # =================================================

        await websocket.send_json(
            {
                "type": "connected",
                "live_id": live_id,
                "status": session.status,
                "role": role,
                "viewer_count": (
                    session.viewer_count
                ),
            }
        )

        # =================================================
        # KEEP SOCKET ALIVE
        # =================================================

        while True:

            message = (
                await websocket.receive_json()
            )

            await manager.broadcast(
                live_id,
                {
                    "type": "message",
                    "live_id": live_id,
                    "data": message,
                },
            )

    # =====================================================
    # NORMAL DISCONNECT
    # =====================================================

    except WebSocketDisconnect:

        manager.disconnect(
            live_id,
            websocket,
        )

        # -------------------------------------------------
        # Recalculate viewers
        # -------------------------------------------------

        session.viewer_count = (
            manager.viewer_count(
                live_id
            )
        )

        # -------------------------------------------------
        # Notify remaining connections
        # -------------------------------------------------

        await manager.broadcast(
            live_id,
            {
                "type": "viewer_count",
                "live_id": live_id,
                "viewer_count": (
                    session.viewer_count
                ),
            },
        )

    # =====================================================
    # UNEXPECTED ERROR
    # =====================================================

    except Exception as e:

        print(
            "STREETGO WEBSOCKET ERROR:",
            e,
        )

        manager.disconnect(
            live_id,
            websocket,
        )

        session.viewer_count = (
            manager.viewer_count(
                live_id
            )
        )

        await manager.broadcast(
            live_id,
            {
                "type": "viewer_count",
                "live_id": live_id,
                "viewer_count": (
                    session.viewer_count
                ),
            },
        )