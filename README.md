# pi4gpio

Raspberry Pi 4（BCM2711）向けの、pigpio後継となるGPIO/SPI/I2C/UART共有アクセス・デーモン。

設計の背景・意思決定の経緯は以下を参照。

- [SESSION_HANDOFF.md](SESSION_HANDOFF.md) — プロジェクトの位置づけ、pigpio不採用の理由、想定課題
- [FEATURE_PRIORITY.md](FEATURE_PRIORITY.md) — 機能優先順位（Tier分け）
- [NETWORK_POLICY.md](NETWORK_POLICY.md) — ネットワーク制御方針
- [MIGRATION_PLAN.md](MIGRATION_PLAN.md) — `rpi-sensor-lib`移行計画
- [VERIFICATION_LOG.md](VERIFICATION_LOG.md) — 実機での動作確認記録
- [FAULT_INJECTION.md](FAULT_INJECTION.md) — 隔離テストと実機systemd障害注入の安全手順
- [EXCLUSIVE_ACCESS.md](EXCLUSIVE_ACCESS.md) — directアクセスをOSレベルで排除する運用

## 現在の状態

`FEATURE_PRIORITY.md`のTier 1（GPIO・I2C・SPI・UART基本読み書き）とTier 2（GPIOエッジ検出/通知）を実装し、実機（`kazuki1729.local`）で検証済み。Pythonクライアントライブラリ（`clients/python`）も実装済みで、`rpi-sensor-lib`の全7センサークラスがこのクライアント経由で使えるよう二重モード化（`RPI_SENSOR_BACKEND=direct|pi4gpio`）が完了している（`MIGRATION_PLAN.md`の移行順序5/5、詳細は`VERIFICATION_LOG.md`）。

実機テストではこれまでに6件の実運用バグを発見・修正済み: GPIOのプルアップ/ダウン符号バグ、UARTの無期限ブロックバグ、PythonクライアントのOperationタグ付け誤り、クライアント通信エラー時のロック解放漏れ、DHT22デコードのHIGH区間長の測り方の取り違え、DHT22 pi4gpioモードでのカーネルGPIO v2エッジ割り込みの取りこぼし。

`pi4gpiod`はsystemdサービスとして実機に常駐化済み（`User=pi`・自動再起動対応）。センサー再接続後、全7センサークラスの実データ検証も完了した（2026-07-13、`VERIFICATION_LOG.md`）。この過程で、DHT22のpi4gpioモードがカーネルGPIO v2エッジ割り込みの取りこぼしにより実機では常に失敗する重大バグを発見し、Tier 1相当の高速ポーリング方式（`WatchEdgesPolled`）を新規実装して解決した——実機テストでなければ気づけなかった不具合の6件目。

`MIGRATION_PLAN.md` §6のカナリア（`scripts/canary_compare.py`）は、保守時間帯の手動診断用として残している。本番がdirectの間は完全受動型で、センサーライブラリをimportせず、systemdの`PrivateDevices`・`DevicePolicy`でも物理デバイスを不可視化する。2026-07-21時点では既存のweek09監視と重複するため、実機の常駐serviceは撤去済み。本番移行後にdirectアクセスを禁止するdrop-inは`EXCLUSIVE_ACCESS.md`参照。

## 構成

- `crates/pi4gpio-daemon` — メインバイナリ（`pi4gpiod`）。Unixソケットサーバ・ロック機構・クライアント管理
- `crates/pi4gpio-hw` — ハードウェア直叩き層。`unsafe`を要する操作をこのクレートに局所化する
- `clients/python` — Pythonクライアントライブラリ（`pi4gpio_client`）。`rpi-sensor-lib`等がpi4gpiodと通信するために使う

## ビルド

Raspberry Pi 4（`aarch64-unknown-linux-gnu`）向け:

```bash
cargo build --release --target aarch64-unknown-linux-gnu
```
