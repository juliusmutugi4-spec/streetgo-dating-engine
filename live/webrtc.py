import asyncio
import os

import httpx
from dotenv import load_dotenv

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceServer,
    RTCConfiguration,
)

from aiortc.contrib.media import MediaRelay

load_dotenv()
# =========================================================
# ROUTER
# =========================================================

router = APIRouter(
    prefix="/live/webrtc",
    tags=["Live WebRTC"],
)


# =========================================================
# MEDIA RELAY
# =========================================================

relay = MediaRelay()


# =========================================================
# ACTIVE PEER CONNECTIONS
#
# live_id -> {
#     "broadcaster": set[RTCPeerConnection],
#     "viewer": set[RTCPeerConnection],
# }
# =========================================================

peer_connections: dict[
    str,
    dict[str, set[RTCPeerConnection]],
] = {}


# =========================================================
# BROADCASTER MEDIA TRACKS
#
# live_id -> {
#     "video": MediaStreamTrack,
#     "audio": MediaStreamTrack,
# }
# =========================================================

broadcast_tracks: dict[
    str,
    dict[str, object],
] = {}


# =========================================================
# CLEANUP LOCKS
#
# Prevent multiple WebRTC/ICE cleanup callbacks from
# fighting over the same live session.
# =========================================================

cleanup_locks: dict[
    str,
    asyncio.Lock,
] = {}


# =========================================================
# CLEANED PEERS
#
# Prevent cleanup_peer() from executing twice for the
# same RTCPeerConnection.
# =========================================================

cleaned_peers: set[
    RTCPeerConnection
] = set()


# =========================================================
# WEBRTC OFFER MODEL
# =========================================================

class WebRTCOffer(BaseModel):

    live_id: str

    sdp: str

    type: str

    role: str


# =========================================================
# GET CLEANUP LOCK
# =========================================================

def get_cleanup_lock(
    live_id: str,
) -> asyncio.Lock:

    if live_id not in cleanup_locks:

        cleanup_locks[
            live_id
        ] = asyncio.Lock()

    return cleanup_locks[
        live_id
    ]


# =========================================================
# GET LIVE PEERS
# =========================================================

def get_live_peers(
    live_id: str,
):
    if live_id not in peer_connections:

        peer_connections[live_id] = {
            "broadcaster": set(),
            "viewer": set(),
        }

    else:

        peer_connections[
            live_id
        ].setdefault(
            "broadcaster",
            set(),
        )

        peer_connections[
            live_id
        ].setdefault(
            "viewer",
            set(),
        )

    return peer_connections[
        live_id
    ]


# =========================================================
# ACTIVE PEER COUNTS
# =========================================================

def get_active_peer_counts(
    live_id: str,
) -> tuple[int, int]:

    peers = peer_connections.get(
        live_id
    )

    if not peers:
        return 0, 0

    broadcaster_count = len(
        peers.get(
            "broadcaster",
            set(),
        )
    )

    viewer_count = len(
        peers.get(
            "viewer",
            set(),
        )
    )

    return (
        broadcaster_count,
        viewer_count,
    )


# =========================================================
# DEBUG PEER COUNTS
# =========================================================

def print_peer_counts(
    live_id: str,
    prefix: str = "STREETGO WEBRTC",
):

    broadcasters, viewers = (
        get_active_peer_counts(
            live_id
        )
    )

    print(
        f"{prefix} PEERS:",
        live_id,
        "BROADCASTERS:",
        broadcasters,
        "VIEWERS:",
        viewers,
    )


# =========================================================
# WAIT FOR BROADCASTER MEDIA
#
# Viewer can arrive slightly before the broadcaster
# tracks have been received by the backend.
# =========================================================

async def wait_for_broadcast_tracks(
    live_id: str,
    timeout: float = 15.0,
):

    print(
        "STREETGO WAITING FOR BROADCASTER TRACKS:",
        live_id,
    )

    started = (
        asyncio.get_running_loop().time()
    )

    while True:

        tracks = broadcast_tracks.get(
            live_id
        )

        # -------------------------------------------------
        # Video is the minimum requirement.
        # -------------------------------------------------

        if (
            tracks
            and tracks.get("video") is not None
        ):

            print(
                "STREETGO BROADCASTER VIDEO READY:",
                live_id,
            )

            return tracks

        # -------------------------------------------------
        # Timeout
        # -------------------------------------------------

        elapsed = (
            asyncio.get_running_loop().time()
            - started
        )

        if elapsed >= timeout:

            print(
                "STREETGO BROADCASTER TRACK WAIT TIMEOUT:",
                live_id,
            )

            return tracks or {}

        # -------------------------------------------------
        # Wait briefly
        # -------------------------------------------------

        await asyncio.sleep(
            0.1
        )


