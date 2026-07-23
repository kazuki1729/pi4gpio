"""受動カナリアがハードウェアへアクセスしないことの回帰テスト。"""

import ast
import csv
import datetime
import glob
import inspect
import io
import unittest
from pathlib import Path
from unittest import mock

from scripts import canary_compare


class PassiveCanaryTest(unittest.TestCase):
    def test_module_does_not_import_sensor_or_hardware_libraries(self):
        tree = ast.parse(inspect.getsource(canary_compare))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        self.assertTrue(
            {"rpi_sensors", "lgpio", "spidev", "smbus2", "serial"}.isdisjoint(imported)
        )

    def test_run_once_uses_production_log_and_protocol_probe_only(self):
        payload = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "trigger": "button",
            "light": {"raw": 100, "voltage": 0.08},
            "sound": {"raw": 200},
            "joystick": {"x": 0.1, "y": -0.2},
            "potentiometer": {"percent": 42.0},
            "dht22": {"temp": 24.0, "hum": 55.0},
            "bme280": {"pres": 1012.3},
            "mh_z19c": {"co2": 615},
        }
        output = io.StringIO()
        canary_compare.run_once(
            csv.writer(output),
            payload,
            daemon_probe=lambda: (True, "responsive", 1.25, ""),
            production_probe=lambda: (True, "active", 0.5, ""),
        )

        rows = list(csv.reader(io.StringIO(output.getvalue())))
        self.assertEqual(len(rows), 11)
        sensor_rows = [
            row
            for row in rows
            if row[1]
            not in {
                "pi4gpiod_health",
                "production_log_health",
                "production_service_health",
            }
        ]
        self.assertTrue(all(row[2] == "production_log" for row in sensor_rows))
        self.assertTrue(all(row[3] == "True" for row in sensor_rows))
        production_health = next(
            row for row in rows if row[1] == "production_log_health"
        )
        self.assertEqual(production_health[3], "True")
        health = next(row for row in rows if row[1] == "pi4gpiod_health")
        self.assertEqual(health[2], "unix_socket")
        self.assertEqual(health[3], "True")
        self.assertEqual(health[4], "responsive")
        service_health = next(
            row for row in rows if row[1] == "production_service_health"
        )
        self.assertEqual(service_health[3], "True")
        self.assertEqual(service_health[4], "active")

    def test_missing_production_values_are_explicit_failures(self):
        output = io.StringIO()
        canary_compare.run_once(
            csv.writer(output),
            {},
            daemon_probe=lambda: (False, "", 2.0, "down"),
            production_probe=lambda: (True, "active", 0.5, ""),
        )
        rows = list(csv.reader(io.StringIO(output.getvalue())))

        failed_sensor_rows = [
            row
            for row in rows
            if row[1]
            not in {
                "pi4gpiod_health",
                "production_log_health",
                "production_service_health",
            }
        ]
        self.assertTrue(all(row[3] == "False" for row in failed_sensor_rows))
        self.assertTrue(
            all(
                row[6] == "production_reference_missing"
                for row in failed_sensor_rows
                if row[3] == "False"
            )
        )
        health = next(row for row in rows if row[1] == "pi4gpiod_health")
        self.assertEqual(health[3], "False")
        self.assertEqual(health[6], "down")

    def test_stale_production_payload_is_not_reported_as_healthy(self):
        stale_time = datetime.datetime.now() - datetime.timedelta(minutes=10)
        output = io.StringIO()
        canary_compare.run_once(
            csv.writer(output),
            {
                "timestamp": stale_time.isoformat(timespec="seconds"),
                "trigger": "timer",
                "light": {"raw": 1, "voltage": 0.1},
                "sound": {"raw": 2},
                "joystick": {"x": 0, "y": 0},
                "potentiometer": {"percent": 50},
                "dht22": {"temp": 20, "hum": 40},
                "bme280": {"pres": 1000},
                "mh_z19c": {"co2": 400},
            },
            daemon_probe=lambda: (True, "responsive", 1.0, ""),
            production_probe=lambda: (True, "active", 0.5, ""),
        )
        rows = list(csv.reader(io.StringIO(output.getvalue())))
        production_rows = [
            row for row in rows if row[2] in {"journal", "production_log"}
        ]
        self.assertTrue(all(row[3] == "False" for row in production_rows))
        self.assertTrue(
            all(row[6] == "production_reference_stale" for row in production_rows)
        )

    def test_inactive_production_service_marks_sensor_rows_failed(self):
        output = io.StringIO()
        canary_compare.run_once(
            csv.writer(output),
            {"timestamp": datetime.datetime.now().isoformat(timespec="seconds")},
            daemon_probe=lambda: (True, "responsive", 1.0, ""),
            production_probe=lambda: (
                False,
                "inactive",
                0.5,
                "production_service_inactive",
            ),
        )
        rows = list(csv.reader(io.StringIO(output.getvalue())))
        service_health = next(
            row for row in rows if row[1] == "production_service_health"
        )
        self.assertEqual(service_health[3], "False")
        production_rows = [row for row in rows if row[2] == "production_log"]
        self.assertTrue(all(row[3] == "False" for row in production_rows))
        self.assertTrue(
            all(row[6] == "production_service_inactive" for row in production_rows)
        )

    def test_private_device_preflight_rejects_visible_hardware(self):
        with mock.patch.object(glob, "glob", return_value=["/dev/gpiomem"]):
            with self.assertRaises(RuntimeError):
                canary_compare.verify_hardware_devices_hidden()

    def test_private_device_preflight_accepts_hidden_hardware(self):
        with mock.patch.object(glob, "glob", return_value=[]):
            canary_compare.verify_hardware_devices_hidden()

    def test_systemd_units_enforce_no_direct_device_access(self):
        repo_root = Path(__file__).resolve().parents[2]
        canary_unit = (repo_root / "systemd" / "canary-compare.service").read_text(
            encoding="utf-8"
        )
        exclusive_drop_in = (
            repo_root / "systemd" / "sensor-tiered-client-pi4gpio-exclusive.conf"
        ).read_text(encoding="utf-8")

        for config in (canary_unit, exclusive_drop_in):
            self.assertIn("PrivateDevices=true", config)
            self.assertIn("DevicePolicy=closed", config)
            self.assertIn("NoNewPrivileges=true", config)
            self.assertIn("ProtectKernelTunables=true", config)
            self.assertIn("ProtectKernelModules=true", config)
            self.assertIn("ProtectControlGroups=true", config)
        self.assertIn("--require-private-devices", canary_unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", canary_unit)
        self.assertIn("RPI_SENSOR_BACKEND=pi4gpio", exclusive_drop_in)


if __name__ == "__main__":
    unittest.main()
