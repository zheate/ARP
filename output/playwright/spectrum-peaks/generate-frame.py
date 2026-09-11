import json
from pathlib import Path
from tempfile import TemporaryDirectory
from combined_test.devices import SpectrometerReaderThread
from combined_test.models import SpectrometerSettings
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
with TemporaryDirectory() as temporary:
    backend = HeadlessBackend(JsonSettings(Path(temporary) / 'settings.json'))
    station = backend.stations['1']
    reader = SpectrometerReaderThread(SpectrometerSettings(10000, 100, average_frames=1))
    station.spectrometer_reader = reader
    reader.reading.connect(station._on_spectrometer_reading)
    wavelength = [420.1 + i * 0.267 for i in range(188)]
    intensity = [float((i % 5) - 2) for i in range(188)]
    intensity[108:119] = [10, 22, 47, 150, 400, 620, 615, 480, 125, 35, 12]
    reader._publish_averaged_spectrum(wavelength, intensity)
    measurement = backend.snapshot({'stationId':'1', 'view':'manual'})['measurements']
    Path('output/playwright/spectrum-peaks/frame.json').write_text(json.dumps(measurement), encoding='utf-8')
    print(json.dumps(measurement['spectrumPeaks']))
    backend.export_worker.graceful_stop()
