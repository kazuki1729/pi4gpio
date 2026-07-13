#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MIGRATION_PLAN.md §6の並行稼働・カナリア検証用スクリプト。

`rpi-sensor-lib`のdirect/pi4gpio両バックエンドで同じセンサーを読み、
値・レイテンシ・成功率をCSVに記録する。本番の`sensor-tiered-client.service`
には一切触れない、別プロセスとして動作する（本番を止めない、
MIGRATION_PLAN.md §6の方針通り）。

【重要な設計上の制約】
- I2C（BME280）・SPI（MCP3208系3種）は、カーネルがバス単位でトランザク
  ションをシリアライズするため、direct/pi4gpio両方を本スクリプト自身が
  独立に読んでも安全に並行稼働できる（VERIFICATION_LOGのTier 1検証で
  確認済みの前提）。
- GPIO（tactile_button）・UART（mh_x19c_co2）・DHT22（robust_dht22）は、
  本番プロセスが既にlgpio/pyserial経由で同じピン/デバイスを掴んでいる
  ため、本スクリプトが独自にdirectモードで読むと競合する
  （lgpioはgpiochip行を排他確保するためEBUSYになりうる、UARTは
  termiosの二重アクセスで通信が混線しうる——いずれもVERIFICATION_LOGで
  実機確認済みの制約）。これらはpi4gpioモードのみ本スクリプトが独自に
  読み、directモード側の参考値は、本番プロセスがjournalctlに残す
  「送信準備: {json}」ログから抽出する（厳密な同時比較ではないが、
  傾向の突き合わせとしては十分）。ボタン状態は本番側が周期ログに含めて
  いないため、参考値なしでpi4gpioモードの成功率のみ記録する。

配線パラメータは本番（sensor_client_tiered.py）に合わせている:
  tactile_button pin=6, robust_dht22 pin=26, bme280 addr=0x76,
  light ch=0, sound ch=1, joystick ch_x=2/ch_y=3, potentiometer ch=4

使い方:
    python3 canary_compare.py --interval 30 --output canary_log.csv
    python3 canary_compare.py --interval 30 --duration 3600 --output canary_log.csv