# =========================================================
# CLEANUP PEER
# =========================================================

async def cleanup_peer(
    live_id: str,
    role: str,
    pc: RTCPeerConnection,
):

    # -----------------------------------------------------
    # Prevent duplicate cleanup.
    # -----------------------------------------------------

    if pc in cleaned_peers:
        return

    cleaned_peers.add(pc)

    lock = get_cleanup_lock(
        live_id
    )

    async with lock:

        print(
            "========================================"
        )

        print(
            "STREETGO WEBRTC CLEANUP START"
        )

        print(
            "LIVE ID:",
            live_id,
        )

        print(
            "ROLE:",
            role,
        )

        # -------------------------------------------------
        # Remove peer from its role set.
        # -------------------------------------------------

        peers = peer_connections.get(
            live_id
        )

        if peers:

            role_peers = peers.get(
                role
            )

            if role_peers is not None:

                role_peers.discard(
                    pc
                )

        # -------------------------------------------------
        # Close the peer connection.
        # -------------------------------------------------

        try:

            if (
                pc.connectionState
                != "closed"
            ):

                await pc.close()

        except Exception as exc:

            print(
                "STREETGO WEBRTC CLOSE ERROR:",
                exc,
            )

        # -------------------------------------------------
        # Check remaining peers.
        # -------------------------------------------------

        peers = peer_connections.get(
            live_id
        )

        if peers:

            broadcaster_peers = peers.get(
                "broadcaster",
                set(),
            )

            viewer_peers = peers.get(
                "viewer",
                set(),
            )

            print(
                "STREETGO WEBRTC AFTER CLEANUP:",
                "BROADCASTERS:",
                len(
                    broadcaster_peers
                ),
                "VIEWERS:",
                len(
                    viewer_peers
                ),
            )

            # -------------------------------------------------
            # If broadcaster is gone, the broadcast media
            # should no longer be available.
            #
            # This also protects against a viewer reconnecting
            # to an old broadcaster track.
            # -------------------------------------------------

            if not broadcaster_peers:

                if live_id in broadcast_tracks:

                    broadcast_tracks.pop(
                        live_id,
                        None,
                    )

                    print(
                        "STREETGO BROADCAST TRACKS REMOVED:",
                        live_id,
                    )

            # -------------------------------------------------
            # Nobody connected anymore.
            # -------------------------------------------------

            if (
                not broadcaster_peers
                and not viewer_peers
            ):

                peer_connections.pop(
                    live_id,
                    None,
                )

                broadcast_tracks.pop(
                    live_id,
                    None,
                )

                cleanup_locks.pop(
                    live_id,
                    None,
                )

                print(
                    "STREETGO WEBRTC LIVE MEDIA CLEANED:",
                    live_id,
                )

        print(
            "STREETGO WEBRTC PEER CLEANED:",
            live_id,
            role,
        )

        print(
            "========================================"
        )


# =========================================================
# CREATE WEBRTC OFFER
# =========================================================



async def get_cloudflare_ice_servers():
    key_id = os.getenv("CLOUDFLARE_TURN_KEY_ID")
    api_token = os.getenv("CLOUDFLARE_TURN_API_TOKEN")

    if not key_id or not api_token:
        raise RuntimeError(
            "Cloudflare TURN credentials are not configured"
        )

    url = (
        "https://rtc.live.cloudflare.com/v1/turn/keys/"
        f"{key_id}/credentials/generate-ice-servers"
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
            json={
                "ttl": 86400,
            },
        )

    response.raise_for_status()

    result = response.json()

    return result["iceServers"]


@router.get("/ice-servers")
async def get_ice_servers():
    """
    Return temporary Cloudflare TURN/STUN credentials
    for browser WebRTC clients.

    The Cloudflare API token remains server-side.
    """
    try:
        return {
            "iceServers": await get_cloudflare_ice_servers()
        }

    except Exception as exc:
        print(
            "Cloudflare ICE server generation failed:",
            repr(exc)
        )

        raise HTTPException(
            status_code=502,
            detail="Unable to generate WebRTC ICE servers",
        )

