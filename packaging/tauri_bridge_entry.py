"""PyInstaller entry point for the packaged Tauri Python sidecar."""

from tauri_bridge.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
