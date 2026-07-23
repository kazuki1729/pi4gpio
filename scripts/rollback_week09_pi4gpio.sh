#!/bin/sh
# Week09 Ver2の段階移行が確認されなかった場合にdirectへ戻す。
# systemd-runの一時タイマーからrootで実行することを想定している。
set -eu

marker=/run/pi4gpio-week09-v2-migration.pending
dropin=/etc/systemd/system/sensor-tiered-client.service.d/pi4gpio-exclusive.conf
archive_dir=/var/lib/pi4gpio/week09-v2-rollbacks
stamp=$(date -u +%Y%m%dT%H%M%SZ)

log()
{
    logger -t pi4gpio-week09-rollback -- "$*"
    printf '%s\n' "$*"
}

if [ ! -e "$marker" ]; then
    log "marker absent; rollback is not required"
    exit 0
fi

mkdir -p "$archive_dir"
if [ -e "$dropin" ]; then
    cp -p "$dropin" "$archive_dir/pi4gpio-exclusive.$stamp.conf"
    rm -f "$dropin"
fi
systemctl daemon-reload

# Ver3とVer2にはConflicts=が設定されている。Ver3が既に動いている場合は
# Ver2やハードウェアデーモンを操作せず、将来のVer2起動をdirectへ戻すだけにする。
if systemctl is-active --quiet sensor-v3-client.service; then
    rm -f "$marker"
    log "Ver3 is active; disabled the Ver2 pi4gpio drop-in without starting Ver2"
    exit 0
fi

systemctl stop sensor-tiered-client.service
systemctl restart pi4gpio.service
systemctl start sensor-tiered-client.service
rm -f "$marker"
log "rolled Week09 Ver2 back to the original direct service"
