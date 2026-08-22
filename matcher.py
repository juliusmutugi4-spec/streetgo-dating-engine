def calculate_match(user, person):

    score = 0
    reasons = []


    # ==========================
    # AGE COMPATIBILITY (20%)
    # ==========================

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



    # ==========================
    # INTERESTS (40%)
    # ==========================

    user_interests = set(
        user.get("interests") or []
    )


    person_interests = set(
        person.get("interests") or []
    )


    shared = user_interests.intersection(
        person_interests
    )


    if shared:

        interest_score = min(
            len(shared) * 25,
            40
        )


        score += interest_score


        reasons.append(
            f"Shared interests: {', '.join(shared)}"
        )



    # ==========================
    # PERSONALITY (20%)
    # ==========================

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



    # ==========================
    # RELATIONSHIP GOAL (10%)
    # ==========================

    if (
        user.get("looking_for")
        ==
        person.get("looking_for")
    ):

        score += 10

        reasons.append(
            "Same relationship goal"
        )



    # ==========================
    # REPUTATION / TRUST (10%)
    # ==========================

    reputation = person.get(
        "reputation",
        0
    )


    if reputation >= 20:

        score += 10

        reasons.append(
            "Trusted StreetGO user"
        )


    elif reputation > 0:

        score += 5



    # ==========================
    # PROFILE COMPLETENESS BONUS
    # ==========================

    complete_fields = 0


    for field in [
        "age",
        "gender",
        "interests",
        "personality",
        "looking_for"
    ]:

        if person.get(field):

            complete_fields += 1



    if complete_fields == 5:

        score += 10

        reasons.append(
            "Complete dating profile"
        )



    # ==========================
    # FINAL RESULT
    # ==========================

    return {

        "match_score": min(
            score,
            100
        ),

        "reasons": reasons

    }