import json
import math
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from combined_test.record_store import SessionRecordStore, SessionStatus
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tests.test_session_journal import _record
from tests.test_data_browser import write_workbook

root = Path(tempfile.mkdtemp(prefix='arp-library-preview-'))
backend = HeadlessBackend(JsonSettings(root / 'settings.json'))
for index in range(55):
    store = SessionRecordStore(root / 'test-records' / 'station-1')
    store.begin_session(root / 'excel', f'H6B560{569 + index:03}', datetime(2026, 9, 17, 10) - timedelta(hours=index), test_station='封盖后' if index % 2 else '封盖前', mode='automatic', settings={'testTemperatureC': '25 °C', 'stationId': '1'})
    for step in range(1, 11):
        current = step * 2
        power = max(0, current-1.8)*(2.1+index*.015)
        store.queue(replace(_record(), current_a=current, power_w=power, voltage_v=9+step*.3+index*.02, efficiency=power/(current*(9+step*.3+index*.02)), peak_wavelength_nm=975.2+step*.09+index*.02, centroid_nm=975.15+step*.085+index*.02, fwhm_nm=.32+step*.006+index*.002, pib=.94+step*.003, smsr_db=math.nan if index==2 else 33-step*.15+index*.04))
    store.complete_session(SessionStatus.COMPLETED, 'preview', shutdown_confirmed=True)
write_workbook(root / 'imports' / 'import-success.xlsx', sn='IMPORT-001', extra_spectrum=True)
write_workbook(root / 'imports' / 'import-stopped.xlsx', status='aborted_safely')
print(json.dumps({'root': str(root), 'imports': str(root/'imports'), 'url': 'http://127.0.0.1:18763'}, ensure_ascii=False), flush=True)
class Handler(BaseHTTPRequestHandler):
    def response_headers(self):
        self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin', 'http://localhost:4173') if self.headers.get('Origin') in {'http://localhost:1420', 'http://localhost:4173'} else 'http://localhost:4173')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
    def do_OPTIONS(self):
        self.send_response(200); self.response_headers()
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        try:
            if data['method'] not in {'dataBrowser.list','dataBrowser.inspect','dataBrowser.import'}:
                raise ValueError('Preview only supports data library operations')
            result = backend.dispatcher.dispatch_file_task(data['method'], data.get('params', {}))
            self.send_response(200); self.response_headers(); self.wfile.write(json.dumps(result, ensure_ascii=False, allow_nan=False).encode())
        except Exception as exc:
            self.send_response(400); self.response_headers(); self.wfile.write(json.dumps({'error': str(exc)}).encode())
    def log_message(self, *args): pass
ThreadingHTTPServer(('127.0.0.1',18763),Handler).serve_forever()
