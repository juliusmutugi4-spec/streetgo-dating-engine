from supabase_client import supabase



def get_all_profiles():

    result = (
        supabase
        .table("profiles")
        .select("*")
        .eq(
            "dating_active",
            True
        )
        .execute()
    )


    return result.data



def get_profile(user_id):

    result = (
        supabase
        .table("profiles")
        .select("*")
        .eq(
            "id",
            user_id
        )
        .single()
        .execute()
    )


    return result.data