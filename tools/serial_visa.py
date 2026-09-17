"""Bundled VISA serial transport for PCs without a native VISA runtime.

Only local ASRL ports are supported. Resource listing never invokes the
PyVISA-py network, USB or GPIB discovery implementations.
"""

from __future__ import annotations

from pyvisa import ResourceManager, constants, errors, rname
from pyvisa_py.highlevel import PyVisaLibrary
from pyvisa_py.serial import SerialSession


class SerialVisaLibrary(PyVisaLibrary):
    def list_resources(self, session, query="?*::INSTR"):
        if session not in self.sessions:
            raise errors.VisaIOError(constants.StatusCode.error_invalid_object)
        return rname.filter(SerialSession.list_resources(), query)

    def open(
        self, session, resource_name,
        access_mode=constants.AccessModes.no_lock,
        open_timeout=constants.VI_TMO_IMMEDIATE,
    ):
        # Windows COM numbers only: never pass URL-like serial names through
        # PySerial's serial_for_url dispatcher.
        import re

        if re.fullmatch(r"ASRL[0-9]+::INSTR", resource_name, re.IGNORECASE) is None:
            raise errors.VisaIOError(constants.StatusCode.error_invalid_resource_name)
        return super().open(session, resource_name, access_mode, open_timeout)


def create_serial_resource_manager() -> ResourceManager:
    return ResourceManager(SerialVisaLibrary("arp-local-serial"))
