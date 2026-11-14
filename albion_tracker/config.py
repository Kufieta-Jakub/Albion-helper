import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def database_url() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return "postgresql://{u}:{p}@{h}:{port}/{d}".format(
        u=os.getenv("POSTGRES_USER", "albion"),
        p=os.getenv("POSTGRES_PASSWORD", "albion"),
        h=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        d=os.getenv("POSTGRES_DB", "albion_market"),
    )
