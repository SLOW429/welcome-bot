def normalize_username(username: str) -> str:
    return username.strip().lstrip('@').casefold()


def is_owner(username: str, owner_username: str) -> bool:
    return normalize_username(username) == normalize_username(owner_username)


def is_mod(username: str, mods: list[str]) -> bool:
    uname = normalize_username(username)
    return uname in {normalize_username(m) for m in mods}


def has_role(bot, username: str, role_name: str) -> bool:
    try:
        return bot.has_role(username, role_name)
    except Exception:
        return False


def is_staff(username: str, owner_username: str, mods: list[str]) -> bool:
    return is_owner(username, owner_username) or is_mod(username, mods)


def can_use_floors(bot, username: str, owner_username: str, mods: list[str]) -> bool:
    return is_staff(username, owner_username, mods) or has_role(bot, username, 'plus') or has_role(bot, username, 'elite')


def can_use_to(bot, username: str, owner_username: str, mods: list[str]) -> bool:
    return is_staff(username, owner_username, mods) or has_role(bot, username, 'elite')


def can_use_vip(bot, username: str, owner_username: str, mods: list[str], vip_users: list[str]) -> bool:
    uname = normalize_username(username)
    return (
        is_staff(username, owner_username, mods)
        or uname in {normalize_username(x) for x in vip_users}
        or has_role(bot, username, 'vip')
        or has_role(bot, username, 'plus')
        or has_role(bot, username, 'elite')
    )
