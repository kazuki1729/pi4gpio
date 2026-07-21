# directアクセス競合の解消方針

## 制約

pi4gpiodの`LockTable`が仲裁できるのは、Unixソケット経由のクライアントだけで
ある。rootまたはGPIO/I2C/SPI/dialout権限を持つ別プロセスが`/dev`を直接開く
ことを、daemon内部のロックだけで防ぐことはできない。

## 現行direct本番での手動診断

常駐カナリアは既存のweek09監視と重複するため実機から撤去した。保守作業で
`canary-compare.service`を一時的に使う場合は、完全受動型としてセンサー
ハードウェアを一切開かない。
値は`journalctl`の本番ログから取得し、pi4gpiodにはハードウェア操作にならない
不正な空JSONだけを送って応答経路を確認する。さらに以下をunitへ設定する。

```ini
PrivateDevices=true
DevicePolicy=closed
NoNewPrivileges=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
```

このため、将来コードへセンサーアクセスが誤って再導入されても、カナリアの
プロセス名前空間から物理デバイスは見えない。

診断中は本番サービスのactive状態を毎回即時確認する。week09の標準出力はjournaldへ
約260秒間隔でまとめて反映されるため、payload時刻が360秒を超えて古い場合に
`production_reference_stale`として失敗記録する。診断終了後はserviceを停止し、
一時CSVを移行判断の恒久データとして扱わない。

## week09をpi4gpioへ移行した後

`systemd/sensor-tiered-client-pi4gpio-exclusive.conf`を
`/etc/systemd/system/sensor-tiered-client.service.d/pi4gpio-exclusive.conf`へ配置する。
このdrop-inは次を同時に行う。

1. `RPI_SENSOR_BACKEND=pi4gpio`を明示する
2. Pythonクライアントのパスを指定する
3. week09サービスのcgroupからGPIO/I2C/SPI/UARTデバイスを隠す
4. sysfs／カーネルモジュール／cgroup経由の迂回も読み取り専用化する
5. Unixソケット経由のアクセスだけを残す

direct運用中にこのdrop-inを適用してはならない。移行は保守時間帯にバックアップ、
Pi4gpio接続確認、サービス再起動、全センサー確認、切り戻し確認の順で行う。

## 共有Pi全体を制限しない理由

このPiではTracker等の別プロジェクトもハードウェアを使用する。udevルール変更や
`pi`ユーザーのgpio/i2c/spi/dialoutグループ削除は、それらを同時に停止させるため
現段階では採用しない。systemdのサービス単位制限ならweek09とカナリアだけを
強制でき、他プロジェクトへ影響しない。

rootプロセスはsystemd制限の外からデバイスへアクセスできるため、ホスト管理者まで
含む防御ではない。サポート対象は、管理された非rootサービス間の競合防止である。
