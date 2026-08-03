"""PyInstaller entry point for the packaged Tauri Python sidecar."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    if "--usb2xxx-worker" in sys.argv:
        from tools.usb2xxx_control import usb2xxx_worker_main

        raise SystemExit(usb2xxx_worker_main())
    from tauri_bridge.__main__ import main

    raise SystemExit(main())
