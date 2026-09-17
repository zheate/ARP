"""Validate and stage a reviewed offline deployment lock (build machine only).

No network access, vendor discovery, trust-on-first-use hashes, or guessed silent
switches. The target uses Windows PowerShell and never needs this Python module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

REQUIRED = (
    "webview2", "ch341-i2c", "ch341-serial", "ch343-serial", "ocean-hr4",
    "laserpoint-ftdi", "ni-package-manager", "ni-runtime", "ophir", "coherent",
)
PNP = {"ch341-i2c", "ch341-serial", "ch343-serial", "ocean-hr4", "laserpoint-ftdi"}
KINDS = {"webview2", "pnp", "vendor"}
PROBES = {"file-version", "registry-version", "com", "driver-store"}
HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
VERSION = re.compile(r"\d+(?:\.\d+){1,3}\Z")
UNSAFE = re.compile(r"(?i)(https?://|ftp://|\\\\|invoke-webrequest|invoke-restmethod|downloadstring|downloadfile|start-bitstransfer|\b(?:winget|choco|curl|wget)\b)")
SCRIPT_NETWORK = re.compile(r"(?i)(https?://|ftp://|invoke-webrequest|invoke-restmethod|downloadstring|downloadfile|start-bitstransfer|\b(?:winget|choco|curl|wget)\b)")
RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z")


class ManifestError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def relative(value: Any) -> str:
    require(isinstance(value, str) and bool(value), "Empty/non-string payload path")
    value = value.replace("\\", "/")
    parts = value.split("/")
    require(not value.startswith("/") and all(p not in {"", ".", ".."} for p in parts), f"Unsafe relative path: {value}")
    require(not any(any(c in ':*?<>|\"' or ord(c) < 32 for c in p) or p.endswith((" ", ".")) or RESERVED.fullmatch(p) for p in parts), f"Invalid Windows path: {value}")
    return str(PurePosixPath(value))


def confined(root: Path, name: str) -> Path:
    require(not root.is_symlink() and not (hasattr(root, "is_junction") and root.is_junction()), "Reparse/symlink root rejected")
    result = root.joinpath(*relative(name).split("/"))
    require(result.resolve().is_relative_to(root.resolve()), f"Path escapes payload: {name}")
    current = result
    while current != root and current != current.parent:
        require(not current.is_symlink() and not (hasattr(current, "is_junction") and current.is_junction()), f"Reparse/symlink path rejected: {name}")
        current = current.parent
    return result


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    def unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    with path.open(encoding="utf-8-sig") as stream:
        result = json.load(stream, object_pairs_hook=unique)
    require(isinstance(result, dict), "Manifest must be an object")
    return result


def validate(lock: dict[str, Any]) -> None:
    require(lock.get("schemaVersion") == 1 and lock.get("architecture") == "x64", "Expected schemaVersion=1, architecture=x64")
    require(type(lock.get("minimumWindowsBuild")) is int and lock["minimumWindowsBuild"] >= 17763, "Pin the minimum supported Windows build (>=17763)")
    require(lock.get("approved") is True, "Deployment lock is NOT approved; review local vendor media/commands first")
    require(isinstance(lock.get("reviewer"), str) and bool(lock["reviewer"].strip()), "Missing release engineer review")
    files = lock.get("files")
    require(isinstance(files, list) and bool(files), "Missing pinned payload inventory")
    index: dict[str, dict[str, Any]] = {}
    for item in files:
        require(isinstance(item, dict), "Inventory entry must be an object")
        name = relative(item.get("path"))
        require(name.casefold() not in {"deployment.json", "application.json", "powertest.msi"}, f"Reserved payload metadata name: {name}")
        require(name.casefold() not in index, f"Duplicate/case-colliding payload file: {name}")
        require(HASH.fullmatch(str(item.get("sha256", ""))) is not None, f"Missing SHA-256: {name}")
        require(item["sha256"] != "0" * 64, f"Placeholder hash: {name}")
        require(isinstance(item.get("signatureSubject", ""), str), f"Invalid signer constraint: {name}")
        index[name.casefold()] = item
    def pinned(name: str) -> dict[str, Any]:
        key = relative(name).casefold()
        require(key in index, f"Unpinned payload reference: {name}")
        return index[key]
    require(isinstance(lock.get("archives", []), list), "Archives must be an array")
    for archive in lock.get("archives", []):
        require(isinstance(archive, dict), "Archive entry must be an object")
        path = relative(archive["path"])
        pinned(path)
        require(path.lower().endswith(".zip"), f"Only ZIP extraction is supported: {path}")
        relative(archive["destination"])
    components = lock.get("components")
    require(isinstance(components, list) and all(isinstance(c, dict) for c in components), "Missing/invalid component plan")
    require([c.get("id") for c in components] == list(REQUIRED), "Components must contain every supported device/runtime exactly once, in dependency order")
    for c in components:
        ident, kind = c["id"], c.get("kind")
        require(kind in KINDS, f"{ident}: unsupported installer kind")
        require(kind != "webview2" or ident == "webview2", f"{ident}: cannot substitute WebView2 for a device runtime")
        require(ident not in PNP or kind == "pnp", f"{ident}: must use PnP staging")
        require(ident != "webview2" or kind == "webview2", "WebView2 must use the offline standalone adapter")
        require(ident not in {"ni-package-manager", "ni-runtime", "ophir"} or kind == "vendor", f"{ident}: runtime verification is required")
        if kind == "pnp":
            require(isinstance(c.get("inf"), list) and bool(c["inf"]), f"{ident}: no INF files")
            for name in c["inf"]:
                pinned(name)
                require(name.lower().endswith(".inf"), f"{ident}: not an INF: {name}")
            continue
        name = relative(c.get("path"))
        entry = pinned(name)
        require(type(c.get("timeoutSeconds")) is int and 1 <= c["timeoutSeconds"] <= 14400, f"{ident}: invalid component timeout")
        require(isinstance(c.get("successExitCodes"), list) and 0 in c["successExitCodes"], f"{ident}: explicit success codes required")
        require(isinstance(c.get("rebootExitCodes"), list), f"{ident}: explicit reboot codes required")
        codes = c["successExitCodes"] + c["rebootExitCodes"]
        require(all(type(code) is int for code in codes), f"{ident}: non-integer exit code")
        require(not set(c["successExitCodes"]) & set(c["rebootExitCodes"]), f"{ident}: ambiguous exit code")
        require(1641 not in codes and 259 not in codes, f"{ident}: do not accept initiated reboot or PnP-only status")
        if kind == "webview2":
            require(Path(name).name == "MicrosoftEdgeWebView2RuntimeInstallerX64.exe", "Use the x64 standalone WebView2 installer, NOT a bootstrapper")
            require("Microsoft" in entry.get("signatureSubject", ""), "WebView2 publisher constraint is required")
            require(VERSION.fullmatch(str(c.get("minimumVersion", ""))) is not None, "Pin a tested minimum WebView2 version")
            continue
        require(c.get("launcher") in {"exe", "powershell64"}, f"{ident}: unsupported vendor launcher")
        require(name.lower().endswith(".ps1" if c["launcher"] == "powershell64" else ".exe"), f"{ident}: launcher/file mismatch")
        require(c["launcher"] != "exe" or bool(entry.get("signatureSubject")), f"{ident}: vendor executable must have a signer constraint")
        args = c.get("arguments")
        require(isinstance(args, list) and all(isinstance(a, str) and not UNSAFE.search(a) and not any(x in a for x in ["\x00", "\r", "\n", "{AppInstallDir}"]) for a in args), f"{ident}: arguments must be local and must not redirect system runtime into application path")
        require(c.get("unattendedVerified") is True and c.get("offlineVerified") is True and c.get("rebootSuppressionVerified") is True, f"{ident}: unverified silent/offline/reboot behavior")
        pinned(c.get("evidence"))
        probes = c.get("detect")
        require(isinstance(probes, list) and bool(probes), f"{ident}: installation needs real postcondition probes")
        for p in probes:
            require(isinstance(p, dict), f"{ident}: probe must be an object")
            require(p.get("kind") in PROBES, f"{ident}: unknown verification probe")
            if p["kind"] == "com":
                require(p.get("progId") == "OphirLMMeasurement.CoLMMeasurement", "Only the read-only Ophir COM activation probe is permitted")
                if "minimumVersion" in p:
                    require(VERSION.fullmatch(str(p["minimumVersion"])) is not None, f"{ident}: invalid COM server version")
                if "maximumVersion" in p:
                    require("minimumVersion" in p and VERSION.fullmatch(str(p["maximumVersion"])) is not None, f"{ident}: invalid maximum COM server version")
                continue
            if p["kind"] == "driver-store":
                inf = relative(p.get("inf"))
                pinned(inf)
                require(inf.lower().endswith(".inf"), f"{ident}: driver-store probe needs a pinned INF")
                continue
            require(VERSION.fullmatch(str(p.get("minimumVersion", ""))) is not None, f"{ident}: pin verification version")
            if "maximumVersion" in p:
                require(VERSION.fullmatch(str(p["maximumVersion"])) is not None, f"{ident}: invalid maximum version")
            if p["kind"] == "file-version":
                path = str(p.get("path", ""))
                require(re.match(r"^\{(System32|ProgramFiles|ProgramFilesX86|CommonProgramFiles)\}/", path) is not None, f"{ident}: runtime file probe must use a standard system location")
                relative(path.split("}/", 1)[1])
            else:
                require(p.get("view") in {32, 64} and str(p.get("key", "")).startswith("SOFTWARE\\") and bool(p.get("value")), f"{ident}: HKLM registry-version probe needs view/key/value")
        require(any(p.get("kind") in {"file-version", "registry-version"} or (p.get("kind") == "com" and "minimumVersion" in p) for p in probes), f"{ident}: require a versioned runtime probe, not COM activation alone")
        if ident == "ophir":
            require(any(p.get("kind") == "com" for p in probes), "Ophir needs 64-bit COM activation verification")
    require(isinstance(lock.get("licenses"), list) and bool(lock["licenses"]), "Redistributed components must include offline license text")
    for path in lock["licenses"]:
        pinned(path)


def verify_files(root: Path, lock: dict[str, Any]) -> None:
    for item in lock["files"]:
        path = confined(root, item["path"])
        require(path.is_file(), f"Missing offline payload: {item['path']}")
        require(sha256(path).lower() == item["sha256"].lower(), f"SHA-256 mismatch: {item['path']}")
        if path.suffix.lower() == ".ps1":
            text = path.read_text(encoding="utf-8-sig")
            require(not SCRIPT_NETWORK.search(text), f"Network-capable command/reference in target script: {item['path']}; review and replace with a local-only adapter")


def expand_archives(root: Path, lock: dict[str, Any]) -> None:
    """Extract only separately pinned members; refuse traversal and extra files."""
    index = {relative(i["path"]).casefold(): i for i in lock["files"]}
    expanded_bytes = 0
    for archive in lock.get("archives", []):
        path = confined(root, archive["path"])
        require(path.is_file() and sha256(path).lower() == index[relative(archive["path"]).casefold()]["sha256"].lower(), f"Archive missing/hash mismatch: {path.name}")
        dest = relative(archive["destination"])
        with zipfile.ZipFile(path) as z:
            seen: set[str] = set()
            for info in z.infolist():
                member = relative(info.filename.rstrip("/"))
                mode = info.external_attr >> 16
                require((mode & 0o170000) != 0o120000, f"Archive symlink: {member}")
                if info.is_dir():
                    continue
                target = relative(dest + "/" + member)
                key = target.casefold()
                require(key not in seen, f"Duplicate ZIP member: {target}")
                seen.add(key)
                require(key in index, f"Archive contains unreviewed file: {target}")
                expanded_bytes += info.file_size
                require(expanded_bytes <= 16 * 1024**3, "Archive expansion exceeds 16 GiB")
                require(info.file_size <= 2 * 1024**3, f"Oversized archive member: {target}")
                out = confined(root, target)
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as source, out.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                require(sha256(out).lower() == index[key]["sha256"].lower(), f"Extracted hash mismatch: {target}")


def prepare(source: Path, destination: Path, lock: dict[str, Any]) -> None:
    validate(lock)
    require(not destination.exists(), "Destination must be a fresh directory")
    require(not source.resolve().is_relative_to(destination.resolve()) and not destination.resolve().is_relative_to(source.resolve()), "Source and destination must be disjoint")
    expand_archives(source, lock)
    verify_files(source, lock)
    destination.mkdir(parents=True)
    for item in lock["files"]:
        source_file = confined(source, item["path"])
        target = confined(destination, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
    verify_files(destination, lock)
    (destination / "deployment.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    try:
        lock = read_json(args.lock)
        validate(lock)
        if args.source:
            require(args.destination is not None, "--source requires --destination")
            prepare(args.source, args.destination, lock)
        print("Offline deployment lock validated (NOT a Windows installation acceptance test).")
        return 0
    except (ManifestError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print(f"Offline deployment blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
