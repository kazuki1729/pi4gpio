# pi4gpio

Raspberry Pi 4（BCM2711）向けの、pigpio後継となるGPIO/SPI/I2C/UART共有アクセス・デーモン。

設計の背景・意思決定の経緯は以下を参照。

- [SESSION_HANDOFF.md](SESSION_HANDOFF.md) — プロジェクトの位置づけ、pigpio不採用の理由、想定課題
- [FEATURE_PRIORITY.md](FEATURE_PRIORITY.md) — 機能優先順位（Tier分け）
- [NETWORK_POLICY.md](NETWORK_POLICY.md) — ネットワーク制御方針
- [MIGRATION_PLAN.md](MIGRATION_PLAN.md) — `rpi-sensor-lib`移行計画
- [VERIFICATION_LOG.md](VERIFICATION_LOG.md) — 実機での動作確認記録

## 現在の状態

`FEATURE_PRIORITY.md`のTier 1（GPIO・I2C・SPI・UART基本読み書き）とTier 2（GPIOエッジ検出/通知）を実装し、実機（`kazuki1729.local`）で検証済み。Pythonクライアントライブラリ（`clients/python`）も実装済みで、`rpi-sensor-lib`の全7センサークラスがこのクライアント経由で使えるよう二重モード化（`RPI_SENSOR_BACKEND=direct|pi4gpio`）が完了している（`MIGRATION_PLAN.md`の移行順序5/5、詳細は`VERIFICATION_LOG.md`）。

実機テストではこれまでに5件の実運用バグを発見・修正済み: GPIOのプルアップ/ダウン符号バグ、UARTの無期限ブロックバグ、PythonクライアントのOperationタグ付け誤り、クライアント通信エラー時のロック解放漏れ、DHT22デコードのHIGH区間長の測り方の取り違え。

実センサーが物理的に未接続のため、実際の温湿度値等での動作確認はまだできていない。次のステップは、センサー再接続後の実データ検証と、`MIGRATION_PLAN.md` §6の並行稼働・カナリア検証（本番`sensor-tiered-client.service`とは別プロセスでの並行稼働、1〜2週間の値突き合わせ）。

## 構成

- `crates/pi4gpio-daemon` — メインバイナリ（`pi4gpiod`）。Unixソケットサーバ・ロック機構・クライアント管理
- `crates/pi4gpio-hw` — ハードウェア直叩き層。`unsafe`を要する操作をこのクレートに局所化する
- `clients/python` — Pythonクライアントライブラリ（`pi4gpio_client`）。`rpi-sensor-lib`等がpi4gpiodと通信するために使う

## ビルド

Raspberry Pi 4（`aarch64-unknown-linux-gnu`）向け:

```bash
cargo build --release --target aarch64-unknown-linux-gnu
```
