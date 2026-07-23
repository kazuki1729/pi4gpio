# Week09 Ver2 Pi4gpio段階移行

## 実施結果

2026-07-23 17:00:16 JST、Week09 Ver2の
`sensor-tiered-client.service`をdirectアクセスからPi4gpio経由へ切り替え、
連続運転を開始した。

- Ver3の専用ディレクトリ、venv、unit、資格情報は変更していない
- `sensor-v3-client.service`がinactiveであることを切替前後に確認した
- 元のVer2 unitと`/home/pi/sensor-tiered-store/.venv`は保存した
- `/home/pi/pi4gpio-week09-v2/.venv`へPi4gpio用依存を分離導入した
- `systemd/sensor-tiered-client-pi4gpio-staged.conf`だけで実行環境を上書きした
- Ver2クライアントは`PrivateDevices=yes`、`DevicePolicy=closed`で
  GPIO/I2C/SPI/UARTの物理デバイスFDを持たない
- buffaloへ全センサー値が10秒周期で継続到着し、未送信キューは0だった

## デーモン再起動試験

17:03:16 JSTに`pi4gpio.service`だけを計画再起動した。

- pi4gpiod PID: 67666から72941へ変更
- Ver2クライアントPID: 72845のまま維持
- 17:03:18 JSTにクライアントが自動再接続
- 17:03:06から17:03:56まで10秒周期データが連続
- 両サービスとも`NRestarts=0`、未送信キュー0

## 自動ロールバック

切替前に10分後の一時ロールバックtimerを予約した。スモーク試験と再接続試験の
合格後、timer/serviceとmarkerを解除した。現在のdrop-inは恒久配置だが、
元のdirect用venvとunit本体は残してある。

障害時は`/usr/local/sbin/rollback_week09_pi4gpio`、または
`scripts/rollback_week09_pi4gpio.sh`をrootで実行する。このスクリプトは、
Ver3がactiveの場合にVer2を起動せずdrop-inだけを無効化する。

## Ver3への引継ぎ

Ver3のunitにはVer2との`Conflicts=`がある。Ver3を起動するとVer2は停止し、
Pi4gpioクライアント切断時にdaemonのI2C/SPI/UARTハンドルは解放される。
Ver3側でハードウェアへdirectアクセスする場合も二重保持を避けられるが、
切替時には次を必ず確認する。

1. Ver2がinactiveである
2. pi4gpiodにI2C/SPI/UARTの残留FDがない
3. Ver3だけが対象デバイスを保持する
4. Ver2へ戻す場合はVer3がinactiveである

機械可読の結果は
`baselines/pi4gpio_week09_cutover_20260723.json`に保存した。
