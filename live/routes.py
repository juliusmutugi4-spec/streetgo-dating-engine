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
def create_live(
    request: CreateLiveRequest,
):
    live_id = (
        f"live_{uuid4().hex[:12]}"
    )

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

    print(
        "STREETGO LIVE CREATED:",
        live_id,
        "HOST:",
        request.host_id,
        flush=True,
    )

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

        return {
            "success": True,
            "message": "Live session already live",
            "live": session,
        }

    if session.status == "ended":

        raise HTTPException(
            status_code=400,
            detail="Live session has already ended",
        )

    session.status = "live"

    session.started_at = (
        datetime.now(timezone.utc)
    )

    print(
        "STREETGO LIVE STARTED:",
        live_id,
        flush=True,
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

        return {
            "success": True,
            "message": "Live session is already stopped",
            "live": session,
        }

    session.status = "ended"

    session.ended_at = (
        datetime.now(timezone.utc)
    )

    # Reset viewer count.
    session.viewer_count = 0

    print(
        "STREETGO LIVE STOPPED:",
        live_id,
        flush=True,
    )

    return {
        "success": True,
        "message": "Live session ended",
        "live": session,
    }


# =========================================================
# LIVE WEBSOCKET
#
# Broadcaster:
# /live/{live_id}/ws?role=broadcaster
#
# Viewer:
# /live/{live_id}/ws?role=viewer
#
# IMPORTANT:
# Accept the WebSocket FIRST.
# Then validate the session.
#
# This prevents a missing/stale live_id from becoming
# an HTTP 403 handshake failure.
# =========================================================

@router.websocket(
    "/{live_id}/ws"
)
async def live_websocket(
    websocket: WebSocket,
    live_id: str,
):

    role = websocket.query_params.get(
        "role",
        "viewer",
    )

    if role not in {
        "broadcaster",
        "viewer",
    }:
        role = "viewer"

    print(
        "================================================",
        flush=True,
    )

    print(
        "STREETGO WS REQUEST:",
        flush=True,
    )

    print(
        "LIVE ID:",
        live_id,
        flush=True,
    )

    print(
        "ROLE:",
        role,
        flush=True,
    )

    print(
        "ORIGIN:",
        websocket.headers.get("origin"),
        flush=True,
    )

    print(
        "HOST:",
        websocket.headers.get("host"),
        flush=True,
    )

    print(
        "USER AGENT:",
        websocket.headers.get(
            "user-agent"
        ),
        flush=True,
    )

    print(
        "================================================",
        flush=True,
    )

    # =====================================================
    # ACCEPT HANDSHAKE FIRST
    # =====================================================

    try:

        await websocket.accept()

    except Exception as exc:

        print(
            "STREETGO WS ACCEPT ERROR:",
            repr(exc),
            flush=True,
        )

        return

    print(
        "STREETGO WS HANDSHAKE ACCEPTED:",
        live_id,
        role,
        flush=True,
    )

    # =====================================================
    # FIND SESSION AFTER ACCEPT
    # =====================================================

    session = live_sessions.get(
        live_id
    )

    if not session:

        print(
            "STREETGO WS SESSION NOT FOUND:",
            live_id,
            flush=True,
        )

        try:

            await websocket.send_json(
                {
                    "type":
                        "error",

                    "live_id":
                        live_id,

                    "message":
                        "Live session not found.",
                }
            )

        except Exception:
            pass

        try:

            await websocket.close(
                code=1008,
                reason=
                    "Live session not found",
            )

        except Exception:
            pass

        return

    # =====================================================
    # CONNECT MANAGER
    # =====================================================

    connected_to_manager = False

    try:

        await manager.connect(
            live_id,
            websocket,
            role,
        )

        connected_to_manager = True

        print(
            "STREETGO WS CONNECTED:",
            live_id,
            "ROLE:",
            role,
            flush=True,
        )

        # =================================================
        # UPDATE VIEWER COUNT
        # =================================================

        session.viewer_count = (
            manager.viewer_count(
                live_id
            )
        )

        print(
            "STREETGO VIEWER COUNT:",
            live_id,
            session.viewer_count,
            flush=True,
        )

        # =================================================
        # BROADCAST VIEWER COUNT
        # =================================================

        await manager.broadcast(
            live_id,
            {
                "type":
                    "viewer_count",

                "live_id":
                    live_id,

                "viewer_count":
                    session.viewer_count,
            },
        )

        # =================================================
        # TELL CURRENT CLIENT CONNECTED
        # =================================================

        await websocket.send_json(
            {
                "type":
                    "connected",

                "live_id":
                    live_id,

                "status":
                    session.status,

                "role":
                    role,

                "viewer_count":
                    session.viewer_count,
            }
        )

        # =================================================
        # MESSAGE LOOP
        # =================================================

        while True:

            message = (
                await websocket.receive_json()
            )

            if not isinstance(
                message,
                dict,
            ):
                continue

            print(
                "STREETGO WS MESSAGE:",
                live_id,
                role,
                flush=True,
            )

            await manager.broadcast(
                live_id,
                {
                    "type":
                        "message",

                    "live_id":
                        live_id,

                    "data":
                        message,
                },
            )

    # =====================================================
    # NORMAL DISCONNECT
    # =====================================================

    except WebSocketDisconnect:

        print(
            "STREETGO WS DISCONNECTED:",
            live_id,
            role,
            flush=True,
        )

    # =====================================================
    # UNEXPECTED ERROR
    # =====================================================

    except Exception as exc:

        print(
            "STREETGO WS ERROR:",
            repr(exc),
            flush=True,
        )

    # =====================================================
    # CLEANUP
    # =====================================================

    finally:

        if connected_to_manager:

            try:

                manager.disconnect(
                    live_id,
                    websocket,
                )

            except Exception as exc:

                print(
                    "STREETGO WS MANAGER DISCONNECT ERROR:",
                    repr(exc),
                    flush=True,
                )

        # The session may have been removed
        # while this connection was active.

        session = live_sessions.get(
            live_id
        )

        if session:

            try:

                session.viewer_count = (
                    manager.viewer_count(
                        live_id
                    )
                )

                print(
                    "STREETGO WS FINAL VIEWER COUNT:",
                    live_id,
                    session.viewer_count,
                    flush=True,
                )

                await manager.broadcast(
                    live_id,
                    {
                        "type":
                            "viewer_count",

                        "live_id":
                            live_id,

                        "viewer_count":
                            session.viewer_count,
                    },
                )

            except Exception as exc:

                print(
                    "STREETGO WS FINAL BROADCAST ERROR:",
                    repr(exc),
                    flush=True,
                )