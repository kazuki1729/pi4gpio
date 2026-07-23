#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""week09本番とpi4gpiodを無侵襲で監視する受動カナリア。

本番がdirectバックエンドで動作している間、別プロセスがpi4gpio経由で同じ
GPIO/I2C/SPI/UARTへ触れると、pi4gpiodのLockTableでは競合を防げない。
そのため本スクリプトはセンサークラスをimportせず、ハードウェアアクセスを
一切行わない。センサー値は本番のjournalログから転記し、pi4gpiodについては
不正な空JSONに対するプロトコルエラー応答だけを確認する。

systemd unitはPrivateDevices=trueとDevicePolicy=closedを併用し、将来の変更で
誤ってdirect/pi4gpioセンサーアクセスが再導入されてもデバイスを開けないよう
OSレベルで制限する。``--require-private-devices``はその制限が実際に効いている
ことを起動時に確認する。

使い方:
    python3 canary_compare.py --interval 30 --output canary_log_passive.csv
"""

import argparse
import csv
import datetime
import glob
import json
import os
import socket
import subprocess
import time

DEFAULT_SOCKET_PATH = "/run/pi4gpio/pi4gpio.sock"
# week09の標準出力はjournaldへ約260秒間隔でまとめて反映されるため、
# 1分では正常稼働中にも誤警報になる。サービス状態は別途即時確認し、
# payloadの停止判定にはバッファ周期を上回る6分を使う。
PRODUCTION_STALE_AFTER_SEC = 360.0
CSV_HEADER = [
    "timestamp",
    "sensor",
    "backend",
    "ok",
    "value",
    "latency_ms",
    "error",
    "production_ref",
]

HARDWARE_DEVICE_PATTERNS = (
    "/dev/gpiomem",
    "/dev/gpiochip*",
    "/dev/i2c-*",
    "/dev/spidev*",
    "/dev/ttyS*",
    "/dev/ttyAMA*",
    "/dev/serial*",
)


def verify_hardware_devices_hidden() -> None:
    """systemdのprivate /dev内に対象デバイスが見えないことを検証する。"""
    visible = sorted(
        {path for pattern in HARDWARE_DEVICE_PATTERNS for path in glob.glob(pattern)}
    )
    if visible:
        raise RuntimeError(
            "ハードウェアデバイスが可視のため受動カナリアを開始しません: "
            + ", ".join(visible)
        )


def latest_production_payload():
    """本番サービス直近の「送信準備: {json}」ログを1件取得する。"""
    try:
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                "sensor-tiered-client.service",
                "-n",
                "300",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None

    marker = "送信準備: "
    for line in reversed(result.stdout.splitlines()):
        idx = line.find(marker)
        if idx == -1:
            continue
        try:
            return json.loads(line[idx + len(marker) :])
        except json.JSONDecodeError:
            continue
    return None


def probe_pi4gpiod(socket_path=DEFAULT_SOCKET_PATH):
    """ハードウェア操作なしでdaemonのソケット／JSON応答経路を確認する。"""
    start = time.monotonic()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(socket_path)
            sock.sendall(b"{}\n")
            reader = sock.makefile("rb")
            line = reader.readline()
            reader.close()
        if not line:
            raise ConnectionError("空の応答")
        response = json.loads(line)
        if response.get("ok") is not False:
            raise RuntimeError("不正要求に対する想定外の応答")
        return True, "responsive", (time.monotonic() - start) * 1000.0, ""
    except Exception as exc:  # daemon監視は全通信エラーをCSVへ記録する
        return False, "", (time.monotonic() - start) * 1000.0, str(exc)


def probe_production_service():
    """systemd上のweek09本番サービスが現在activeかを非侵襲で確認する。"""
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "sensor-tiered-client.service"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        state = result.stdout.strip() or "unknown"
        ok = result.returncode == 0 and state == "active"
        error = "" if ok else f"production_service_{state}"
        return ok, state, (time.monotonic() - start) * 1000.0, error
    except Exception as exc:
        return False, "unknown", (time.monotonic() - start) * 1000.0, str(exc)


def _has_complete_value(value):
    if value is None:
        return False
    if isinstance(value, dict):
        return bool(value) and all(item is not None for item in value.values())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(item is not None for item in value)
    return True


def _nested(payload, *keys):
    value = payload or {}
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _write_production_row_with_status(writer, timestamp, sensor, value, source_error):
    complete = _has_complete_value(value)
    ok = complete and not source_error
    display = json.dumps(value, ensure_ascii=False, sort_keys=True) if complete else ""
    writer.writerow(
        [
            timestamp,
            sensor,
            "production_log",
            ok,
            display,
            "",
            source_error or ("" if complete else "production_reference_missing"),
            display,
        ]
    )


def production_payload_age_seconds(prod_payload, now=None):
    """payload内の現地時刻から経過秒を返す。解析不能ならNone。"""
    raw_timestamp = _nested(prod_payload, "timestamp")
    if not isinstance(raw_timestamp, str):
        return None
    try:
        recorded = datetime.datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return None
    current = now or datetime.datetime.now(tz=recorded.tzinfo)
    if recorded.tzinfo is None and current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    return max(0.0, (current - recorded).total_seconds())


def run_once(
    writer,
    prod_payload,
    daemon_probe=probe_pi4gpiod,
    production_probe=probe_production_service,
):
    now = datetime.datetime.now()
    timestamp = now.isoformat()
    payload_age = production_payload_age_seconds(prod_payload, now=now)
    production_ok, production_state, production_latency, production_error = (
        production_probe()
    )
    writer.writerow(
        [
            timestamp,
            "production_service_health",
            "systemd",
            production_ok,
            production_state,
            f"{production_latency:.1f}",
            production_error,
            "",
        ]
    )

    source_error = production_error
    if not source_error and payload_age is None:
        source_error = "production_reference_missing"
    elif not source_error and payload_age > PRODUCTION_STALE_AFTER_SEC:
        source_error = "production_reference_stale"

    writer.writerow(
        [
            timestamp,
            "production_log_health",
            "journal",
            not source_error,
            f"{payload_age:.1f}" if payload_age is not None else "",
            "",
            source_error,
            "",
        ]
    )
    values = (
        ("bme280", _nested(prod_payload, "bme280", "pres")),
        ("grove_light", _nested(prod_payload, "light")),
        ("grove_sound", _nested(prod_payload, "sound", "raw")),
        ("joystick", _nested(prod_payload, "joystick")),
        ("potentiometer", _nested(prod_payload, "potentiometer", "percent")),
        # 現在の押下レベルではなく、本番が記録した送信トリガーを転記する。
        ("tactile_button_event", _nested(prod_payload, "trigger") == "button"),
        ("mh_z19c_co2", _nested(prod_payload, "mh_z19c", "co2")),
        (
            "robust_dht22",
            {
                "temp": _nested(prod_payload, "dht22", "temp"),
                "hum": _nested(prod_payload, "dht22", "hum"),
            },
        ),
    )
    for sensor, value in values:
        _write_production_row_with_status(
            writer, timestamp, sensor, value, source_error
        )

    ok, value, latency_ms, error = daemon_probe()
    writer.writerow(
        [
            timestamp,
            "pi4gpiod_health",
            "unix_socket",
            ok,
            value,
            f"{latency_ms:.1f}",
            error,
            "",
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=30.0, help="記録間隔(秒)")
    parser.add_argument(
        "--duration", type=float, default=None, help="総実行時間(秒)。省略時は無期限"
    )
    parser.add_argument(
        "--output", default="canary_log_passive.csv", help="出力CSVパス"
    )
    parser.add_argument(
        "--require-private-devices",
        action="store_true",
        help="GPIO/I2C/SPI/UARTデバイスが見えない場合だけ起動する",
    )
    args = parser.parse_args()

    if args.interval <= 0:
        parser.error("--intervalは0より大きい値が必要です")
    if args.duration is not None and args.duration <= 0:
        parser.error("--durationは0より大きい値が必要です")
    if args.require_private_devices:
        verify_hardware_devices_hidden()

    print("受動カナリアを開始します（センサーハードウェアアクセスなし）。")
    file_exists = os.path.exists(args.output)
    start = time.monotonic()
    iteration = 0
    try:
        with open(args.output, "a", newline="", encoding="utf-8") as output_file:
            writer = csv.writer(output_file)
            if not file_exists:
                writer.writerow(CSV_HEADER)

            while args.duration is None or (time.monotonic() - start) < args.duration:
                run_once(writer, latest_production_payload())
                output_file.flush()
                iteration += 1
                print(f"\r記録回数: {iteration}", end="", flush=True)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n中断されました。")
    finally:
        print(f"\n合計{iteration}回記録し、{args.output}に保存しました。")


if __name__ == "__main__":
    main()
