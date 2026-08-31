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

router = APIRouter(
    prefix="/live/webrtc",
    tags=["Live WebRTC"],
)

relay = MediaRelay()

peer_connections: dict[str, dict[str, set[RTCPeerConnection]]] = {}
broadcast_tracks: dict[str, dict[str, object]] = {}
cleaned_peers: set[RTCPeerConnection] = set()


class WebRTCOffer(BaseModel):
    live_id: str
    sdp: str
    type: str
    role: str


def get_live_peers(live_id: str):
    if live_id not in peer_connections:
        peer_connections[live_id] = {
            "broadcaster": set(),
            "viewer": set(),
        }
    return peer_connections[live_id]


def print_peer_counts(live_id: str):
    peers = get_live_peers(live_id)
    print(
        "STREETGO PEERS:",
        live_id,
        "BROADCASTERS:",
        len(peers["broadcaster"]),
        "VIEWERS:",
        len(peers["viewer"]),
        flush=True,
    )


async def cleanup_peer(
    live_id: str,
    role: str,
    pc: RTCPeerConnection,
):
    if pc in cleaned_peers:
        return

    cleaned_peers.add(pc)

    try:
        peers = peer_connections.get(live_id)

        if peers:
            peers.get(role, set()).discard(pc)

        if pc.connectionState != "closed":
            await pc.close()

    except Exception as exc:
        print(
            "STREETGO CLEANUP ERROR:",
            repr(exc),
            flush=True,
        )

    peers = peer_connections.get(live_id)

    if not peers:
        return

    if not peers["broadcaster"]:
        broadcast_tracks.pop(live_id, None)

    if (
        not peers["broadcaster"]
        and not peers["viewer"]
    ):
        peer_connections.pop(live_id, None)

    print_peer_counts(live_id)


async def get_cloudflare_ice_servers():
    key_id = os.getenv(
        "CLOUDFLARE_TURN_KEY_ID"
    )

    token = os.getenv(
        "CLOUDFLARE_TURN_API_TOKEN"
    )

    if not key_id or not token:
        raise RuntimeError(
            "Cloudflare TURN credentials are not configured"
        )

    url = (
        "https://rtc.live.cloudflare.com/v1/turn/keys/"
        f"{key_id}/credentials/generate-ice-servers"
    )

    async with httpx.AsyncClient(
        timeout=10
    ) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "ttl": 86400,
            },
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
            detail=(
                "Unable to generate WebRTC ICE servers"
            ),
        )


