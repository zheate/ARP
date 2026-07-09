# Local OceanDirect Unbind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop importing `application.models...` from scripts_runner for spectrometer access.

**Architecture:** Add project-local OceanDirect and spectrum math modules. Keep the existing `OceanSpectrometer` interface so `spectrometer_mvp.py` and `combined_test_mvp.py` need only narrow import and loader changes.

**Tech Stack:** Python, ctypes, NumPy-compatible iterables, unittest.

---

### Task 1: Local Spectrum Math

**Files:**
- Create: `spectrum_math.py`
- Test: `test_spectrum_math.py`

- [ ] Write failing tests for peak wavelength, centroid, FWHM, and empty/mismatched arrays.
- [ ] Implement pure local functions and `SpectrumStats`.
- [ ] Run `python -m unittest test_spectrum_math.py -v`.

### Task 2: Local OceanDirect Adapter

**Files:**
- Create: `ocean_direct_adapter.py`
- Test: `test_ocean_direct_adapter.py`

- [ ] Write failing tests with a fake OceanDirect API object for device discovery, open, integration time, wavelength, intensity, and close.
- [ ] Implement minimal ctypes wrapper with injectable API and default DLL path under `assets/libs/ocean_direct/OceanDirect.dll`.
- [ ] Run `python -m unittest test_ocean_direct_adapter.py -v`.

### Task 3: Switch Spectrometer Loading

**Files:**
- Modify: `spectrometer_mvp.py`
- Modify: `combined_test_mvp.py`
- Modify: `test_combined_test_mvp.py`

- [ ] Change `spectrometer_mvp.py` to import local adapter and local spectrum math.
- [ ] Change `load_spectrometer_components()` to load only local `spectrometer_mvp.py` and leave `sys.path` untouched.
- [ ] Keep scripts runner field accepted but ignored for backward-compatible UI/tests.
- [ ] Run `python -m unittest test_spectrum_math.py test_ocean_direct_adapter.py test_combined_test_core.py test_combined_test_mvp.py -v`.

