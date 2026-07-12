# pi4gpio

Raspberry Pi 4（BCM2711）向けの、pigpio後継となるGPIO/SPI/I2C/UART共有アクセス・デーモン。

設計の背景・意思決定の経緯は以下を参照。

- [SESSION_HANDOFF.md](SESSION_HANDOFF.md) — プロジェクトの位置づけ、pigpio不採用の理由、想定課題
- [FEATURE_PRIORITY.md](FEATURE_PRIORITY.md) — 機能優先順位（Tier分け）
- [NETWORK_POLICY.md](NETWORK_POLICY.md) — ネットワーク制御方針
- [MIGRATION_PLAN.md](MIGRATION_PLAN.md) — `rpi-sensor-lib`移行計画
- [VERIFICATION_LOG.md](VERIFICATION_LOG.md) — 実機での動作確認記録

## 現在の状態

`FEATURE_PRIORITY.md`のTier 1（GPIO・I2C・SPI・UART基本読み書き）とTier 2（GPIOエッジ検出/通知）を実装し、実機（`kazuki1729.local`）で検証済み（`VERIFICATION_LOG.md`参照）。実機テストでGPIOのプルアップ/ダウン符号バグ・UARTの無期限ブロックバグの2件を発見・修正済み。

次のステップはPythonクライアントライブラリの実装（`rpi-sensor-lib`の二重モード化の前提、`MIGRATION_PLAN.md`参照）。

## 構成

- `crates/pi4gpio-daemon` — メインバイナリ（`pi4gpiod`）。Unixソケットサーバ・ロック機構・クライアント管理
- `crates/pi4gpio-hw` — ハードウェア直叩き層。`unsafe`を要する操作をこのクレートに局所化する

## ビルド

Raspberry Pi 4（`aarch64-unknown-linux-gnu`）向け:

```bash
cargo build --release --target aarch64-unknown-linux-gnu
```
