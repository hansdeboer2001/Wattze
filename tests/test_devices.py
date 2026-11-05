"""Tests for BLE device classification used by the start screen."""
import unittest

from wattze.ble_wattze import classify_device, FTMS_SVC
from wattze.state import CLICK_ASYNC, HR_SVC


class ClassifyDeviceTests(unittest.TestCase):
    def test_click_by_name(self):
        self.assertEqual(classify_device("Zwift Click", []), "click")

    def test_click_by_uuid(self):
        self.assertEqual(classify_device("Remote", [CLICK_ASYNC]), "click")

    def test_click_is_not_trainer(self):
        self.assertNotEqual(classify_device("Zwift Click", []), "trainer")

    def test_trainer_by_name(self):
        self.assertEqual(classify_device("KICKR CORE", []), "trainer")

    def test_trainer_by_ftms_uuid(self):
        self.assertEqual(classify_device("Tacx NEO", [FTMS_SVC]), "trainer")

    def test_hr_by_uuid(self):
        self.assertEqual(classify_device("Polar H10", [HR_SVC]), "hr")

    def test_hr_by_name(self):
        self.assertEqual(classify_device("Coospo H808S", []), "hr")

    def test_unknown(self):
        self.assertEqual(classify_device("Speaker", []), "other")

    def test_empty(self):
        self.assertEqual(classify_device("", []), "other")


if __name__ == "__main__":
    unittest.main()