def get_media_sections(sdp: str):
    normalized = (
        sdp
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    lines = normalized.split("\n")

    session = []
    sections = []
    current = None

    for line in lines:
        if line.startswith("m="):
            if current is not None:
                sections.append(current)

            current = [line]
            continue

        if current is None:
            session.append(line)
        else:
            current.append(line)

    if current is not None:
        sections.append(current)

    return session, sections


def get_section_mid(section):
    for line in section:
        if line.startswith("a=mid:"):
            return line[6:].strip()

    return None


def get_offer_mids(sdp: str):
    _, sections = get_media_sections(sdp)

    mids = []

    for index, section in enumerate(sections):
        mid = get_section_mid(section)

        if not mid:
            mid = str(index)

        mids.append(mid)

    return mids


def get_answer_mids(sdp: str):
    _, sections = get_media_sections(sdp)

    return [
        get_section_mid(section)
        for section in sections
    ]


def get_bundle_mids(sdp: str):
    matches = re.findall(
        r"(?m)^a=group:BUNDLE[ \t]+([^\r\n]*)",
        sdp,
    )

    if not matches:
        return []

    return matches[0].split()


def repair_answer_sdp(
    offer_sdp: str,
    answer_sdp: str,
):
    offer_session, offer_sections = (
        get_media_sections(offer_sdp)
    )

    answer_session, answer_sections = (
        get_media_sections(answer_sdp)
    )

    offer_mids = []

    for index, section in enumerate(
        offer_sections
    ):
        mid = get_section_mid(section)

        if not mid:
            mid = str(index)

        offer_mids.append(mid)

    if not offer_mids:
        raise ValueError(
            "Browser offer contains no media sections."
        )

    if not answer_sections:
        raise ValueError(
            "Backend answer contains no media sections."
        )

    if len(answer_sections) != len(
        offer_sections
    ):
        raise ValueError(
            "WebRTC answer media-section count "
            "does not match the offer."
        )

    repaired_sections = []

    for index, section in enumerate(
        answer_sections
    ):
        expected_mid = offer_mids[index]

        section = [
            line
            for line in section
            if not line.startswith("a=mid:")
        ]

        section.insert(
            1,
            f"a=mid:{expected_mid}",
        )

        repaired_sections.append(section)

    active_mids = []

    for index, section in enumerate(
        repaired_sections
    ):
        if section and section[0].startswith(
            "m="
        ):
            parts = section[0].split()

            if len(parts) >= 2:
                try:
                    port = int(parts[1])
                except ValueError:
                    port = 1

                if port != 0:
                    active_mids.append(
                        offer_mids[index]
                    )

    if not active_mids:
        active_mids = offer_mids[:]

    clean_session = []

    for line in offer_session:
        if line.startswith(
            "a=group:BUNDLE"
        ):
            continue

        if line == "":
            continue

        clean_session.append(line)

    clean_session.append(
        "a=group:BUNDLE "
        + " ".join(active_mids)
    )

    output = []

    output.extend(clean_session)

    for section in repaired_sections:
        output.extend(section)

    repaired = (
        "\r\n".join(output)
        + "\r\n"
    )

    return repaired


def validate_sdp_mids(
    sdp: str,
):
    _, sections = get_media_sections(sdp)

    mids = [
        get_section_mid(section)
        for section in sections
    ]

    if any(
        mid is None or mid == ""
        for mid in mids
    ):
        raise ValueError(
            "Backend answer contains an empty MID."
        )

    if len(mids) != len(set(mids)):
        raise ValueError(
            "Backend answer contains duplicate MIDs."
        )

    bundle = get_bundle_mids(sdp)

    invalid = [
        mid
        for mid in bundle
        if mid not in mids
    ]

    if invalid:
        raise ValueError(
            "Backend BUNDLE contains unknown "
            f"MIDs: {invalid}"
        )

    print(
        "STREETGO FINAL ANSWER MIDS:",
        mids,
        flush=True,
    )

    print(
        "STREETGO FINAL ANSWER BUNDLE:",
        bundle,
        flush=True,
    )


async def wait_for_ice(
    pc: RTCPeerConnection,
    timeout: float = 10,
):
    if pc.iceGatheringState == "complete":
        return

    started = asyncio.get_running_loop().time()

    while (
        pc.iceGatheringState != "complete"
    ):
        if (
            asyncio.get_running_loop().time()
            - started
            >= timeout
        ):
            print(
                "STREETGO ICE GATHERING TIMEOUT:",
                pc.iceGatheringState,
                flush=True,
            )
            return

        await asyncio.sleep(0.05)


async def wait_for_broadcast_tracks(
    live_id: str,
    timeout: float = 15,
):
    started = (
        asyncio.get_running_loop().time()
    )

    print(
        "STREETGO WAITING FOR BROADCASTER TRACKS:",
        live_id,
        flush=True,
    )

    while True:
        tracks = broadcast_tracks.get(
            live_id,
            {},
        )

        if tracks.get("video") is not None:
            print(
                "STREETGO BROADCASTER VIDEO READY:",
                live_id,
                flush=True,
            )
            return tracks

        elapsed = (
            asyncio.get_running_loop().time()
            - started
        )

        if elapsed >= timeout:
            print(
                "STREETGO BROADCASTER WAIT TIMEOUT:",
                live_id,
                flush=True,
            )
            return tracks

        await asyncio.sleep(0.1)


@router.post("/offer")
async def create_offer(
    offer: WebRTCOffer,
):
    pc = None

    try:
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

        offer_mids = get_offer_mids(
            offer.sdp
        )

        offer_bundle = get_bundle_mids(
            offer.sdp
        )

        print(
            "STREETGO OFFER MIDS:",
            offer_mids,
            flush=True,
        )

        print(
            "STREETGO OFFER BUNDLE:",
            offer_bundle,
            flush=True,
        )

        if not offer_mids:
            raise HTTPException(
                status_code=400,
                detail=(
                    "WebRTC offer has no media MIDs."
                ),
            )

        pc = RTCPeerConnection(
            RTCConfiguration(
                iceServers=[
                    RTCIceServer(
                        urls=(
                            "stun:"
                            "stun.cloudflare.com:3478"
                        ),
                    ),
                ],
            ),
        )

        peers = get_live_peers(
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
                "STREETGO WEBRTC TRACK RECEIVED:",
                track.kind,
                track.id,
                flush=True,
            )

            if offer.role != "broadcaster":
                return

            tracks = broadcast_tracks.setdefault(
                offer.live_id,
                {},
            )

            tracks[track.kind] = track

            print(
                "STREETGO TRACK STORED:",
                offer.live_id,
                track.kind,
                track.id,
                flush=True,
            )

        remote = RTCSessionDescription(
            sdp=offer.sdp,
            type="offer",
        )

        await pc.setRemoteDescription(
            remote
        )

        print(
            "STREETGO REMOTE DESCRIPTION ACCEPTED",
            flush=True,
        )

        if offer.role == "viewer":
            tracks = (
                await wait_for_broadcast_tracks(
                    offer.live_id,
                    15,
                )
            )

            video = tracks.get("video")
            audio = tracks.get("audio")

            if video is not None:
                pc.addTrack(
                    relay.subscribe(
                        video,
                        buffered=False,
                    )
                )

                print(
                    "STREETGO VIEWER VIDEO RELAY ATTACHED",
                    flush=True,
                )

            if audio is not None:
                pc.addTrack(
                    relay.subscribe(
                        audio,
                        buffered=False,
                    )
                )

                print(
                    "STREETGO VIEWER AUDIO RELAY ATTACHED",
                    flush=True,
                )

        answer = await pc.createAnswer()

        await pc.setLocalDescription(
            answer
        )

        await wait_for_ice(pc)

        if not pc.localDescription:
            raise HTTPException(
                status_code=500,
                detail=(
                    "WebRTC local description "
                    "was not created."
                ),
            )

        raw_answer = (
            pc.localDescription.sdp
        )

        print(
            "STREETGO RAW ANSWER MIDS:",
            get_answer_mids(raw_answer),
            flush=True,
        )

        print(
            "STREETGO RAW ANSWER BUNDLE:",
            get_bundle_mids(raw_answer),
            flush=True,
        )

        repaired_answer = repair_answer_sdp(
            offer.sdp,
            raw_answer,
        )

        validate_sdp_mids(
            repaired_answer
        )

        print(
            "STREETGO WEBRTC ANSWER READY",
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
            "STREETGO ANSWER LENGTH:",
            len(repaired_answer),
            flush=True,
        )

        print(
            "========================================",
            flush=True,
        )

        return {
            "success": True,
            "live_id": offer.live_id,
            "role": offer.role,
            "sdp": repaired_answer,
            "type": "answer",
        }

    except HTTPException:
        if (
            pc is not None
            and offer.live_id
        ):
            await cleanup_peer(
                offer.live_id,
                offer.role,
                pc,
            )
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

        if (
            pc is not None
            and offer.live_id
        ):
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