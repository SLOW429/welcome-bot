import random

GENERAL_WELCOMES = [
    "ولكم حب نورت",
    "المز | ه دخل",
    "حي الله من جانا",
    "هلا هلا اجلط",
    "ولكم نورتنا"
]

SPECIAL_USER = "Wegza"
SPECIAL_WELCOME = "بلعب البخت وصلل"


def build_welcome(username: str) -> str:
    clean_name = username.strip().lstrip("@")

    if clean_name.lower() == SPECIAL_USER.lower():
        return f"@{clean_name} {SPECIAL_WELCOME}"

    msg = random.choice(GENERAL_WELCOMES)
    return f"@{clean_name} {msg}"