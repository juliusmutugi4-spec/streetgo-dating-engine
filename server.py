from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from supabase import create_client
from dotenv import load_dotenv

from matcher import calculate_match
from live.routes import router as live_router
from live.webrtc import router as webrtc_router

import os


# =========================================================
# ENV
# =========================================================

load_dotenv()


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="StreetGO Dating Engine"
)


# =========================================================
# ROUTERS
# =========================================================

app.include_router(live_router)
app.include_router(webrtc_router)


# =========================================================
# CORS
# =========================================================
# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",

        # Production
        "https://streetgo.app",
        "https://www.streetgo.app",
    ],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

    expose_headers=["*"],
)


@app.middleware("http")
async def debug_requests(request, call_next):

    print(
        "REQUEST:",
        request.method,
        request.url.path
    )

    response = await call_next(request)

    print(
        "RESPONSE:",
        response.status_code
    )

    return response



# =========================================================
# SUPABASE
# =========================================================

SUPABASE_URL = os.getenv(
    "SUPABASE_URL"
)

SUPABASE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY"
)


print(
    "URL:",
    SUPABASE_URL
)

print(
    "KEY:",
    bool(SUPABASE_KEY)
)


if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception(
        "Missing Supabase keys"
    )


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    return {
        "status": "online",
        "service": "StreetGO Dating Engine",
    }


# =========================================================
# TEST PROFILES
# =========================================================

@app.get("/test-profiles")
def test_profiles():

    try:

        result = (
            supabase
            .table("profiles")
            .select("*")
            .limit(10)
            .execute()
        )

        return {
            "success": True,
            "count": len(result.data),
            "profiles": result.data,
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e),
        }


# =========================================================
# DATING USERS
# =========================================================

@app.get("/dating-users")
def dating_users():

    try:

        result = (
            supabase
            .table("profiles")
            .select(
                """
                id,
                username,
                avatar_url,
                age,
                gender,
                interests,
                personality,
                looking_for,
                reputation,
                dating_active,
                profile_mode,
                headline,
                profession,
                skills,
                experience,
                education,
                location
                """
            )
            .eq(
                "dating_active",
                True
            )
            .execute()
        )

        return {
            "success": True,
            "users": result.data,
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e),
        }


# =========================================================
# MATCH ENGINE
# =========================================================

@app.get("/matches/{user_id}")
def get_matches(
    user_id: str
):

    try:

        response = (
            supabase
            .table("profiles")
            .select("*")
            .eq(
                "dating_active",
                True
            )
            .execute()
        )

        users = response.data

        current_user = None

        for user in users:

            if user["id"] == user_id:

                current_user = user

                break

        if not current_user:

            return {
                "error": "User not found"
            }

        matches = []

        for person in users:

            if person["id"] != user_id:

                result = calculate_match(
                    current_user,
                    person
                )

                matches.append({

                    "id": person["id"],

                    "name": person.get(
                        "username",
                        "Unknown"
                    ),

                    "avatar": person.get(
                        "avatar_url"
                    ),

                    # =====================================
                    # STREETGO PROFILE DATA
                    # =====================================

                    "profileType": (
                        person.get(
                            "profile_mode",
                            "dating"
                        ).capitalize()
                    ),

                    "headline": (
                        person.get(
                            "headline"
                        )
                        or
                        "Building meaningful connections"
                    ),

                    "profession": person.get(
                        "profession"
                    ),

                    "skills": person.get(
                        "skills",
                        []
                    ),

                    "experience": person.get(
                        "experience"
                    ),

                    "education": person.get(
                        "education"
                    ),

                    "location": (
                        person.get(
                            "location"
                        )
                        or
                        "Nairobi, Kenya"
                    ),

                    # =====================================
                    # MATCH RESULT
                    # =====================================

                    "score": result[
                        "match_score"
                    ],

                    "reasons": result[
                        "reasons"
                    ],
                })

        matches.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        return {

            "user": current_user.get(
                "username"
            ),

            "matches": matches,
        }

    except Exception as e:

        return {
            "error": str(e)
        }