# Combined Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new combined test program that powers the device, reads power and wavelength, waits for power stability, then records output current, output voltage, power, and wavelength.

**Architecture:** Add a small pure-Python core module for stability detection and record formatting, plus a PySide6 GUI that lazily reuses the existing CH341, power meter, and spectrometer classes. Hardware imports stay inside worker code so the core tests can run without connected instruments.

**Tech Stack:** Python, PySide6, pyvisa, OceanDirect existing wrapper, CH341 existing Tkinter controller, `unittest`.

---

### Task 1: Core Logic

**Files:**
- Create: `combined_test_core.py`
- Create: `test_combined_test_core.py`

- [x] **Step 1: Write tests for power stability and record formatting**

Run: `python -m unittest test_combined_test_core.py -v`
Expected before implementation: tests fail because `combined_test_core` does not exist.

- [x] **Step 2: Implement minimal core functions**

Implement `PowerStabilityDetector`, `decode_i2c_value`, `build_set_current_command`, `CombinedMeasurement`, and `record_to_row`.

- [x] **Step 3: Run tests**

Run: `python -m unittest test_combined_test_core.py -v`
Expected after implementation: all tests pass.

### Task 2: Combined GUI

**Files:**
- Create: `combined_test_mvp.py`

- [x] **Step 1: Create a combined PySide6 window**

The GUI includes CH341 parameters, power-meter resource and wavelength, spectrometer integration time, stability threshold/window, current setpoint, and CSV save path.

- [x] **Step 2: Create a worker thread**

The worker connects CH341, sets output current, opens the power meter and spectrometer, samples both devices, and emits live readings.

- [x] **Step 3: Record once stable**

When the stability detector reports stable, read output voltage and output current through CH341, write one CSV row, emit it to the GUI log, then continue monitoring without duplicating records until the user starts another run.

### Task 3: Verification

**Files:**
- Check: `combined_test_core.py`
- Check: `combined_test_mvp.py`
- Check: `test_combined_test_core.py`

- [x] **Step 1: Unit test core logic**

Run: `python -m unittest test_combined_test_core.py -v`

- [x] **Step 2: Compile Python files**

Run: `python -m py_compile combined_test_core.py combined_test_mvp.py`

- [ ] **Step 3: Hardware smoke test**

Run: `python combined_test_mvp.py` on the instrument PC with CH341, power meter, and Ocean Insight spectrometer connected.
