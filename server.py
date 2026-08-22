from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from supabase import create_client
from dotenv import load_dotenv

from matcher import calculate_match

import os



# Load .env
load_dotenv()



app = FastAPI(
    title="StreetGO Dating Engine"
)



# =========================
# CORS
# =========================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# =========================
# SUPABASE CONNECTION
# =========================

SUPABASE_URL = os.getenv(
    "SUPABASE_URL"
)


SUPABASE_KEY = os.getenv(
    "SUPABASE_SERVICE_ROLE_KEY"
)



print("URL:", SUPABASE_URL)
print("KEY:", bool(SUPABASE_KEY))



if not SUPABASE_URL or not SUPABASE_KEY:

    raise Exception(
        "Missing Supabase keys"
    )



supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)




# =========================
# HOME
# =========================

@app.get("/")
def home():

    return {
        "status": "online",
        "service": "StreetGO Dating Engine"
    }





# =========================
# TEST PROFILES
# =========================

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

            "profiles": result.data

        }


    except Exception as e:


        return {

            "success": False,

            "error": str(e)

        }





# =========================
# DATING USERS
# =========================

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
                age,
                gender,
                interests,
                personality,
                looking_for,
                reputation,
                dating_active
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

            "users": result.data

        }



    except Exception as e:


        return {

            "success": False,

            "error": str(e)

        }





# =========================
# MATCH ENGINE
# =========================

@app.get("/matches/{user_id}")
def get_matches(user_id: str):

    try:


        # Get active dating users

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

                    "score": result["match_score"],

                    "reasons": result["reasons"]

                })



        matches.sort(
            key=lambda x: x["score"],
            reverse=True
        )



        return {

            "user": current_user.get(
                "username"
            ),

            "matches": matches

        }



    except Exception as e:


        return {

            "error": str(e)

        }