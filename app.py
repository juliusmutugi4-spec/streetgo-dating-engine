from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from profiles import get_all_profiles, get_profile
from matcher import calculate_match

from live.routes import router as live_router
from live.webrtc import router as webrtc_router


app = FastAPI(
    title="StreetGO Dating Match Engine",
    version="1.0.0",
)


# =========================================================
# ROUTERS
# =========================================================

app.include_router(live_router)
app.include_router(webrtc_router)


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/")
def health_check():
    return {
        "status": "online",
        "service": "StreetGO Dating Match Engine",
    }


# =========================================================
# GET MATCHES
# =========================================================

@app.get("/matches/{user_id}")
def get_matches(user_id: str):

    # Get current user
    current_user = get_profile(user_id)

    if not current_user:
        raise HTTPException(
            status_code=404,
            detail="User profile not found",
        )

    # Get all active dating profiles
    profiles = get_all_profiles()

    matches = []

    for person in profiles:

        # Never match user with themselves
        if person.get("id") == user_id:
            continue

        # Only dating profiles
        profile_mode = (
            person.get("profile_mode")
            or "dating"
        ).lower()

        if profile_mode != "dating":
            continue

        # Calculate compatibility
        result = calculate_match(
            current_user,
            person,
        )

        matches.append({

            "id":
                person.get("id"),

            "name":
                person.get("name")
                or person.get("username")
                or "StreetGO User",

            "avatar":
                person.get("avatar_url"),

            "score":
                result.get(
                    "match_score",
                    0,
                ),

            "reasons":
                result.get(
                    "reasons",
                    [],
                ),

            "headline":
                person.get(
                    "headline",
                ),

            "location":
                person.get(
                    "location",
                ),

            "age":
                person.get(
                    "age",
                ),

            "gender":
                person.get(
                    "gender",
                ),

            "interests":
                person.get(
                    "interests",
                ) or [],

            "reputation":
                person.get(
                    "reputation",
                ) or 0,

            "profileType":
                "Dating",

            "isOnline":
                person.get(
                    "is_online",
                    False,
                ),

        })

    # Highest compatibility first
    matches.sort(
        key=lambda match:
            match["score"],
        reverse=True,
    )

    return {
        "user_id": user_id,
        "matches": matches,
        "count": len(matches),
    }