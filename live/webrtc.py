import asyncio
import os
import httpx

from dotenv import load_dotenv
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel
from aiortc import RTCPeerConnection,RTCSessionDescription,RTCIceServer,RTCConfiguration
from aiortc.contrib.media import MediaRelay

load_dotenv()

router=APIRouter(prefix="/live/webrtc",tags=["Live WebRTC"])
relay=MediaRelay()
peer_connections={}
broadcast_tracks={}
cleaned_peers=set()


class WebRTCOffer(BaseModel):
    live_id:str
    sdp:str
    type:str
    role:str


def get_live_peers(live_id):
    if live_id not in peer_connections:
        peer_connections[live_id]={
            "broadcaster":set(),
            "viewer":set(),
        }
    return peer_connections[live_id]


def print_peer_counts(live_id):
    peers=get_live_peers(live_id)
    print(
        "STREETGO PEERS:",
        live_id,
        "BROADCASTERS:",
        len(peers["broadcaster"]),
        "VIEWERS:",
        len(peers["viewer"]),
        flush=True,
    )


async def cleanup_peer(live_id,role,pc):
    if pc in cleaned_peers:
        return

    cleaned_peers.add(pc)

    try:
        peers=peer_connections.get(live_id)

        if peers:
            peers.get(role,set()).discard(pc)

        if pc.connectionState!="closed":
            await pc.close()

    except Exception as exc:
        print(
            "STREETGO CLEANUP ERROR:",
            repr(exc),
            flush=True,
        )

    peers=peer_connections.get(live_id)

    if not peers:
        return

    if not peers["broadcaster"]:
        broadcast_tracks.pop(live_id,None)

    if (
        not peers["broadcaster"]
        and not peers["viewer"]
    ):
        peer_connections.pop(live_id,None)

    print_peer_counts(live_id)


async def get_cloudflare_ice_servers():
    key_id=os.getenv("CLOUDFLARE_TURN_KEY_ID")
    token=os.getenv("CLOUDFLARE_TURN_API_TOKEN")

    if not key_id or not token:
        raise RuntimeError(
            "Cloudflare TURN credentials are not configured"
        )

    url=(
        "https://rtc.live.cloudflare.com/v1/turn/keys/"
        f"{key_id}/credentials/generate-ice-servers"
    )

    async with httpx.AsyncClient(timeout=10) as client:
        response=await client.post(
            url,
            headers={
                "Authorization":f"Bearer {token}",
                "Content-Type":"application/json",
            },
            json={"ttl":86400},
        )

    response.raise_for_status()
    return response.json()["iceServers"]


@router.get("/ice-servers")
async def get_ice_servers():
    try:
        return {
            "iceServers":
            await get_cloudflare_ice_servers()
        }
    except Exception as exc:
        print(
            "STREETGO ICE SERVER ERROR:",
            repr(exc),
            flush=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Unable to generate WebRTC ICE servers",
        )


async def wait_for_ice(pc,timeout=10):
    if pc.iceGatheringState=="complete":
        return

    started=asyncio.get_running_loop().time()

    while pc.iceGatheringState!="complete":
        if (
            asyncio.get_running_loop().time()
            -started
            >=timeout
        ):
            print(
                "STREETGO ICE GATHERING TIMEOUT:",
                pc.iceGatheringState,
                flush=True,
            )
            return

        await asyncio.sleep(.05)


async def wait_for_broadcast_tracks(
    live_id,
    timeout=15,
):
    started=asyncio.get_running_loop().time()

    print(
        "STREETGO WAITING FOR BROADCAST TRACKS:",
        live_id,
        flush=True,
    )

    while True:
        tracks=broadcast_tracks.get(
            live_id,
            {},
        )

        if tracks.get("video") is not None:
            print(
                "STREETGO BROADCAST VIDEO READY:",
                live_id,
                flush=True,
            )
            return tracks

        if (
            asyncio.get_running_loop().time()
            -started
            >=timeout
        ):
            print(
                "STREETGO BROADCAST TRACK TIMEOUT:",
                live_id,
                flush=True,
            )
            return tracks

        await asyncio.sleep(.1)


