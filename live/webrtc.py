import asyncio
import os
import re

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
# =========================================================

cleanup_locks: dict[
    str,
    asyncio.Lock,
] = {}


# =========================================================
# CLEANED PEERS
# =========================================================

cleaned_peers: set[
    RTCPeerConnection
] = set()


# =========================================================
# DISCONNECT GRACE TIMERS
#
# A temporary WebRTC "disconnected" state should not
# immediately destroy the peer or broadcaster media.
# =========================================================

disconnect_timers: dict[
    RTCPeerConnection,
    asyncio.Task,
] = {}


# =========================================================
# OFFER MODEL
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
        cleanup_locks[live_id] = asyncio.Lock()

    return cleanup_locks[live_id]


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

    peer_connections[live_id].setdefault(
        "broadcaster",
        set(),
    )

    peer_connections[live_id].setdefault(
        "viewer",
        set(),
    )

    return peer_connections[live_id]


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
        flush=True,
    )


# =========================================================
# GET ACTIVE BROADCAST TRACK
# =========================================================

def get_active_broadcast_track(
    live_id: str,
    kind: str,
):
    tracks = broadcast_tracks.get(
        live_id
    )

    if not tracks:
        return None

    track = tracks.get(
        kind
    )

    if track is None:
        return None

    try:
        ready_state = getattr(
            track,
            "readyState",
            None,
        )

        if ready_state == "ended":
            return None

    except Exception:
        pass

    return track


# =========================================================
# WAIT FOR BROADCASTER MEDIA
#
# The viewer must not get a successful SDP answer
# before broadcaster video is actually available.
# =========================================================

async def wait_for_broadcast_tracks(
    live_id: str,
    timeout: float = 15.0,
):
    print(
        "STREETGO WAITING FOR BROADCASTER TRACKS:",
        live_id,
        flush=True,
    )

    started = (
        asyncio.get_running_loop().time()
    )

    while True:

        video_track = (
            get_active_broadcast_track(
                live_id,
                "video",
            )
        )

        if video_track is not None:
            print(
                "STREETGO BROADCASTER VIDEO READY:",
                live_id,
                flush=True,
            )

            return broadcast_tracks.get(
                live_id,
                {},
            )

        elapsed = (
            asyncio.get_running_loop().time()
            - started
        )

        if elapsed >= timeout:
            print(
                "STREETGO BROADCASTER TRACK WAIT TIMEOUT:",
                live_id,
                flush=True,
            )

            return {}

        await asyncio.sleep(
            0.1
        )


# =========================================================
# CANCEL DISCONNECT TIMER
# =========================================================

def cancel_disconnect_timer(
    pc: RTCPeerConnection,
):
    task = disconnect_timers.pop(
        pc,
        None,
    )

    if task and not task.done():
        task.cancel()


# =========================================================
# DELAYED DISCONNECT CLEANUP
# =========================================================

async def delayed_disconnect_cleanup(
    live_id: str,
    role: str,
    pc: RTCPeerConnection,
    delay: float = 10.0,
):
    try:
        await asyncio.sleep(
            delay
        )

        if pc in cleaned_peers:
            return

        if pc.connectionState in {
            "connected",
            "connecting",
        }:
            return

        if pc.connectionState in {
            "disconnected",
            "failed",
            "closed",
        }:
            await cleanup_peer(
                live_id,
                role,
                pc,
            )

    except asyncio.CancelledError:
        return

    except Exception as exc:
        print(
            "STREETGO DELAYED CLEANUP ERROR:",
            repr(exc),
            flush=True,
        )

    finally:
        disconnect_timers.pop(
            pc,
            None,
        )


# =========================================================
# SCHEDULE DISCONNECT CLEANUP
# =========================================================

def schedule_disconnect_cleanup(
    live_id: str,
    role: str,
    pc: RTCPeerConnection,
):
    cancel_disconnect_timer(
        pc
    )

    task = asyncio.create_task(
        delayed_disconnect_cleanup(
            live_id,
            role,
            pc,
            10.0,
        )
    )

    disconnect_timers[
        pc
    ] = task


# =========================================================
# CLEANUP PEER
# =========================================================

