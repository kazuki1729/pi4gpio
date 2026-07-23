#!/usr/bin/env python3
"""week09 journalの送信payloadからdirect運転基準値を集計する。"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import subprocess
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MARKER = "送信準備: "


def extract_payloads(lines: Iterable[str]) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    for line in lines:
        marker_index = line.find(MARKER)
        if marker_index == -1:
            continue
        try:
            payload = json.loads(line[marker_index + len(MARKER) :])
            recorded = datetime.datetime.fromisoformat(payload["timestamp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        payload["_recorded"] = recorded
        payloads.append(payload)
    payloads.sort(key=lambda item: item["_recorded"])
    return payloads


def failure_summary(lines: Iterable[str]) -> Dict[str, int]:
    materialized = list(lines)
    failure_lines = [line for line in materialized if "[失敗したセンサ]" in line]
    return {
        "cycles_with_sensor_failure": len(failure_lines),
        "dht22_mentions": sum("RobustDHT22" in line for line in failure_lines),
        "bme280_mentions": sum("BME280Sensor" in line for line in failure_lines),
        "mh_z19c_mentions": sum("MHZ19C" in line for line in failure_lines),
        "network_error_lines": sum("通信エラー" in line for line in materialized),
    }


def _nested(payload: Mapping[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _valid(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, Mapping):
        return bool(value) and all(item is not None for item in value.values())
    return True


def _range(values: Sequence[Any]) -> Dict[str, Optional[float]]:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return {"min": None, "max": None, "average": None}
    return {
        "min": round(min(numeric), 3),
        "max": round(max(numeric), 3),
        "average": round(statistics.fmean(numeric), 3),
    }


def analyze(payloads: Sequence[Mapping[str, Any]], hours: float) -> Dict[str, Any]:
    if not payloads:
        raise ValueError("送信payloadが見つかりません")
    latest = max(item["_recorded"] for item in payloads)
    cutoff = latest - datetime.timedelta(hours=hours)
    selected = [item for item in payloads if item["_recorded"] >= cutoff]
    if not selected:
        raise ValueError("対象時間帯の送信payloadがありません")

    timer_times = [
        item["_recorded"] for item in selected if item.get("trigger") == "timer"
    ]
    intervals = [
        (later - earlier).total_seconds()
        for earlier, later in zip(timer_times, timer_times[1:])
    ]
    value_getters: Dict[str, Tuple[str, ...]] = {
        "light": ("light",),
        "sound": ("sound",),
        "joystick": ("joystick",),
        "potentiometer": ("potentiometer",),
        "dht22": ("dht22",),
        "bme280": ("bme280",),
        "mh_z19c": ("mh_z19c",),
    }
    valid_counts = {
        name: sum(_valid(_nested(item, *keys)) for item in selected)
        for name, keys in value_getters.items()
    }

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "sensor-tiered-client.service journal",
        "backend": "direct",
        "window": {
            "requested_hours": hours,
            "start": selected[0]["_recorded"].isoformat(sep=" "),
            "end": selected[-1]["_recorded"].isoformat(sep=" "),
            "actual_hours": round(
                (selected[-1]["_recorded"] - selected[0]["_recorded"]).total_seconds()
                / 3600.0,
                3,
            ),
        },
        "records": {
            "total": len(selected),
            "timer": sum(item.get("trigger") == "timer" for item in selected),
            "button": sum(item.get("trigger") == "button" for item in selected),
        },
        "timer_interval_sec": {
            "samples": len(intervals),
            "average": round(statistics.fmean(intervals), 3) if intervals else None,
            "minimum": min(intervals) if intervals else None,
            "maximum": max(intervals) if intervals else None,
            "outside_10s_plus_minus_0_5s": sum(
                abs(interval - 10.0) > 0.5 for interval in intervals
            ),
        },
        "sensor_valid_records": valid_counts,
        "sensor_success_percent": {
            name: round(count * 100.0 / len(selected), 3)
            for name, count in valid_counts.items()
        },
        "sensor_ranges": {
            "dht22_temp": _range([_nested(item, "dht22", "temp") for item in selected]),
            "dht22_humidity": _range(
                [_nested(item, "dht22", "hum") for item in selected]
            ),
            "pressure": _range(
                [_nested(item, "bme280", "pres") for item in selected]
            ),
            "co2": _range([_nested(item, "mh_z19c", "co2") for item in selected]),
        },
        "acquisition_duration": "not_recorded_by_current_client",
    }


def read_journal_lines(service: str, hours: float) -> List[str]:
    result = subprocess.run(
        [
            "journalctl",
            "-u",
            service,
            "--since",
            f"-{hours:g} hours",
            "--no-pager",
            "-o",
            "cat",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"journalctl exit={result.returncode}")
    return result.stdout.splitlines()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", default="sensor-tiered-client.service")
    parser.add_argument("--hours", type=float, default=24.0)
    args = parser.parse_args()
    if args.hours <= 0:
        parser.error("--hoursは0より大きい値が必要です")
    lines = read_journal_lines(args.service, args.hours)
    result = analyze(extract_payloads(lines), args.hours)
    result["journal_failures"] = failure_summary(lines)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
