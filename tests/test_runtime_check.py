from __future__ import annotations

import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.runtime_check import check_daqmx, run_check


class RuntimeCheckTests(unittest.TestCase):
    def test_import_only_build_check_does_not_load_installed_driver(self):
        with patch('nidaqmx.system.System.local', side_effect=AssertionError('build check must not load NI driver')):
            result = check_daqmx(imports_only=True)
        self.assertTrue(result['loaded'])
        self.assertIsNone(result['driverVersion'])

    def test_packaged_entry_diagnostic_never_imports_live_backend(self):
        entry = Path(__file__).resolve().parents[1] / 'packaging' / 'tauri_bridge_entry.py'
        with patch.object(sys, 'argv', [str(entry), '--check-runtime', '--output', 'unused.json']), patch('tools.runtime_check.main', return_value=0) as check, patch.dict(sys.modules, {'tauri_bridge.__main__': None}):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(entry), run_name='__main__')
            self.assertEqual(stopped.exception.code, 0)
            check.assert_called_once_with()

    def test_legacy_driver_version_uses_api_not_dll_version(self):
        system = SimpleNamespace(driver_version=SimpleNamespace(major_version=9, minor_version=7, update_version=0))
        with patch('nidaqmx.system.System.local', return_value=system), patch('nidaqmx.Task', side_effect=AssertionError('diagnostic must not create tasks')):
            result = check_daqmx()
        self.assertEqual(result['driverVersion'], '9.7.0')
        self.assertTrue(result['loaded'])

    def test_failed_loading_is_reported_without_installing_or_claiming_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'runtime.json'
            with patch('tools.runtime_check.check_daqmx', return_value={'loaded': False, 'error': 'missing dependency'}), patch('tools.runtime_check.check_serial_transport', return_value={'loaded': True}):
                self.assertEqual(run_check(output), 1)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertFalse(report['hardwareAcceptancePassed'])
            self.assertFalse(report['legacyApplicationAcceptancePassed'])
            self.assertFalse(report['daqmx']['loaded'])