"""

import argparse
import csv
import datetime
import json
import os
import subprocess
import sys
import time

# 本番venvと同じ依存(lgpio/spidev/smbus2/pyserial/bme280)を使う前提で、
# 本番venvのPythonインタプリタで実行する想定
# (例: /home/pi/sensor-tiered-store/.venv/bin/python3 canary_compare.py)。
# rpi_sensors/pi4gpio_clientは、更新済みソースを指すパスをsys.pathの
# 先頭に置いて読み込む（本番のsite-packagesは二重モード化前の古い
# バージョンのため使わない）。
sys.path.insert(0, os.path.expanduser("~/pi4gpio/clients/python"))
sys.path.insert(0, os.path.expanduser("~/rpi-sensor-lib-canary"))

from rpi_sensors import (  # noqa: E402
    BME280Sensor,
    DHT22ReadError,
    GroveLightSensor,
    GroveSoundSensor,
    JoystickMCP3208,
    MHZ19C,
    PotentiometerMCP3208,
    RobustDHT22,
    TactileButton,
)

PIN_BUTTON = 6
PIN_DHT22 = 26
I2C_ADDR_BME280 = 0x76
MCP3208_CH_LIGHT = 0
MCP3208_CH_SOUND = 1
MCP3208_CH_JOY_X = 2
MCP3208_CH_JOY_Y = 3
MCP3208_CH_POT = 4

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


def _timed_call(fn):
    """(成功可否, 値, レイテンシms, エラー文字列)を返す。"""
    start = time.monotonic()
    try:
        value = fn()
        return True, value, (time.monotonic() - start) * 1000.0, None
    except Exception as e:  # noqa: BLE001 - センサー読み取りは何でも失敗しうる
        return False, None, (time.monotonic() - start) * 1000.0, str(e)


def latest_production_payload():
    """journalctlから本番サービス直近の「送信準備: {json}」ログを1件取得する。
    見つからなければNoneを返す。
    """
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


def build_sensors():
    """direct/pi4gpio両方のセンサーインスタンスを事前に構築する
    （毎回作り直すと不要な再初期化I/Oが発生するため、本番の実装方針に
    合わせて1回だけ構築し使い回す）。
    """
    sensors = {"direct": {}, "pi4gpio": {}}

    # I2C/SPIは両バックエンドを独立に構築(並行稼働が安全なため)。
    for backend in ("direct", "pi4gpio"):
        os.environ["RPI_SENSOR_BACKEND"] = backend
        sensors[backend]["bme280"] = BME280Sensor(address=I2C_ADDR_BME280)
        sensors[backend]["grove_light"] = GroveLightSensor(channel=MCP3208_CH_LIGHT)
        sensors[backend]["grove_sound"] = GroveSoundSensor(channel=MCP3208_CH_SOUND)
        sensors[backend]["joystick"] = JoystickMCP3208(deadzone=150)
        sensors[backend]["potentiometer"] = PotentiometerMCP3208(channel=MCP3208_CH_POT)

    # GPIO/UART/DHT22はpi4gpioモードのみ構築(directは本番と競合するため作らない)。
    os.environ["RPI_SENSOR_BACKEND"] = "pi4gpio"
    sensors["pi4gpio"]["tactile_button"] = TactileButton(pin=PIN_BUTTON)
    sensors["pi4gpio"]["mh_z19c"] = MHZ19C()
    sensors["pi4gpio"]["robust_dht22"] = RobustDHT22(
        pin=PIN_DHT22, max_retries=1, read_interval=2.0
    )

    return sensors


def close_sensors(sensors):
    for backend_sensors in sensors.values():
        for sensor in backend_sensors.values():
            try:
                sensor.close()
            except Exception:
                pass


def run_once(writer, sensors, prod_payload):
    timestamp = datetime.datetime.now().isoformat()

    # --- I2C: BME280（direct/pi4gpio 両方） ---
    for backend in ("direct", "pi4gpio"):
        ok, value, latency_ms, err = _timed_call(
            lambda b=backend: sensors[b]["bme280"].read()
        )
        writer.writerow(
            [timestamp, "bme280", backend, ok, value, f"{latency_ms:.1f}", err, ""]
        )

    # --- SPI: MCP3208系4種（direct/pi4gpio 両方） ---
    spi_reads = {
        "grove_light": lambda s: s["grove_light"].read_raw(),
        "grove_sound": lambda s: s["grove_sound"].read_raw(),
        "joystick": lambda s: s["joystick"].read_xy(
            ch_x=MCP3208_CH_JOY_X, ch_y=MCP3208_CH_JOY_Y, normalize=True
        ),
        "potentiometer": lambda s: s["potentiometer"].read_percentage(),
    }
    for name, read_fn in spi_reads.items():
        for backend in ("direct", "pi4gpio"):
            ok, value, latency_ms, err = _timed_call(
                lambda b=backend, f=read_fn: f(sensors[b])
            )
            writer.writerow(
                [timestamp, name, backend, ok, value, f"{latency_ms:.1f}", err, ""]
            )

    # --- GPIO: tactile_button（pi4gpioモードのみ、参考値なし） ---
    ok, value, latency_ms, err = _timed_call(
        lambda: sensors["pi4gpio"]["tactile_button"].update()
    )
    writer.writerow(
        [timestamp, "tactile_button", "pi4gpio", ok, value, f"{latency_ms:.1f}", err, ""]
    )

    # --- UART: mh_z19c_co2（pi4gpioモードのみ、参考値は本番ログから） ---
    ok, value, latency_ms, err = _timed_call(
        lambda: sensors["pi4gpio"]["mh_z19c"].read_co2()
    )
    prod_ref = (prod_payload or {}).get("mh_z19c", {}).get("co2")
    writer.writerow(
        [timestamp, "mh_z19c_co2", "pi4gpio", ok, value, f"{latency_ms:.1f}", err, prod_ref]
    )

    # --- DHT22: robust_dht22（pi4gpioモードのみ、参考値は本番ログから） ---
    def _read_dht22():
        try:
            return sensors["pi4gpio"]["robust_dht22"].read()
        except DHT22ReadError as e:
            raise RuntimeError(str(e)) from e

    ok, value, latency_ms, err = _timed_call(_read_dht22)
    dht22_ref = (prod_payload or {}).get("dht22", {})
    prod_ref = (dht22_ref.get("temp"), dht22_ref.get("hum")) if dht22_ref else None
    writer.writerow(
        [timestamp, "robust_dht22", "pi4gpio", ok, value, f"{latency_ms:.1f}", err, prod_ref]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=30.0, help="読み取り間隔(秒)")
    parser.add_argument(
        "--duration", type=float, default=None, help="総実行時間(秒)。省略時は無期限"
    )
    parser.add_argument("--output", default="canary_log.csv", help="出力CSVパス")
    args = parser.parse_args()

    print("センサーを初期化中...")
    sensors = build_sensors()
    print("初期化完了。記録を開始します。Ctrl+Cで終了。")

    file_exists = os.path.exists(args.output)
    start = time.monotonic()
    iteration = 0
    try:
        with open(args.output, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(CSV_HEADER)

            while args.duration is None or (time.monotonic() - start) < args.duration:
                prod_payload = latest_production_payload()
                run_once(writer, sensors, prod_payload)
                f.flush()
                iteration += 1
                print(f"\r記録回数: {iteration}", end="", flush=True)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n中断されました。")
    finally:
        close_sensors(sensors)
        print(f"\n合計{iteration}回記録し、{args.output}に保存しました。")


if __name__ == "__main__":
    main()
