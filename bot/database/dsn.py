import os
from pathlib import Path
from bot.misc import EnvKeys


def dsn() -> str:
    return os.getenv("DATABASE_URL") if Path("/.dockerenv").exists() else EnvKeys.DATABASE_URL
