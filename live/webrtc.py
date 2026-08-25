import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
)

from aiortc.contrib.media import MediaRelay


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
#
# IMPORTANT:
# The role sent by the frontend is "viewer",
# NOT "viewers".
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
# WEBRTC OFFER MODEL
# =========================================================

class WebRTCOffer(BaseModel):

    live_id: str

    sdp: str

    type: str

    role: str


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

    return peer_connections[live_id]


# =========================================================
# WAIT FOR BROADCASTER MEDIA
#
# This solves the race condition where the viewer connects
# before the broadcaster's camera/audio tracks have arrived.
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
        # Video is the minimum requirement for a viewer.
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
        # Wait briefly and check again
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

    peers = peer_connections.get(
        live_id
    )

    if peers:

        # -------------------------------------------------
        # IMPORTANT
        #
        # role is exactly:
        #
        # "broadcaster"
        # "viewer"
        #
        # So use peers.get(role).
        # -------------------------------------------------

        peers.get(
            role,
            set(),
        ).discard(
            pc
        )

    try:

        await pc.close()

    except Exception:

        pass

    # -----------------------------------------------------
    # If nobody is connected anymore, clean everything.
    # -----------------------------------------------------

    peers = peer_connections.get(
        live_id
    )

    if peers:

        if (
            not peers.get(
                "broadcaster",
                set(),
            )
            and not peers.get(
                "viewer",
                set(),
            )
        ):

            peer_connections.pop(
                live_id,
                None,
            )

            broadcast_tracks.pop(
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


# =========================================================
# CREATE WEBRTC OFFER
# =========================================================

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

    pc = RTCPeerConnection()

    peers = get_live_peers(
        offer.live_id
    )

    # -----------------------------------------------------
    # FIX:
    #
    # "viewer" is now an actual key in peers.
    # -----------------------------------------------------

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

    print(
        "BROADCASTERS:",
        len(
            peers.get(
                "broadcaster",
                set(),
            )
        ),
    )

    print(
        "VIEWERS:",
        len(
            peers.get(
                "viewer",
                set(),
            )
        ),
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

        if pc.connectionState in {
            "failed",
            "closed",
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

        if pc.iceConnectionState == "failed":

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
        # Only broadcaster is allowed to send media.
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

    remote_description = (
        RTCSessionDescription(
            sdp=offer.sdp,
            type=offer.type,
        )
    )

    await pc.setRemoteDescription(
        remote_description
    )

    # =====================================================
    # VIEWER
    # =====================================================

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

        # -------------------------------------------------
        # WAIT FOR BROADCASTER VIDEO.
        #
        # This is important.
        #
        # The viewer must not create the answer before
        # the broadcaster track exists.
        # -------------------------------------------------

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

        # -------------------------------------------------
        # VIDEO
        # -------------------------------------------------

        video_track = tracks.get(
            "video"
        )

        if video_track is not None:

            pc.addTrack(
                relay.subscribe(
                    video_track
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

        # -------------------------------------------------
        # AUDIO
        # -------------------------------------------------

        audio_track = tracks.get(
            "audio"
        )

        if audio_track is not None:

            pc.addTrack(
                relay.subscribe(
                    audio_track
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

    # =====================================================
    # CREATE ANSWER
    # =====================================================

    answer = await pc.createAnswer()

    await pc.setLocalDescription(
        answer
    )

    # =====================================================
    # VERIFY LOCAL DESCRIPTION
    # =====================================================

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

    # =====================================================
    # ANSWER DEBUG
    # =====================================================

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

    print(
        "========================================"
    )

    # =====================================================
    # RETURN ANSWER
    # =====================================================

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