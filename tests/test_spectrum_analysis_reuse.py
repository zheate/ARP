"""Public-contract checks for sharing a single frame between PIB and SMSR."""
import math
import struct
import unittest
from unittest.mock import patch

from combined_test import spectrum_math
from combined_test.native_realtime import NativeRealtimeUnavailable


def bits(value):
    if isinstance(value, float):
        return struct.pack(">d", value)
    if hasattr(value, "__dataclass_fields__"):
        return tuple(bits(getattr(value, name)) for name in value.__dataclass_fields__)
    if isinstance(value, tuple):
        return tuple(bits(item) for item in value)
    return value


class SharedSpectrumMetricsTests(unittest.TestCase):
    def setUp(self):
        self.x = [950.0 + i * 0.1 for i in range(601)]
        self.y = [75 + 40000 * math.exp(-0.5 * ((x - 976.1) / 0.32) ** 2)
                  + 4000 * math.exp(-0.5 * ((x - 977.3) / 0.2) ** 2) for x in self.x]

    def expected(self, x, y, **params):
        return spectrum_math.calculate_pib(x, y, **params), spectrum_math.calculate_smsr(x, y)

    def test_generators_are_consumed_once_and_inputs_keep_every_sample(self):
        original = self.x.copy(), self.y.copy()
        expected = self.expected(self.x, self.y)
        result = spectrum_math.calculate_pib_and_smsr(iter(self.x), iter(self.y))
        self.assertEqual(bits(result), bits(expected))
        self.assertEqual((self.x, self.y), original)

    def test_custom_pib_band_does_not_change_smsr_band(self):
        params = {"center_nm": 977.0, "half_range_nm": 0.5}
        result = spectrum_math.calculate_pib_and_smsr(self.x, self.y, **params)
        self.assertEqual(bits(result), bits(self.expected(self.x, self.y, **params)))

    def test_empty_mismatched_and_nonfinite_inputs_match_independent_metrics(self):
        for x, y in [([], []), ([1.0], [1.0, 2.0]),
                     ([950.0, 976.0, 1010.0], [0.0, math.nan, 0.0]),
                     ([950.0, 976.0, math.inf], [0.0, 10.0, 0.0]),
                     ([975.0, 976.0], [1.0, 2.0])]:
            with self.subTest(x=x, y=y):
                self.assertEqual(bits(spectrum_math.calculate_pib_and_smsr(x, y)),
                                 bits(self.expected(x, y)))

    def test_native_failure_preserves_python_fallback_bits(self):
        class UnavailableCore:
            def calculate_pib(self, *args):
                raise NativeRealtimeUnavailable("fixture")
        with patch.object(spectrum_math, "get_native_realtime_core", return_value=None):
            expected = self.expected(self.x, self.y)
        with patch.object(spectrum_math, "get_native_realtime_core", return_value=UnavailableCore()):
            self.assertEqual(bits(spectrum_math.calculate_pib_and_smsr(self.x, self.y)), bits(expected))

    def test_invalid_pib_configuration_keeps_existing_error(self):
        with self.assertRaisesRegex(ValueError, "PIB"):
            spectrum_math.calculate_pib_and_smsr(self.x, self.y, half_range_nm=-1.0)


if __name__ == "__main__":
    unittest.main()
