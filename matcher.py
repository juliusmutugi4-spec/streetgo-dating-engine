def calculate_match(user, person):

    score = 0
    reasons = []


    mode = (
        person.get("profile_mode")
        or "dating"
    ).lower()



    # =====================================
    # AGE COMPATIBILITY
    # =====================================

    if user.get("age") and person.get("age"):

        difference = abs(
            user["age"] - person["age"]
        )


        if difference <= 2:

            score += 20

            reasons.append(
                "Very similar age"
            )


        elif difference <= 5:

            score += 15

            reasons.append(
                "Similar age"
            )


        elif difference <= 10:

            score += 5



    # =====================================
    # SHARED INTERESTS
    # =====================================

    user_interests = set(
        user.get("interests") or []
    )


    person_interests = set(
        person.get("interests") or []
    )


    shared_interests = (
        user_interests
        .intersection(
            person_interests
        )
    )


    if shared_interests:


        interest_score = min(
            len(shared_interests) * 20,
            40
        )


        score += interest_score


        reasons.append(
            "Shared interests: "
            +
            ", ".join(
                shared_interests
            )
        )



    # =====================================
    # DATING MODE
    # =====================================

    if mode == "dating":


        if (
            user.get("personality")
            and
            user.get("personality")
            ==
            person.get("personality")
        ):

            score += 20

            reasons.append(
                "Similar personality"
            )



        if (
            user.get("looking_for")
            and
            user.get("looking_for")
            ==
            person.get("looking_for")
        ):

            score += 10

            reasons.append(
                "Same relationship goal"
            )




    # =====================================
    # BUSINESS MODE
    # =====================================

    elif mode == "business":


        user_skills = set(
            user.get("skills") or []
        )


        person_skills = set(
            person.get("skills") or []
        )


        shared_skills = (
            user_skills
            .intersection(
                person_skills
            )
        )


        if shared_skills:


            score += 30


            reasons.append(
                "Shared skills: "
                +
                ", ".join(
                    shared_skills
                )
            )



        if (
            user.get("profession")
            and
            user.get("profession")
            ==
            person.get("profession")
        ):


            score += 20


            reasons.append(
                "Similar profession"
            )




    # =====================================
    # JOB MODE
    # =====================================

    elif mode == "job":


        user_skills = set(
            user.get("skills") or []
        )


        person_skills = set(
            person.get("skills") or []
        )


        matching_skills = (
            user_skills
            .intersection(
                person_skills
            )
        )


        if matching_skills:


            score += 40


            reasons.append(
                "Matching skills: "
                +
                ", ".join(
                    matching_skills
                )
            )



        if person.get("experience"):


            score += 10


            reasons.append(
                "Professional experience available"
            )



        if person.get("education"):


            score += 10


            reasons.append(
                "Education profile completed"
            )




    # =====================================
    # TRUST SCORE
    # =====================================

    reputation = (
        person.get("reputation")
        or 0
    )


    if reputation >= 20:


        score += 10


        reasons.append(
            "Trusted StreetGO user"
        )


    elif reputation > 0:


        score += 5




    # =====================================
    # PROFILE COMPLETION
    # =====================================

    required_fields = [

        "age",
        "gender",
        "interests",
        "profile_mode"

    ]


    completed = 0


    for field in required_fields:


        if person.get(field):

            completed += 1



    if completed == len(required_fields):


        score += 10


        reasons.append(
            "Complete StreetGO profile"
        )




    # =====================================
    # MODE LABEL
    # =====================================


    if mode == "dating":


        reasons.insert(
            0,
            "❤️ Dating compatibility"
        )


    elif mode == "business":


        reasons.insert(
            0,
            "💼 Business networking match"
        )


    elif mode == "job":


        reasons.insert(
            0,
            "🎯 Career opportunity match"
        )




    return {


        "match_score":
            min(
                score,
                100
            ),


        "reasons":
            reasons,


        "profile_mode":
            mode

    }