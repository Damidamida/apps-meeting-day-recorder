import subprocess
import sys
from typing import Any


def hidden_process_kwargs() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not create_no_window:
        return {}

    return {"creationflags": create_no_window}
