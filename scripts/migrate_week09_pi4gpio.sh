#!/bin/sh
# Week09 Ver2をdirectからpi4gpioへ、自動ロールバック付きで段階移行する。
# rootで実行する。Ver3が起動中なら何も変更せず終了する。
set -eu

marker=/run/pi4gpio-week09-v2-migration.pending
dropin_source=/tmp/pi4gpio-exclusive.conf
dropin_dir=/etc/systemd/system/sensor-tiered-client.service.d
dropin=$dropin_dir/pi4gpio-exclusive.conf
rollback_unit=pi4gpio-week09-v2-rollback

if [ "$(systemctl is-active sensor-v3-client.service || true)" != inactive ]; then
    echo "sensor-v3-client.service is not inactive; aborting without changes" >&2
    exit 20
fi
test "$(systemctl is-active sensor-tiered-client.service)" = active
test "$(systemctl is-active pi4gpio.service)" = active
test -f "$dropin_source"
test -x /usr/local/sbin/rollback_week09_pi4gpio

echo "BEFORE"
systemctl show sensor-tiered-client.service \
    -p MainPID -p ActiveEnterTimestamp -p NRestarts

touch "$marker"
systemctl stop "$rollback_unit.timer" "$rollback_unit.service" 2>/dev/null || true
systemd-run \
    --unit="$rollback_unit" \
    --on-active=10m \
    --timer-property=AccuracySec=1s \
    /usr/local/sbin/rollback_week09_pi4gpio

rollback_on_error()
{
    rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ -e "$marker" ]; then
        echo "migration failed; running immediate rollback" >&2
        /usr/local/sbin/rollback_week09_pi4gpio || true
    fi
    exit "$rc"
}
trap rollback_on_error EXIT

install -d -o root -g root -m 0755 "$dropin_dir"
install -o root -g root -m 0644 "$dropin_source" "$dropin"
systemctl daemon-reload
systemd-analyze verify sensor-tiered-client.service

if [ "$(systemctl is-active sensor-v3-client.service || true)" != inactive ]; then
    echo "Ver3 became active before restart; aborting" >&2
    exit 21
fi

systemctl restart sensor-tiered-client.service
sleep 12
systemctl is-active --quiet sensor-tiered-client.service

echo "AFTER"
systemctl show sensor-tiered-client.service \
    -p MainPID -p ActiveEnterTimestamp -p NRestarts -p ExecMainStatus \
    -p DevicePolicy -p PrivateDevices

pid=$(systemctl show -p MainPID --value sensor-tiered-client.service)
tr '\0' '\n' <"/proc/$pid/environ" |
    grep -E '^(RPI_SENSOR_BACKEND|PYTHONPATH|SENSOR_HOST|SENSOR_PORT)=' |
    sort
tr '\0' ' ' <"/proc/$pid/cmdline"
echo

echo "CLIENT_DEVICE_FDS"
find "/proc/$pid/fd" -maxdepth 1 -type l -printf '%f %l\n' |
    grep -E '/dev/(gpiochip|i2c|spi|tty)' || echo "none"

echo "ROLLBACK_TIMER"
systemctl list-timers "$rollback_unit.timer" --no-pager
echo "LOG"
journalctl -u sensor-tiered-client.service --since "-1 min" --no-pager -n 80

trap - EXIT
