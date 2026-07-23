"""Pi4gpioセンサー試験ランナーの安全性テスト。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import pi4gpio_sensor_test


class SensorTestRunnerTest(unittest.TestCase):
    def test_interval_comes_from_environment(self):
        self.assertEqual(
            pi4gpio_sensor_test.interval_default(
                {"SENSOR_SEND_INTERVAL_SEC": "5"}
            ),
            5.0,
        )
        with self.assertRaises(ValueError):
            pi4gpio_sensor_test.interval_default(
                {"SENSOR_SEND_INTERVAL_SEC": "0"}
            )

    def test_dry_run_never_builds_sensors_or_writes_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "result.jsonl"
            with mock.patch.object(
                pi4gpio_sensor_test, "service_properties", return_value={"ActiveState": "active"}
            ), mock.patch.object(pi4gpio_sensor_test, "build_sensors") as build:
                result = pi4gpio_sensor_test.main(["--output", str(output)])
            self.assertEqual(result, 0)
            build.assert_not_called()
            self.assertFalse(output.exists())

    def test_execute_is_refused_while_production_is_active(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "result.jsonl"
            with mock.patch.object(
                pi4gpio_sensor_test,
                "service_properties",
                return_value={"ActiveState": "active"},
            ), mock.patch.object(pi4gpio_sensor_test, "build_sensors") as build:
                result = pi4gpio_sensor_test.main(
                    ["--execute", "--cycles", "1", "--output", str(output)]
                )
            self.assertEqual(result, 2)
            build.assert_not_called()
            self.assertFalse(output.exists())

    def test_one_cycle_records_metrics_without_network_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "result.jsonl"

            def properties(service):
                state = "inactive" if "sensor-tiered" in service else "active"
                return {"ActiveState": state, "MainPID": "123", "NRestarts": "0"}

            with mock.patch.object(
                pi4gpio_sensor_test, "service_properties", side_effect=properties
            ), mock.patch.object(
                pi4gpio_sensor_test, "build_sensors", return_value={}
            ), mock.patch.object(
                pi4gpio_sensor_test,
                "collect_sensor_values",
                return_value={"fake": {"ok": True, "latency_ms": 1.0}},
            ):
                result = pi4gpio_sensor_test.main(
                    ["--execute", "--cycles", "1", "--output", str(output)]
                )

            self.assertEqual(result, 0)
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(record["backend"], "pi4gpio")
            self.assertEqual(record["cycle"], 1)
            self.assertTrue(record["sensors"]["fake"]["ok"])


if __name__ == "__main__":
    unittest.main()
