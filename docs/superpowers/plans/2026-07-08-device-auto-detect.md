# Device Auto Detect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic device discovery for power meters and OceanDirect spectrometers, with selectable devices when more than one is found.

**Architecture:** Keep hardware probing in `combined_test_mvp.py` and store selected devices in `CombinedTestSettings`. Add lightweight device option dataclasses and label helpers so selection behavior can be tested without hardware.

**Tech Stack:** Python, PySide6, pyvisa via existing `power_meter_mvp.py`, OceanDirect via existing `spectrometer_mvp.py`.

---

### Task 1: Selection Models

**Files:**
- Modify: `combined_test_mvp.py`
- Modify: `test_combined_test_mvp.py`

- [x] Add tests for power-meter and spectrometer option labels.
- [x] Implement dataclasses used as combo-box item data.

### Task 2: GUI Discovery

**Files:**
- Modify: `combined_test_mvp.py`

- [x] Replace manual power resource field with a combo box plus auto-detect button.
- [x] Add spectrometer combo box plus auto-detect button.
- [x] Use the selected power resource and selected spectrometer device id when starting the combined test.

### Task 3: Documentation

**Files:**
- Create: `agent.md`

- [x] Document the `sth_eb314` environment, scripts runner root, and device auto-detect behavior.

### Task 4: Verification

**Files:**
- Check: `combined_test_mvp.py`
- Check: `test_combined_test_mvp.py`

- [x] Run unit tests.
- [x] Run `py_compile` with a temporary pycache prefix.
