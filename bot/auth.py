from pathlib import Path

from loguru import logger

_AUTH_FILE = Path("authorized_users.txt")

_authorized: set[int] = set()


def load() -> None:
    if not _AUTH_FILE.exists():
        return
    for line in _AUTH_FILE.read_text().splitlines():
        line = line.strip()
        if line.isdigit():
            _authorized.add(int(line))
    logger.info("Loaded {} authorized user(s)", len(_authorized))


def is_authorized(user_id: int) -> bool:
    return user_id in _authorized


def all_users() -> set[int]:
    return set(_authorized)


def authorize(user_id: int) -> None:
    if user_id in _authorized:
        return
    _authorized.add(user_id)
    with _AUTH_FILE.open("a") as f:
        f.write(f"{user_id}\n")
    logger.info("Authorized new user {}", user_id)
