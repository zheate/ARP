from __future__ import annotations

import unittest
from contextlib import closing
from unittest.mock import patch

import pyvisa
import serial

from tools.serial_visa import create_serial_resource_manager
from tools.power_meter_mvp import configure_caihuang, configure_coherent, configure_laserpoint


class SerialVisaTests(unittest.TestCase):
    def test_listing_is_local_serial_only(self):
        with closing(create_serial_resource_manager()) as manager:
            with patch('pyvisa_py.serial.SerialSession.list_resources', return_value=['ASRL3::INSTR', 'ASRL12::INSTR']), patch('pyvisa_py.sessions.Session.iter_valid_session_classes', side_effect=AssertionError('must not discover network buses')):
                self.assertEqual(manager.list_resources(), ('ASRL3::INSTR', 'ASRL12::INSTR'))
                self.assertEqual(manager.list_resources('ASRL12::INSTR'), ('ASRL12::INSTR',))

    def test_nonlocal_and_nonserial_resources_never_open(self):
        with closing(create_serial_resource_manager()) as manager, patch('pyvisa_py.serial.serial.serial_for_url') as factory:
            for resource in ['TCPIP0::127.0.0.1::INSTR', 'GPIB0::1::INSTR', 'ASRLsocket://127.0.0.1:23::INSTR']:
                with self.subTest(resource=resource), self.assertRaises((pyvisa.errors.VisaIOError, ValueError)):
                    manager.open_resource(resource)
            factory.assert_not_called()

    def test_actual_virtual_serial_io_matches_device_framing(self):
        # Real PyVISA-py + PySerial loopback. No Windows COM port is opened.
        loop_factory = serial.serial_for_url
        for configure, baud, write_end, read_end in [
            (configure_caihuang, 9600, b'\r\n', b'\r\n'),
            (configure_coherent, 9600, b'\r', b'\r\n'),
            (configure_laserpoint, 38400, b':', b';'),
        ]:
            opened = []
            def virtual_port(url, **kwargs):
                self.assertEqual(url, 'COM999')
                port = loop_factory('loop://', **kwargs)
                opened.append(port)
                return port

            with self.subTest(configure=configure.__name__), patch('pyvisa_py.serial.serial.serial_for_url', side_effect=virtual_port), closing(create_serial_resource_manager()) as manager:
                with manager.open_resource('ASRL999::INSTR') as instrument:
                    configure(instrument, timeout_ms=50)
                    self.assertEqual(instrument.baud_rate, baud)
                    instrument.write('PING')
                    self.assertEqual(opened[0].read(4 + len(write_end)), b'PING' + write_end)
                    opened[0].write(b'1.234' + read_end)
                    self.assertEqual(instrument.read(), '1.234')
                    with self.assertRaises(pyvisa.errors.VisaIOError) as error:
                        instrument.read()
                    self.assertEqual(error.exception.error_code, pyvisa.constants.StatusCode.error_timeout)
                self.assertFalse(opened[0].is_open)


if __name__ == '__main__':
    unittest.main()