async def cleanup_peer(
    live_id: str,
    role: str,
    pc: RTCPeerConnection,
):
    if pc in cleaned_peers:
        return

    cleaned_peers.add(
        pc
    )

    cancel_disconnect_timer(
        pc
    )

    lock = get_cleanup_lock(
        live_id
    )

    async with lock:

        print(
            "========================================",
            flush=True,
        )

        print(
            "STREETGO WEBRTC CLEANUP START",
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

        try:

            if (
                pc.connectionState
                != "closed"
            ):
                await pc.close()

        except Exception as exc:
            print(
                "STREETGO WEBRTC CLOSE ERROR:",
                repr(exc),
                flush=True,
            )

        peers = peer_connections.get(
            live_id
        )

        if peers:

            broadcaster_peers = (
                peers.get(
                    "broadcaster",
                    set(),
                )
            )

            viewer_peers = (
                peers.get(
                    "viewer",
                    set(),
                )
            )

            print(
                "STREETGO WEBRTC AFTER CLEANUP:",
                "BROADCASTERS:",
                len(broadcaster_peers),
                "VIEWERS:",
                len(viewer_peers),
                flush=True,
            )

            # If no broadcaster peer remains,
            # its media is no longer valid.

            if not broadcaster_peers:

                broadcast_tracks.pop(
                    live_id,
                    None,
                )

                print(
                    "STREETGO BROADCAST TRACKS REMOVED:",
                    live_id,
                    flush=True,
                )

            # If nobody remains at all,
            # remove the complete live state.

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
                    flush=True,
                )

        print(
            "STREETGO WEBRTC PEER CLEANED:",
            live_id,
            role,
            flush=True,
        )

        print(
            "========================================",
            flush=True,
        )


# =========================================================
# CLOUDFLARE ICE SERVERS
# =========================================================

async def get_cloudflare_ice_servers():
    key_id = os.getenv(
        "CLOUDFLARE_TURN_KEY_ID"
    )

    api_token = os.getenv(
        "CLOUDFLARE_TURN_API_TOKEN"
    )

    if (
        not key_id
        or not api_token
    ):
        raise RuntimeError(
            "Cloudflare TURN credentials are not configured"
        )

    url = (
        "https://rtc.live.cloudflare.com/v1/turn/keys/"
        f"{key_id}/credentials/generate-ice-servers"
    )

    async with httpx.AsyncClient(
        timeout=10.0
    ) as client:

        response = await client.post(
            url,
            headers={
                "Authorization":
                    f"Bearer {api_token}",

                "Content-Type":
                    "application/json",
            },
            json={
                "ttl":
                    86400,
            },
        )

    response.raise_for_status()

    result = response.json()

    return result[
        "iceServers"
    ]


# =========================================================
# GET ICE SERVERS
# =========================================================

@router.get(
    "/ice-servers"
)
async def get_ice_servers():

    try:

        return {
            "iceServers":
                await get_cloudflare_ice_servers()
        }

    except Exception as exc:

        print(
            "Cloudflare ICE server generation failed:",
            repr(exc),
            flush=True,
        )

        raise HTTPException(
            status_code=502,
            detail=
                "Unable to generate WebRTC ICE servers",
        )


# =========================================================
# SDP MEDIA DIRECTION
# =========================================================

def get_media_direction(
    sdp: str,
    media_kind: str,
) -> str | None:

    sections = re.split(
        r"(?=m=)",
        sdp,
    )

    for section in sections:

        if not section.startswith(
            f"m={media_kind} "
        ):
            continue

        for direction in (
            "sendrecv",
            "sendonly",
            "recvonly",
            "inactive",
        ):

            if (
                f"a={direction}"
                in section
            ):
                return direction

    return None


# =========================================================
# WAIT FOR BACKEND ICE GATHERING
# =========================================================

async def wait_for_ice_gathering_complete(
    pc: RTCPeerConnection,
    timeout: float = 10.0,
):

    if (
        pc.iceGatheringState
        == "complete"
    ):
        return

    started = (
        asyncio.get_running_loop().time()
    )

    while (
        pc.iceGatheringState
        != "complete"
    ):

        elapsed = (
            asyncio.get_running_loop().time()
            - started
        )

        if elapsed >= timeout:

            print(
                "STREETGO BACKEND ICE GATHERING TIMEOUT:",
                pc.iceGatheringState,
                flush=True,
            )

            return

        await asyncio.sleep(
            0.05
        )


# =========================================================
# CREATE WEBRTC OFFER / ANSWER
# =========================================================

