from pathlib import Path
import sys


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative_path: str | Path) -> Path:
    base = Path(getattr(sys, "_MEIPASS", app_root()))
    return base / Path(relative_path)


def bundled_tool_path(tool_name: str) -> Path:
    return app_root() / "resources" / "ffmpeg" / tool_name
