"""カナリアが本番UARTへ二重アクセスしないことの回帰テスト。"""

import csv
import io
import sys
import types
import unittest
from unittest import mock

from scripts import canary_compare


class _FakeSensor:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def read(self):
        return (20.0, 50.0)

    def read_raw(self):
        return 123

    def read_xy(self, **kwargs):
        return (0.1, 0.2)

    def read_percentage(self):
        return 42.0

    def update(self):
        return False

    def close(self):
        return None


class CanaryNoUartTest(unittest.TestCase):
    def test_build_sensors_does_not_require_or_construct_mhz19c(self):
        fake_module = types.ModuleType("rpi_sensors")
        for name in (
            "BME280Sensor",
            "GroveLightSensor",
            "GroveSoundSensor",
            "JoystickMCP3208",
            "PotentiometerMCP3208",
            "RobustDHT22",
            "TactileButton",
        ):
            setattr(fake_module, name, _FakeSensor)
        # 意図的にMHZ19C属性を用意しない。再びimportすればこのテストが失敗する。
        with mock.patch.dict(sys.modules, {"rpi_sensors": fake_module}):
            sensors = canary_compare.build_sensors()

        self.assertNotIn("mh_z19c", sensors["direct"])
        self.assertNotIn("mh_z19c", sensors["pi4gpio"])

    def test_run_once_records_uart_from_production_log_only(self):
        sensors = {
            "direct": {
                "bme280": _FakeSensor(),
                "grove_light": _FakeSensor(),
                "grove_sound": _FakeSensor(),
                "joystick": _FakeSensor(),
                "potentiometer": _FakeSensor(),
            },
            "pi4gpio": {
                "bme280": _FakeSensor(),
                "grove_light": _FakeSensor(),
                "grove_sound": _FakeSensor(),
                "joystick": _FakeSensor(),
                "potentiometer": _FakeSensor(),
                "tactile_button": _FakeSensor(),
                "robust_dht22": _FakeSensor(),
                # UARTセンサーは存在しない。参照すればKeyErrorでテストが失敗する。
            },
        }
        output = io.StringIO()
        writer = csv.writer(output)

        canary_compare.run_once(
            writer,
            sensors,
            {"mh_z19c": {"co2": 615}, "dht22": {"temp": 24.0, "hum": 55.0}},
        )

        rows = list(csv.reader(io.StringIO(output.getvalue())))
        uart_rows = [row for row in rows if row[1] == "mh_z19c_co2"]
        self.assertEqual(len(uart_rows), 1)
        self.assertEqual(uart_rows[0][2], "production_log")
        self.assertEqual(uart_rows[0][3], "True")
        self.assertEqual(uart_rows[0][4], "615")
        self.assertEqual(uart_rows[0][5], "")
        self.assertEqual(uart_rows[0][7], "615")

    def test_missing_production_uart_value_is_explicit_failure(self):
        sensors = {
            backend: {
                "bme280": _FakeSensor(),
                "grove_light": _FakeSensor(),
                "grove_sound": _FakeSensor(),
                "joystick": _FakeSensor(),
                "potentiometer": _FakeSensor(),
                **(
                    {
                        "tactile_button": _FakeSensor(),
                        "robust_dht22": _FakeSensor(),
                    }
                    if backend == "pi4gpio"
                    else {}
                ),
            }
            for backend in ("direct", "pi4gpio")
        }
        output = io.StringIO()
        canary_compare.run_once(csv.writer(output), sensors, {})

        uart_row = next(
            row
            for row in csv.reader(io.StringIO(output.getvalue()))
            if row[1] == "mh_z19c_co2"
        )
        self.assertEqual(uart_row[2], "production_log")
        self.assertEqual(uart_row[3], "False")
        self.assertEqual(uart_row[6], "production_reference_missing")


if __name__ == "__main__":
    unittest.main()
