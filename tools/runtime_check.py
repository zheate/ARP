"""Packaged-runtime diagnostic; never starts the product backend or opens devices."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path


def check_daqmx(*, imports_only: bool = False) -> dict:
    try:
        from nidaqmx.system import System

        if imports_only:
            return {"loaded": True, "driverVersion": None, "scope": "Python imports only; installed driver not loaded or queried."}
        # Only the driver version API. No device enumeration, Task or channel.
        version = System.local().driver_version
        return {
            "loaded": True,
            "driverVersion": f"{version.major_version}.{version.minor_version}.{version.update_version}",
            "scope": "Driver version query only; acquisition compatibility untested.",
        }
    except Exception as exc:
        return {"loaded": False, "errorType": type(exc).__name__, "error": str(exc)}


def check_serial_transport() -> dict:
    try:
        from tools.serial_visa import create_serial_resource_manager

        manager = create_serial_resource_manager()
        try:
            return {"loaded": True, "backend": type(manager.visalib).__name__, "portsOpened": 0, "discoveryPerformed": False}
        finally:
            manager.close()
    except Exception as exc:
        return {"loaded": False, "errorType": type(exc).__name__, "error": str(exc)}


def run_check(output: Path, *, imports_only: bool = False) -> int:
    results = {
        "schemaVersion": 1,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "pointerBits": 64 if sys.maxsize > 2**32 else 32,
        "frozen": bool(getattr(sys, "frozen", False)),
        "driverQueryRequested": not imports_only,
        "scope": "Actual packaged Python/serial loading and installed DAQmx version API. No device enumeration, DAQ task, instrument connection, driver installation or upgrade.",
        "daqmx": check_daqmx(imports_only=imports_only),
        "serial": check_serial_transport(),
        "hardwareAcceptancePassed": False,
        "legacyApplicationAcceptancePassed": False,
    }
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Runtime report: {output}")
    return 0 if results["daqmx"]["loaded"] and results["serial"]["loaded"] else 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--imports-only", action="store_true", help="Validate packaged dependencies without loading the installed DAQmx driver")
    args = parser.parse_args()
    return run_check(args.output, imports_only=args.imports_only)


if __name__ == "__main__":
    raise SystemExit(main())