@router.post("/offer")
async def create_offer(offer:WebRTCOffer):
    pc=None

    try:
        if not offer.live_id:
            raise HTTPException(
                400,
                "live_id is required",
            )

        if not offer.sdp:
            raise HTTPException(
                400,
                "SDP is required",
            )

        if offer.type!="offer":
            raise HTTPException(
                400,
                "Expected WebRTC offer",
            )

        if offer.role not in {
            "broadcaster",
            "viewer",
        }:
            raise HTTPException(
                400,
                "role must be broadcaster or viewer",
            )

        print(
            "========================================",
            flush=True,
        )

        print(
            "STREETGO WEBRTC OFFER RECEIVED",
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
            "OFFER SDP LENGTH:",
            len(offer.sdp),
            flush=True,
        )

        pc=RTCPeerConnection(
            RTCConfiguration(
                iceServers=[
                    RTCIceServer(
                        urls="stun:stun.cloudflare.com:3478"
                    )
                ]
            )
        )

        peers=get_live_peers(
            offer.live_id
        )

        peers[offer.role].add(pc)

        print_peer_counts(
            offer.live_id
        )

        @pc.on("connectionstatechange")
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
                "failed",
                "closed",
                "disconnected",
            }:
                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

        @pc.on("iceconnectionstatechange")
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
                "failed",
                "closed",
                "disconnected",
            }:
                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

        @pc.on("track")
        def on_track(track):
            print(
                "STREETGO WEBRTC TRACK:",
                track.kind,
                track.id,
                "LIVE:",
                offer.live_id,
                flush=True,
            )

            if offer.role!="broadcaster":
                return

            tracks=broadcast_tracks.setdefault(
                offer.live_id,
                {},
            )

            tracks[track.kind]=track

            print(
                "STREETGO TRACK STORED:",
                track.kind,
                track.id,
                flush=True,
            )

        remote=RTCSessionDescription(
            sdp=offer.sdp,
            type=offer.type,
        )

        await pc.setRemoteDescription(
            remote
        )

        print(
            "STREETGO REMOTE DESCRIPTION ACCEPTED",
            flush=True,
        )

        if offer.role=="viewer":
            tracks=await wait_for_broadcast_tracks(
                offer.live_id,
                15,
            )

            video=tracks.get("video")
            audio=tracks.get("audio")

            if video is None:
                await cleanup_peer(
                    offer.live_id,
                    offer.role,
                    pc,
                )

                raise HTTPException(
                    503,
                    "Broadcaster video is not ready yet. Please retry.",
                )

            pc.addTrack(
                relay.subscribe(
                    video,
                    buffered=False,
                )
            )

            if audio is not None:
                pc.addTrack(
                    relay.subscribe(
                        audio,
                        buffered=False,
                    )
                )

        answer=await pc.createAnswer()

        await pc.setLocalDescription(
            answer
        )

        await wait_for_ice(
            pc
        )

        if not pc.localDescription:
            raise HTTPException(
                500,
                "WebRTC local description was not created.",
            )

        answer_sdp=pc.localDescription.sdp

        print(
            "STREETGO ANSWER SDP LENGTH:",
            len(answer_sdp),
            flush=True,
        )

        print(
            "STREETGO ANSWER SDP:",
            flush=True,
        )

        print(
            answer_sdp,
            flush=True,
        )

        print(
            "STREETGO WEBRTC ANSWER READY",
            flush=True,
        )

        print(
            "========================================",
            flush=True,
        )

        return {
            "success":True,
            "live_id":offer.live_id,
            "role":offer.role,
            "sdp":answer_sdp,
            "type":"answer",
        }

    except HTTPException:
        if pc is not None:
            await cleanup_peer(
                offer.live_id,
                offer.role,
                pc,
            )
        raise

    except Exception as exc:
        print(
            "STREETGO WEBRTC ERROR:",
            type(exc).__name__,
            repr(exc),
            flush=True,
        )

        if pc is not None:
            await cleanup_peer(
                offer.live_id,
                offer.role,
                pc,
            )

        raise HTTPException(
            500,
            f"WebRTC offer processing failed: {exc}",
        )