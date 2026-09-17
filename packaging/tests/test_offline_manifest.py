"""Build-only tests use dummy bytes: they do NOT validate any vendor installer."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "offline_manifest.py"
spec = importlib.util.spec_from_file_location("arp_offline_manifest", MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture():
    lock = {"schemaVersion": 1, "architecture": "x64", "minimumWindowsBuild": 18363,
            "approved": True, "reviewer": "unit-test-only", "files": [], "archives": [],
            "components": [], "licenses": ["licenses/vendor.txt"]}
    data = {}
    def add(name, content=b"test-only", signer=""):
        data[name] = content
        lock["files"].append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "signatureSubject": signer})
    add("licenses/vendor.txt")
    add("evidence/verified.txt")
    for ident in m.REQUIRED:
        if ident in m.PNP:
            path = f"{ident}/driver.inf"
            add(path)
            c = {"id": ident, "kind": "pnp", "inf": [path]}
        elif ident == "webview2":
            path = "webview/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
            add(path, signer="Microsoft Corporation")
            c = {"id": ident, "kind": "webview2", "path": path, "minimumVersion": "120.0.0.0",
                 "timeoutSeconds": 600, "successExitCodes": [0], "rebootExitCodes": [3010]}
        else:
            path = f"{ident}/runtime.exe"
            add(path, signer="Reviewed Vendor")
            c = {"id": ident, "kind": "vendor", "path": path, "launcher": "exe", "arguments": [],
                 "timeoutSeconds": 3600, "successExitCodes": [0], "rebootExitCodes": [3010],
                 "unattendedVerified": True, "offlineVerified": True, "rebootSuppressionVerified": True,
                 "evidence": "evidence/verified.txt", "detect": [{"kind": "file-version", "path": "{System32}/test.dll", "minimumVersion": "1.0"}]}
            if ident == "ophir":
                c["detect"].append({"kind": "com", "progId": "OphirLMMeasurement.CoLMMeasurement"})
        lock["components"].append(c)
    return lock, data


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.lock, self.data = fixture()

    def rejects(self, change):
        change(self.lock)
        with self.assertRaises(m.ManifestError):
            m.validate(self.lock)

    def test_complete_reviewed_fixture(self):
        m.validate(self.lock)

    def test_field_windows_baseline_does_not_approve_unverified_payload(self):
        template = m.read_json(MODULE.parent / "offline-deployment.example.json")
        self.assertEqual(template["minimumWindowsBuild"], 18363)
        self.assertFalse(template["approved"])
        self.lock["minimumWindowsBuild"] = template["minimumWindowsBuild"]
        m.validate(self.lock)
        with self.assertRaisesRegex(m.ManifestError, "NOT approved"):
            m.validate(template)

    def test_template_is_not_release_ready(self):
        lock = m.read_json(MODULE.parent / "offline-deployment.example.json")
        with self.assertRaisesRegex(m.ManifestError, "NOT approved"):
            m.validate(lock)

    def test_missing_component(self):
        self.rejects(lambda x: x["components"].pop())

    def test_reordered_component(self):
        self.rejects(lambda x: x["components"].reverse())

    def test_duplicate_component(self):
        self.rejects(lambda x: x["components"].append(x["components"][0]))

    def test_unreviewed_vendor(self):
        for key in ["offlineVerified", "unattendedVerified", "rebootSuppressionVerified"]:
            with self.subTest(key=key):
                lock = copy.deepcopy(self.lock)
                lock["components"][-1][key] = False
                with self.assertRaises(m.ManifestError): m.validate(lock)

    def test_missing_probe(self):
        self.rejects(lambda x: x["components"][-1].update(detect=[]))

    def test_ophir_requires_com(self):
        self.rejects(lambda x: x["components"][-2]["detect"].pop())

    def test_registered_com_version_is_a_versioned_postcondition(self):
        ophir = self.lock["components"][-2]
        ophir["detect"] = [{"kind": "com", "progId": "OphirLMMeasurement.CoLMMeasurement", "minimumVersion": "10.4.0.0"}]
        m.validate(self.lock)
        del ophir["detect"][0]["minimumVersion"]
        with self.assertRaisesRegex(m.ManifestError, "versioned runtime probe"):
            m.validate(self.lock)

    def test_com_version_range_must_be_numeric_and_complete(self):
        for change in [{"minimumVersion": "v10.4"}, {"maximumVersion": "11.0"}, {"minimumVersion": "10.4", "maximumVersion": "latest"}]:
            lock = copy.deepcopy(self.lock)
            lock["components"][-2]["detect"][-1].update(change)
            with self.subTest(change=change), self.assertRaises(m.ManifestError):
                m.validate(lock)

    def test_vendor_driver_store_probe_requires_pinned_inf(self):
        ophir = self.lock["components"][-2]
        ophir["detect"].append({"kind": "driver-store", "inf": "ch341-i2c/driver.inf"})
        m.validate(self.lock)
        for path in ["missing.inf", "../driver.inf", "licenses/vendor.txt"]:
            ophir["detect"][-1]["inf"] = path
            with self.subTest(path=path), self.assertRaises(m.ManifestError):
                m.validate(self.lock)

    def test_vendor_switches_cannot_download(self):
        for arg in ["https://example.com/setup", r"\\server\setup", "Invoke-WebRequest", "{AppInstallDir}", "abc\nxyz"]:
            with self.subTest(arg=arg):
                lock = copy.deepcopy(self.lock)
                lock["components"][-1]["arguments"] = [arg]
                with self.assertRaises(m.ManifestError): m.validate(lock)

    def test_bootstrapper_is_rejected(self):
        self.rejects(lambda x: x["components"][0].update(path=x["components"][-1]["path"]))

    def test_signature_constraint_required(self):
        self.rejects(lambda x: x["files"][-1].update(signatureSubject=""))

    def test_runtime_never_accepts_pnp_or_reboot_initiated_code(self):
        for code in [259, 1641]:
            with self.subTest(code=code):
                lock = copy.deepcopy(self.lock)
                lock["components"][-1]["successExitCodes"].append(code)
                with self.assertRaises(m.ManifestError): m.validate(lock)

    def test_cannot_substitute_webview_for_coherent(self):
        self.rejects(lambda x: x["components"][-1].update(kind="webview2"))

    def test_vendor_requires_versioned_postcondition(self):
        self.rejects(lambda x: x["components"][-1].update(detect=[{"kind": "com", "progId": "OphirLMMeasurement.CoLMMeasurement"}]))

    def test_zero_hash(self):
        self.rejects(lambda x: x["files"][0].update(sha256="0" * 64))

    def test_case_collision(self):
        self.rejects(lambda x: x["files"].append({**x["files"][0], "path": x["files"][0]["path"].upper()}))

    def test_invalid_windows_paths(self):
        for name in ["../a", "/a", "C:/a", "a/../b", "a//b", "a:stream", "a/CON.txt", "a/NUL", "a/b. ", "a/b.", "a/."]:
            with self.subTest(name=name), self.assertRaises(m.ManifestError): m.relative(name)

    def test_reserved_metadata(self):
        self.rejects(lambda x: x["files"].append({"path": "Deployment.json", "sha256": "a" * 64}))

    def test_malformed_entry(self):
        self.rejects(lambda x: x["files"].append("bad"))

    def test_missing_license(self):
        self.rejects(lambda x: x.update(licenses=[]))

    def test_unknown_component_kind(self):
        self.rejects(lambda x: x["components"][-1].update(kind="download"))

    def write_payload(self, root):
        for name, content in self.data.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def test_stage_and_hash_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.write_payload(root)
            destination = Path(tmp) / "stage"
            m.prepare(root, destination, self.lock)
            self.assertTrue((destination / "deployment.json").is_file())
            m.verify_files(destination, self.lock)

    def test_missing_payload_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(m.ManifestError): m.verify_files(root, self.lock)
            self.write_payload(root)
            (root / self.lock["files"][0]["path"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(m.ManifestError, "mismatch"): m.verify_files(root, self.lock)

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real").mkdir()
            try: (root / "link").symlink_to(root / "real", target_is_directory=True)
            except OSError: self.skipTest("Symlink privilege not available")
            with self.assertRaises(m.ManifestError): m.confined(root, "link/a")

    def test_source_and_destination_must_be_disjoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_payload(root)
            with self.assertRaises(m.ManifestError): m.prepare(root, root / "dest", self.lock)

    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lock.json"
            path.write_text('{"approved":false,"approved":true}')
            with self.assertRaises(m.ManifestError): m.read_json(path)

    def test_archive_members_must_be_pinned_and_confined(self):
        for member in ["../escape", "unreviewed.inf"]:
            with self.subTest(member=member), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = root / "test.zip"
                with zipfile.ZipFile(archive, "w") as z: z.writestr(member, b"test")
                lock = copy.deepcopy(self.lock)
                lock["files"].append({"path": "test.zip", "sha256": m.sha256(archive)})
                lock["archives"] = [{"path": "test.zip", "destination": "expanded"}]
                with self.assertRaises(m.ManifestError): m.expand_archives(root, lock)

    def test_archive_member_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "test.zip"
            with zipfile.ZipFile(archive, "w") as z: z.writestr("file.inf", b"different")
            lock = copy.deepcopy(self.lock)
            lock["files"] += [{"path": "test.zip", "sha256": m.sha256(archive)}, {"path": "expanded/file.inf", "sha256": "a" * 64}]
            lock["archives"] = [{"path": "test.zip", "destination": "expanded"}]
            with self.assertRaisesRegex(m.ManifestError, "hash mismatch"): m.expand_archives(root, lock)

    def test_unlisted_files_are_not_shipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, destination = Path(tmp) / "src", Path(tmp) / "dest"
            self.write_payload(root)
            (root / "private-key.pem").write_text("must not ship")
            m.prepare(root, destination, self.lock)
            self.assertFalse((destination / "private-key.pem").exists())

    def test_target_scripts_have_no_download_commands(self):
        # Regression lint is not proof about opaque vendor processes/network IO.
        for path in (MODULE.parent / "offline").glob("*.ps1"):
            with self.subTest(path=path): self.assertIsNone(m.SCRIPT_NETWORK.search(path.read_text()))


if __name__ == "__main__": unittest.main()
