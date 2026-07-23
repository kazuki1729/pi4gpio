#!/usr/bin/env python3
"""Pi4gpioセンサー統合試験ランナー。

既定動作はdry-runで、ハードウェアライブラリをimportせず、センサーも生成しない。
``--execute``を指定した場合でも、week09本番サービスが明示的にinactiveで、
pi4gpiodがactiveでなければ開始しない。測定値は本番サーバーへ送信せず、
ローカルJSONLへだけ記録する。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple


DEFAULT_PRODUCTION_SERVICE = "sensor-tiered-client.service"
DEFAULT_DAEMON_SERVICE = "pi4gpio.service"
DEFAULT_OUTPUT = "/home/pi/pi4gpio-test/results/sensor_test.jsonl"
DEFAULT_INTERVAL_SEC = 10.0
DEFAULT_DURATION_SEC = 60.0


class SafetyError(RuntimeError):
    """安全条件を満たさず、ハードウェア試験を拒否した場合の例外。"""


def positive_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}は正の数で指定してください") from exc
    if parsed <= 0:
        raise ValueError(f"{name}は0より大きい値が必要です")
    return parsed


def interval_default(environ: Optional[Mapping[str, str]] = None) -> float:
    source = os.environ if environ is None else environ
    return positive_float(
        source.get("SENSOR_SEND_INTERVAL_SEC", str(DEFAULT_INTERVAL_SEC)),
        "SENSOR_SEND_INTERVAL_SEC",
    )


def service_properties(service: str) -> Dict[str, str]:
    """systemd serviceの監視用プロパティを読み取る。"""
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                service,
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
                "--property=NRestarts",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc), "ActiveState": "unknown"}

    properties: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    if result.returncode != 0 and "error" not in properties:
        properties["error"] = result.stderr.strip() or f"exit={result.returncode}"
    properties.setdefault("ActiveState", "unknown")
    return properties


def process_snapshot() -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {"pid": os.getpid()}
    try:
        snapshot["fd_count"] = len(os.listdir("/proc/self/fd"))
    except OSError:
        snapshot["fd_count"] = None

    snapshot["rss_kib"] = None
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    snapshot["rss_kib"] = int(line.split()[1])
                    break
    except (OSError, ValueError, IndexError):
        pass
    return snapshot


def _sensor_factories() -> Iterable[Tuple[str, Callable[[], Any]]]:
    """executeが許可された後にだけハードウェア対応ライブラリをimportする。"""
    from rpi_sensors.bme280_pressure import BME280Sensor
    from rpi_sensors.grove_mcp3208_sensors import GroveLightSensor, GroveSoundSensor
    from rpi_sensors.joystick_mcp3208 import JoystickMCP3208
    from rpi_sensors.mh_x19c_co2 import MHZ19C
    from rpi_sensors.potentiometer_mcp3208 import PotentiometerMCP3208
    from rpi_sensors.robust_dht22 import RobustDHT22
    from rpi_sensors.tactile_button import TactileButton

    return (
        ("light", lambda: GroveLightSensor(channel=0)),
        ("sound", lambda: GroveSoundSensor(channel=1)),
        ("joystick", lambda: JoystickMCP3208(deadzone=150)),
        ("potentiometer", lambda: PotentiometerMCP3208(channel=4)),
        ("button", lambda: TactileButton(pin=6)),
        ("dht22", lambda: RobustDHT22(pin=26)),
        ("bme280", lambda: BME280Sensor(port=1, address=0x76)),
        ("mh_z19c", lambda: MHZ19C(pi4gpio_port=0)),
    )


def build_sensors() -> Dict[str, Any]:
    sensors: Dict[str, Any] = {}
    try:
        for name, factory in _sensor_factories():
            sensors[name] = factory()
    except BaseException:
        close_sensors(sensors)
        raise
    return sensors


def close_sensors(sensors: Mapping[str, Any]) -> None:
    for sensor in reversed(list(sensors.values())):
        try:
            sensor.close()
        except Exception:
            pass


def _read_value(name: str, sensor: Any) -> Any:
    if name == "light":
        return {"raw": sensor.read_raw(), "voltage": sensor.read_voltage()}
    if name == "sound":
        return {"raw": sensor.read_raw()}
    if name == "joystick":
        x_value, y_value = sensor.read_xy(ch_x=2, ch_y=3, normalize=True)
        return {"x": x_value, "y": y_value}
    if name == "potentiometer":
        return {"percent": sensor.read_percentage()}
    if name == "button":
        just_pressed, released_duration, held_time = sensor.update()
        return {
            "just_pressed": just_pressed,
            "released_duration": released_duration,
            "held_time": held_time,
        }
    if name == "dht22":
        temperature, humidity = sensor.read()
        return {"temp": temperature, "hum": humidity}
    if name == "bme280":
        temperature, humidity, pressure = sensor.read()
        return {"temp": temperature, "hum": humidity, "pres": pressure}
    if name == "mh_z19c":
        return {"co2": sensor.read_co2()}
    raise KeyError(name)


def collect_sensor_values(sensors: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for name, sensor in sensors.items():
        started = time.monotonic()
        try:
            value = _read_value(name, sensor)
            results[name] = {
                "ok": True,
                "value": value,
                "latency_ms": round((time.monotonic() - started) * 1000.0, 3),
                "error_type": None,
                "error": None,
                "reconnected": False,
            }
        except Exception as exc:
            results[name] = {
                "ok": False,
                "value": None,
                "latency_ms": round((time.monotonic() - started) * 1000.0, 3),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "reconnected": bool(getattr(exc, "reconnected", False)),
            }
    return results


def _write_jsonl(output_file: Any, payload: Mapping[str, Any]) -> None:
    output_file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    output_file.flush()


def dry_run_report(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "mode": "dry-run",
        "would_execute": False,
        "hardware_operations": 0,
        "production_service": service_properties(args.production_service),
        "daemon_service": service_properties(args.daemon_service),
        "backend": "pi4gpio",
        "interval_sec": args.interval,
        "duration_sec": args.duration,
        "cycles": args.cycles,
        "output": str(args.output),
    }


def execute(args: argparse.Namespace) -> int:
    production = service_properties(args.production_service)
    if production.get("ActiveState") != "inactive":
        raise SafetyError(
            f"{args.production_service}が明示的なinactiveではありません: "
            f"{production.get('ActiveState')}"
        )
    daemon = service_properties(args.daemon_service)
    if daemon.get("ActiveState") != "active":
        raise SafetyError(
            f"{args.daemon_service}がactiveではありません: "
            f"{daemon.get('ActiveState')}"
        )

    os.environ["RPI_SENSOR_BACKEND"] = "pi4gpio"
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sensors: Dict[str, Any] = {}
    started = time.monotonic()
    next_deadline = started
    cycle_index = 0

    try:
        sensors = build_sensors()
        with output_path.open("a", encoding="utf-8") as output_file:
            os.chmod(output_path, 0o600)
            while True:
                production = service_properties(args.production_service)
                if production.get("ActiveState") != "inactive":
                    raise SafetyError(
                        f"試験中に{args.production_service}がinactiveではなくなりました"
                    )

                cycle_started = time.monotonic()
                sensor_results = collect_sensor_values(sensors)
                cycle_finished = time.monotonic()
                cycle_index += 1
                _write_jsonl(
                    output_file,
                    {
                        "timestamp": datetime.datetime.now().astimezone().isoformat(),
                        "backend": "pi4gpio",
                        "cycle": cycle_index,
                        "interval_sec": args.interval,
                        "cycle_duration_ms": round(
                            (cycle_finished - cycle_started) * 1000.0, 3
                        ),
                        "period_overrun": (cycle_finished - cycle_started) > args.interval,
                        "daemon": service_properties(args.daemon_service),
                        "process": process_snapshot(),
                        "sensors": sensor_results,
                    },
                )

                if args.cycles is not None and cycle_index >= args.cycles:
                    break
                if (cycle_finished - started) >= args.duration:
                    break

                next_deadline += args.interval
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    skipped = int((-delay) // args.interval) + 1
                    next_deadline += skipped * args.interval
    finally:
        close_sensors(sensors)

    print(
        json.dumps(
            {
                "status": "completed",
                "cycles": cycle_index,
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="安全条件を確認した上でセンサーハードウェア試験を実行する",
    )
    parser.add_argument(
        "--interval",
        type=lambda value: positive_float(value, "--interval"),
        default=interval_default(),
        help="取得周期（秒）。既定はSENSOR_SEND_INTERVAL_SECまたは10秒",
    )
    parser.add_argument(
        "--duration",
        type=lambda value: positive_float(value, "--duration"),
        default=DEFAULT_DURATION_SEC,
        help="最大実行時間（秒）",
    )
    parser.add_argument("--cycles", type=int, default=None, help="最大取得回数")
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument(
        "--production-service", default=DEFAULT_PRODUCTION_SERVICE
    )
    parser.add_argument("--daemon-service", default=DEFAULT_DAEMON_SERVICE)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.cycles is not None and args.cycles <= 0:
        parser.error("--cyclesは1以上が必要です")
    return args


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    if not args.execute:
        print(json.dumps(dry_run_report(args), ensure_ascii=False, sort_keys=True))
        return 0
    try:
        return execute(args)
    except SafetyError as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