@router.post("/offer")
async def create_offer(
    offer: WebRTCOffer,
):

    # =====================================================
    # VALIDATION
    # =====================================================

    if not offer.live_id:

        raise HTTPException(
            status_code=400,
            detail="live_id is required",
        )

    if not offer.sdp:

        raise HTTPException(
            status_code=400,
            detail="SDP is required",
        )

    if offer.type != "offer":

        raise HTTPException(
            status_code=400,
            detail="Expected WebRTC offer",
        )

    if offer.role not in {
        "broadcaster",
        "viewer",
    }:

        raise HTTPException(
            status_code=400,
            detail=(
                "role must be "
                "broadcaster or viewer"
            ),
        )

    # =====================================================
    # CREATE PEER CONNECTION
    # =====================================================

    ice_servers = await get_cloudflare_ice_servers()

    pc = RTCPeerConnection(
        RTCConfiguration(
            iceServers=[
                RTCIceServer(
                    urls=server["urls"],
                    username=server.get("username"),
                    credential=server.get("credential"),
                )
                for server in ice_servers
            ]
        )
    )

    peers = get_live_peers(
        offer.live_id
    )

    peers.setdefault(
        offer.role,
        set(),
    ).add(
        pc
    )

    print(
        "========================================"
    )

    print(
        "STREETGO WEBRTC CONNECTION CREATED"
    )

    print(
        "LIVE ID:",
        offer.live_id,
    )

    print(
        "ROLE:",
        offer.role,
    )

    print_peer_counts(
        offer.live_id
    )

    print(
        "========================================"
    )

    # =====================================================
    # CONNECTION STATE
    # =====================================================

    @pc.on(
        "connectionstatechange"
    )
    async def on_connectionstatechange():

        print(
            "STREETGO WEBRTC STATE:",
            pc.connectionState,
            "ROLE:",
            offer.role,
            "LIVE:",
            offer.live_id,
        )

        # -------------------------------------------------
        # IMPORTANT:
        #
        # disconnected is included.
        #
        # This prevents stale viewers from remaining in
        # the viewer set after a browser disconnects.
        # -------------------------------------------------

        if pc.connectionState in {
            "failed",
            "closed",
            "disconnected",
        }:

            await cleanup_peer(
                offer.live_id,
                offer.role,
                pc,
            )


    # =====================================================
    # ICE CONNECTION STATE
    # =====================================================

    @pc.on(
        "iceconnectionstatechange"
    )
    async def on_iceconnectionstatechange():

        print(
            "STREETGO WEBRTC ICE:",
            pc.iceConnectionState,
            "ROLE:",
            offer.role,
            "LIVE:",
            offer.live_id,
        )

        # -------------------------------------------------
        # IMPORTANT:
        #
        # Handle disconnected as well as failed/closed.
        # -------------------------------------------------

        if pc.iceConnectionState in {
            "failed",
            "closed",
            "disconnected",
        }:

            await cleanup_peer(
                offer.live_id,
                offer.role,
                pc,
            )


    # =====================================================
    # BROADCASTER TRACK HANDLER
    # =====================================================

    @pc.on("track")
    def on_track(track):

        print(
            "========================================"
        )

        print(
            "STREETGO WEBRTC TRACK RECEIVED"
        )

        print(
            "LIVE:",
            offer.live_id,
        )

        print(
            "ROLE:",
            offer.role,
        )

        print(
            "TRACK:",
            track.kind,
        )

        print(
            "TRACK ID:",
            track.id,
        )

        print(
            "========================================"
        )

        # -------------------------------------------------
        # Only broadcaster sends media.
        # -------------------------------------------------

        if offer.role != "broadcaster":

            return

        # -------------------------------------------------
        # Create storage for this live.
        # -------------------------------------------------

        if (
            offer.live_id
            not in broadcast_tracks
        ):

            broadcast_tracks[
                offer.live_id
            ] = {}

        # -------------------------------------------------
        # VIDEO
        # -------------------------------------------------
        if track.kind == "video":

            broadcast_tracks[
                offer.live_id
            ]["video"] = track

            print(
                "STREETGO VIDEO TRACK STORED:",
                offer.live_id,
                track.id,
            )

            async def verify_video_frames():

                try:

                    probe = relay.subscribe(
                        track,
                        buffered=False,
                    )

                    frame = await asyncio.wait_for(
                        probe.recv(),
                        timeout=5.0,
                    )

                    print(
                        "========================================"
                    )

                    print(
                        "STREETGO VIDEO FRAME RECEIVED"
                    )

                    print(
                        "LIVE:",
                        offer.live_id,
                    )

                    print(
                        "TRACK:",
                        track.id,
                    )

                    print(
                        "FRAME:",
                        type(frame).__name__,
                    )

                    print(
                        "FRAME SIZE:",
                        getattr(frame, "width", None),
                        "x",
                        getattr(frame, "height", None),
                    )

                    print(
                        "========================================"
                    )

                except Exception as exc:

                    print(
                        "========================================"
                    )

                    print(
                        "STREETGO VIDEO FRAME TEST FAILED"
                    )

                    print(
                        "LIVE:",
                        offer.live_id,
                    )

                    print(
                        "TRACK:",
                        track.id,
                    )

                    print(
                        "ERROR:",
                        repr(exc),
                    )

                    print(
                        "========================================"
                    )

            asyncio.create_task(
                verify_video_frames()
            )


        # -------------------------------------------------
        # AUDIO
        # -------------------------------------------------

        elif track.kind == "audio":

            broadcast_tracks[
                offer.live_id
            ]["audio"] = track

            print(
                "STREETGO AUDIO TRACK STORED:",
                offer.live_id,
                track.id,
            )


    # =====================================================
    # SET REMOTE DESCRIPTION
    # =====================================================

    try:

        remote_description = (
            RTCSessionDescription(
                sdp=offer.sdp,
                type=offer.type,
            )
        )

        await pc.setRemoteDescription(
            remote_description
        )

        # =================================================
        # VIEWER
        # =================================================

        if offer.role == "viewer":

            print(
                "========================================"
            )

            print(
                "STREETGO VIEWER WAITING FOR MEDIA"
            )

            print(
                "LIVE:",
                offer.live_id,
            )

            print(
                "========================================"
            )

            # ---------------------------------------------
            # Wait for broadcaster video.
            # ---------------------------------------------

            tracks = (
                await wait_for_broadcast_tracks(
                    offer.live_id,
                    timeout=15.0,
                )
            )

            print(
                "STREETGO VIEWER TRACK CHECK:",
                offer.live_id,
            )

            print(
                "VIDEO:",
                tracks.get("video") is not None,
            )

            print(
                "AUDIO:",
                tracks.get("audio") is not None,
            )

            # ---------------------------------------------
            # VIDEO
            # ---------------------------------------------

            video_track = tracks.get(
                "video"
            )

            if video_track is not None:

                pc.addTrack(
                    relay.subscribe(
                        video_track,
                        buffered=False,
                    )
                )

                print(
                    "STREETGO VIEWER VIDEO RELAY ATTACHED:",
                    offer.live_id,
                )

            else:

                print(
                    "STREETGO VIEWER VIDEO NOT AVAILABLE:",
                    offer.live_id,
                )

            # ---------------------------------------------
            # AUDIO
            # ---------------------------------------------

            audio_track = tracks.get(
                "audio"
            )

            if audio_track is not None:

                pc.addTrack(
                    relay.subscribe(
                        audio_track,
                        buffered=False,
                    )
                )

                print(
                    "STREETGO VIEWER AUDIO RELAY ATTACHED:",
                    offer.live_id,
                )

            else:

                print(
                    "STREETGO VIEWER AUDIO NOT AVAILABLE:",
                    offer.live_id,
                )

        # =================================================
        # CREATE ANSWER
        # =================================================

        answer = await pc.createAnswer()

        await pc.setLocalDescription(
            answer
        )

        # =================================================
        # VERIFY LOCAL DESCRIPTION
        # =================================================

        if not pc.localDescription:

            await cleanup_peer(
                offer.live_id,
                offer.role,
                pc,
            )

            raise HTTPException(
                status_code=500,
                detail=(
                    "WebRTC local description "
                    "was not created"
                ),
            )

        # =================================================
        # ANSWER DEBUG
        # =================================================

        print(
            "========================================"
        )

        print(
            "STREETGO WEBRTC ANSWER CREATED"
        )

        print(
            "LIVE:",
            offer.live_id,
        )

        print(
            "ROLE:",
            offer.role,
        )

        print(
            "TYPE:",
            pc.localDescription.type,
        )

        print_peer_counts(
            offer.live_id
        )

        print(
            "========================================"
        )

        # =================================================
        # RETURN ANSWER
        # =================================================

        return {
            "success": True,

            "live_id":
                offer.live_id,

            "role":
                offer.role,

            "sdp":
                pc.localDescription.sdp,

            "type":
                pc.localDescription.type,
        }

    except HTTPException:

        raise

    except Exception as exc:

        print(
            "========================================"
        )

        print(
            "STREETGO WEBRTC OFFER ERROR"
        )

        print(
            "LIVE:",
            offer.live_id,
        )

        print(
            "ROLE:",
            offer.role,
        )

        print(
            "ERROR:",
            repr(exc),
        )

        print(
            "========================================"
        )

        # -------------------------------------------------
        # CRITICAL:
        #
        # If offer creation fails after the peer was added
        # to the active set, remove it immediately.
        # -------------------------------------------------

        await cleanup_peer(
            offer.live_id,
            offer.role,
            pc,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "WebRTC offer processing failed: "
                f"{exc}"
            ),
        )

