#!/bin/sh
set -eu

test_root=/home/pi/pi4gpio-test
venv_path="$test_root/.venv"

mkdir -p "$test_root/results"
chmod 700 "$test_root" "$test_root/results"

python3 -m venv "$venv_path"
"$venv_path/bin/python" -m pip install --upgrade pip
"$venv_path/bin/python" -m pip install 'rpi-sensor-lib[bme280]==0.2.0'
"$venv_path/bin/python" -m pip install /home/pi/pi4gpio/clients/python

"$venv_path/bin/python" -c 'import bme280, pi4gpio_client, rpi_sensors; print("test environment ready")'
"$venv_path/bin/python" -m pip freeze
