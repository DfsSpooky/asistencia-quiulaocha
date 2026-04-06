import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent.parent
TRUE_VALUES = {"1", "true", "yes", "on"}


def load_environment():
    env_file = os.environ.get("ENV_FILE")
    env_path = Path(env_file) if env_file else BASE_DIR / ".env"

    if load_dotenv and env_path.exists():
        load_dotenv(env_path, override=False)

    return env_path


def get_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def get_csv(name, default=""):
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def get_first(*names, default=None):
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


def resolve_path(value, *, base_dir=BASE_DIR):
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path