@router.post(
    "/offer"
)
async def create_offer(
    offer: WebRTCOffer,
):

    pc: RTCPeerConnection | None = None

    try:

        # =================================================
        # VALIDATION
        # =================================================

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
                detail=
                    "Expected WebRTC offer",
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

        # =================================================
        # CREATE BACKEND PEER CONNECTION
        # =================================================

        pc = RTCPeerConnection(
            RTCConfiguration(
                iceServers=[
                    RTCIceServer(
                        urls=
                            "stun:stun.cloudflare.com:3478",
                    ),
                ],
            )
        )

        print(
            "STREETGO BACKEND ICE: Cloudflare TURN disabled",
            flush=True,
        )

        # =================================================
        # REGISTER PEER
        # =================================================

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
            "========================================",
            flush=True,
        )

        print(
            "STREETGO WEBRTC CONNECTION CREATED",
            flush=True,
        )

        print(
            "LIVE ID:",
            offer.live_id,
            flush=True,
        )

        print(
            "ROLE:",
            offer.role,
            flush=True,
        )

        print_peer_counts(
            offer.live_id
        )

        print(
            "========================================",
            flush=True,
        )

        # =================================================
        # CONNECTION STATE
        # =================================================

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
                flush=True,
            )

            if pc.connectionState in {
                "connected",
                "connecting",
            }:

                cancel_disconnect_timer(
                    pc
                )

                return

            if (
                pc.connectionState
                == "disconnected"
            ):

                schedule_disconnect_cleanup(
                    offer.live_id,
                    offer.role,
                    pc,
                )

                return

            if pc.connectionState in {
                "failed",
                "closed",
            }:

                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

        # =================================================
        # ICE CONNECTION STATE
        # =================================================

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
                flush=True,
            )

            if pc.iceConnectionState in {
                "connected",
                "completed",
            }:

                cancel_disconnect_timer(
                    pc
                )

                return

            if (
                pc.iceConnectionState
                == "disconnected"
            ):

                schedule_disconnect_cleanup(
                    offer.live_id,
                    offer.role,
                    pc,
                )

                return

            if pc.iceConnectionState in {
                "failed",
                "closed",
            }:

                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

        # =================================================
        # BROADCASTER TRACK HANDLER
        # =================================================

        @pc.on(
            "track"
        )
        def on_track(
            track
        ):

            print(
                "========================================",
                flush=True,
            )

            print(
                "STREETGO WEBRTC TRACK RECEIVED",
                flush=True,
            )

            print(
                "LIVE:",
                offer.live_id,
                flush=True,
            )

            print(
                "ROLE:",
                offer.role,
                flush=True,
            )

            print(
                "TRACK:",
                track.kind,
                flush=True,
            )

            print(
                "TRACK ID:",
                track.id,
                flush=True,
            )

            print(
                "========================================",
                flush=True,
            )

            if (
                offer.role
                != "broadcaster"
            ):
                return

            broadcast_tracks.setdefault(
                offer.live_id,
                {}
            )

            if (
                track.kind
                == "video"
            ):

                broadcast_tracks[
                    offer.live_id
                ][
                    "video"
                ] = track

                print(
                    "STREETGO VIDEO TRACK STORED:",
                    offer.live_id,
                    track.id,
                    flush=True,
                )

            elif (
                track.kind
                == "audio"
            ):

                broadcast_tracks[
                    offer.live_id
                ][
                    "audio"
                ] = track

                print(
                    "STREETGO AUDIO TRACK STORED:",
                    offer.live_id,
                    track.id,
                    flush=True,
                )

        # =================================================
        # SET REMOTE DESCRIPTION
        # =================================================

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

        if (
            offer.role
            == "viewer"
        ):

            print(
                "========================================",
                flush=True,
            )

            print(
                "STREETGO VIEWER WAITING FOR MEDIA",
                flush=True,
            )

            print(
                "LIVE:",
                offer.live_id,
                flush=True,
            )

            print(
                "========================================",
                flush=True,
            )

            # ---------------------------------------------
            # Wait for broadcaster media.
            # ---------------------------------------------

            tracks = (
                await wait_for_broadcast_tracks(
                    offer.live_id,
                    timeout=15.0,
                )
            )

            video_track = (
                get_active_broadcast_track(
                    offer.live_id,
                    "video",
                )
            )

            audio_track = (
                get_active_broadcast_track(
                    offer.live_id,
                    "audio",
                )
            )

            print(
                "STREETGO VIEWER TRACK CHECK:",
                offer.live_id,
                flush=True,
            )

            print(
                "VIDEO:",
                video_track is not None,
                flush=True,
            )

            print(
                "AUDIO:",
                audio_track is not None,
                flush=True,
            )

            # ---------------------------------------------
            # Video is REQUIRED.
            # ---------------------------------------------

            if video_track is None:

                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Broadcaster video is not ready yet. "
                        "Please retry."
                    ),
                )

            # ---------------------------------------------
            # Attach broadcaster video.
            # ---------------------------------------------

            pc.addTrack(
                relay.subscribe(
                    video_track,
                    buffered=False,
                )
            )

            print(
                "STREETGO VIEWER VIDEO RELAY ATTACHED:",
                offer.live_id,
                video_track.id,
                flush=True,
            )

            # ---------------------------------------------
            # Attach broadcaster audio if available.
            # ---------------------------------------------

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
                    audio_track.id,
                    flush=True,
                )

            else:

                print(
                    "STREETGO VIEWER AUDIO NOT AVAILABLE:",
                    offer.live_id,
                    flush=True,
                )

        # =================================================
        # CREATE ANSWER
        # =================================================

        answer = await pc.createAnswer()

        await pc.setLocalDescription(
            answer
        )

        # =================================================
        # WAIT FOR BACKEND ICE
        # =================================================

        await wait_for_ice_gathering_complete(
            pc
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
        # VERIFY VIEWER ANSWER
        # =================================================

        if (
            offer.role
            == "viewer"
        ):

            answer_sdp = (
                pc.localDescription.sdp
            )

            video_direction = (
                get_media_direction(
                    answer_sdp,
                    "video",
                )
            )

            audio_direction = (
                get_media_direction(
                    answer_sdp,
                    "audio",
                )
            )

            print(
                "STREETGO VIEWER ANSWER MEDIA:",
                {
                    "video":
                        video_direction,

                    "audio":
                        audio_direction,
                },
                flush=True,
            )

            # ---------------------------------------------
            # Video must be server -> viewer.
            # ---------------------------------------------

            if video_direction not in {
                "sendonly",
                "sendrecv",
            }:

                print(
                    "STREETGO INVALID VIEWER VIDEO DIRECTION:",
                    video_direction,
                    flush=True,
                )

                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Viewer video negotiation failed. "
                        "The server did not create an active "
                        "video sending direction."
                    ),
                )

            if (
                video_direction
                == "inactive"
            ):

                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Viewer video negotiation is inactive. "
                        "Please retry."
                    ),
                )

        # =================================================
        # DEBUG
        # =================================================

        print(
            "========================================",
            flush=True,
        )

        print(
            "STREETGO WEBRTC ANSWER CREATED",
            flush=True,
        )

        print(
            "LIVE:",
            offer.live_id,
            flush=True,
        )

        print(
            "ROLE:",
            offer.role,
            flush=True,
        )

        print(
            "TYPE:",
            pc.localDescription.type,
            flush=True,
        )

        print_peer_counts(
            offer.live_id
        )

        if (
            offer.role
            == "viewer"
        ):

            print(
                "VIDEO DIRECTION:",
                get_media_direction(
                    pc.localDescription.sdp,
                    "video",
                ),
                flush=True,
            )

            print(
                "AUDIO DIRECTION:",
                get_media_direction(
                    pc.localDescription.sdp,
                    "audio",
                ),
                flush=True,
            )

        print(
            "========================================",
            flush=True,
        )

        # =================================================
        # RETURN ANSWER
        # =================================================

        return {
            "success":
                True,

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
            "========================================",
            flush=True,
        )

        print(
            "STREETGO WEBRTC OFFER ERROR",
            flush=True,
        )

        print(
            "LIVE:",
            offer.live_id,
            flush=True,
        )

        print(
            "ROLE:",
            offer.role,
            flush=True,
        )

        print(
            "ERROR TYPE:",
            type(exc).__name__,
            flush=True,
        )

        print(
            "ERROR:",
            repr(exc),
            flush=True,
        )

        print(
            "========================================",
            flush=True,
        )

        if pc is not None:

            try:

                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

            except Exception as cleanup_exc:

                print(
                    "STREETGO WEBRTC FINAL CLEANUP ERROR:",
                    repr(cleanup_exc),
                    flush=True,
                )

        raise HTTPException(
            status_code=500,
            detail=(
                "WebRTC offer processing failed: "
                f"{exc}"
            ),
        )