from profiles import users
from matcher import calculate_match


current_user = users[0]


print(
    "Finding matches for:",
    current_user["name"]
)

print("----------------------")


for person in users:

    if person["id"] != current_user["id"]:

        result = calculate_match(
            current_user,
            person
        )


        print(
            person["name"],
            "❤️",
            result["match_score"],
            "%"
        )


        for reason in result["reasons"]:
            print(
                " -",
                reason
            )

        print("----------------------")