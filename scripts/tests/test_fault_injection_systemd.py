"""systemd障害注入ツールの安全弁と復旧判定のテスト。"""

import tempfile
import unittest
from pathlib import Path

from scripts.fault_injection_systemd import SafetyError, run_fault_injection


class _FakeSystemd:
    def __init__(self, active, pids=(100,), restarts=(0,)):
        self.active = set(active)
        self.pids = list(pids)
        self.restarts = list(restarts)
        self.kills = []

    def is_active(self, service):
        return service in self.active

    def property_int(self, service, name):
        values = self.pids if name == "MainPID" else self.restarts
        if len(values) > 1:
            return values.pop(0)
        return values[0]

    def kill_main(self, service):
        self.kills.append(service)


class SystemdFaultInjectionSafetyTest(unittest.TestCase):
    def test_dry_run_never_kills_even_when_week09_is_active(self):
        controller = _FakeSystemd(
            {"pi4gpio.service", "sensor-tiered-client.service"}
        )
        result = run_fault_injection(
            execute=False, controller=controller, probe=lambda _: None
        )
        self.assertFalse(result["would_execute"])
        self.assertEqual(controller.kills, [])

    def test_execute_is_refused_while_week09_is_active(self):
        controller = _FakeSystemd(
            {"pi4gpio.service", "sensor-tiered-client.service"}
        )
        with self.assertRaises(SafetyError):
            run_fault_injection(
                execute=True, controller=controller, probe=lambda _: None
            )
        self.assertEqual(controller.kills, [])

    def test_isolated_execute_verifies_pid_and_restart_counter_change(self):
        controller = _FakeSystemd(
            {"pi4gpio.service"}, pids=(100, 200), restarts=(0, 1)
        )
        probes = []
        with tempfile.TemporaryDirectory() as temp_dir:
            socket_marker = Path(temp_dir) / "pi4gpio.sock"
            socket_marker.touch()
            result = run_fault_injection(
                execute=True,
                socket_path=str(socket_marker),
                controller=controller,
                probe=lambda path: probes.append(path),
                sleeper=lambda _: None,
            )

        self.assertTrue(result["recovered"])
        self.assertEqual(result["after_pid"], 200)
        self.assertEqual(result["after_restarts"], 1)
        self.assertEqual(controller.kills, ["pi4gpio.service"])
        self.assertEqual(len(probes), 2)


if __name__ == "__main__":
    unittest.main()
