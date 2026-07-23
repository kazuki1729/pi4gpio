import datetime
import unittest
from unittest import mock

from scripts import analyze_direct_journal


class DirectJournalBaselineTest(unittest.TestCase):
    def test_extract_and_analyze_payloads(self):
        lines = []
        start = datetime.datetime(2026, 7, 20, 12, 0, 0)
        for index in range(4):
            timestamp = start + datetime.timedelta(seconds=10 * index)
            co2 = "null" if index == 2 else "400"
            lines.append(
                "prefix 送信準備: "
                + "{"
                + f'"timestamp":"{timestamp}","trigger":"timer",'
                + '"light":{"raw":1,"voltage":0.1},'
                + '"sound":{"raw":2},"joystick":{"x":0,"y":0},'
                + '"potentiometer":{"percent":50},'
                + '"dht22":{"temp":20,"hum":40},'
                + '"bme280":{"pres":1000},'
                + f'"mh_z19c":{{"co2":{co2}}}'
                + "}"
            )

        payloads = analyze_direct_journal.extract_payloads(lines)
        result = analyze_direct_journal.analyze(payloads, 24.0)

        self.assertEqual(result["records"]["total"], 4)
        self.assertEqual(result["timer_interval_sec"]["average"], 10.0)
        self.assertEqual(result["sensor_valid_records"]["dht22"], 4)
        self.assertEqual(result["sensor_valid_records"]["mh_z19c"], 3)
        self.assertEqual(result["sensor_success_percent"]["mh_z19c"], 75.0)

    def test_failure_summary_counts_sensor_and_network_errors(self):
        result = analyze_direct_journal.failure_summary(
            [
                "[失敗したセンサ]: RobustDHT22, MHZ19C",
                "[失敗したセンサ]: BME280Sensor",
                "通信エラー: timeout",
            ]
        )
        self.assertEqual(result["cycles_with_sensor_failure"], 2)
        self.assertEqual(result["dht22_mentions"], 1)
        self.assertEqual(result["bme280_mentions"], 1)
        self.assertEqual(result["mh_z19c_mentions"], 1)
        self.assertEqual(result["network_error_lines"], 1)

    @mock.patch("scripts.analyze_direct_journal.subprocess.run")
    def test_journal_reader_uses_exact_requested_window(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

        analyze_direct_journal.read_journal_lines(
            "sensor-tiered-client.service", 24.0
        )

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--since") + 1], "-24 hours")


if __name__ == "__main__":
    unittest.main()
