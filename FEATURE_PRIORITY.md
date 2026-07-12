# 機能優先順位（Feature Priority）

`pi4gpio`が引き継ぐpigpioの機能群は多岐にわたり、全部を一度に実装すると一人保守の前提と噛み合わない（詳細は`SESSION_HANDOFF.md` §3・§4-1）。そこで、移行対象である`rpi-sensor-lib`（`C:\Users\Kazuki\github-ripo\`）の実コードを調査し、実際の使用実績に基づいてTier分けした。作業日: 2026-07-12。

## 1. 現状把握：`rpi-sensor-lib`の依存関係

| ファイル | 使用ライブラリ | 用途 |
|---|---|---|
| `bme280_pressure.py` | `smbus2` | I2C — 気圧センサー |
| `grove_mcp3208_sensors.py` / `joystick_mcp3208.py` / `potentiometer_mcp3208.py` | `spidev` | SPI — MCP3208 ADC経由でアナログ値読み取り |
| `tactile_button.py` | `lgpio` | GPIO — デジタル入力（プルアップ） |
| `robust_dht22.py` | `lgpio` | GPIO — 出力→入力切替のビットバンギング波形読み取り |
| `mh_x19c_co2.py` | `serial` | UART — CO2センサー |

### 特筆事項：`robust_dht22.py`

DHT22の波形読み取りを、busy-loopでピン状態をポーリングし続けPython側でパルス長を計算するという力技で実装している。コード内docstringでも「Linuxのマルチタスクによるタイミングのズレ（DHT22の癖）」を課題として明記済み。これはpigpioのGPIO通知／コールバック（正確なタイムスタンプ付きエッジ検出）が本来解決する問題そのものであり、Tier分けの重要な手がかりとなった。

## 2. Tier分け

### Tier 1 — 必須（現行センサー全種が今使っている機能）— **実装・実機検証済み（2026-07-12）**
- I2Cハードウェアアクセス（BME280用） — `crates/pi4gpio-hw/src/i2c.rs`
- SPIハードウェアアクセス（MCP3208系3用途） — `crates/pi4gpio-hw/src/spi.rs`
- GPIO基本読み書き（デジタル入出力・プルアップ設定） — `crates/pi4gpio-hw/src/gpio.rs`
- UART（MH-Z19C用） — `crates/pi4gpio-hw/src/uart.rs`

Tier 1が揃ったので、`rpi-sensor-lib`の全センサーの移行が成立する状態になった（実際の移行＝Pythonクライアント実装・二重モード化はまだ未着手、`MIGRATION_PLAN.md`参照）。詳細な検証内容は`VERIFICATION_LOG.md`。

### Tier 2 — このプロジェクトの一番の価値が出る部分 — **実装・実機検証済み（2026-07-12）**
- GPIOエッジ検出＋タイムスタンプ付き通知（notification/callback） — `crates/pi4gpio-hw/src/gpio_watch.rs`

`robust_dht22.py`の自前busy-loopを、デーモン側の正確なタイミング機構に置き換えられる。既存の具体的な痛み（DHT22読み取りの信頼性問題）を解決する機能であり、単なる「ついで」ではなくpi4gpioを作る動機そのものに直結する。カーネルの`gpiochip`割り込み機構を使い、実機で20ms間隔のトグルを0.06msの誤差で記録できることを確認済み（`VERIFICATION_LOG.md`）。DHT22固有の40ビットデコードロジックはPythonクライアント側に実装する想定でまだ未着手。

### Tier 3 — 使用実績なし、必要になったら着手
- ハードウェアPWM／サーボパルス
- 汎用クロック出力

### Tier 4 — 初期スコープから明確に除外
- 波形生成（pigpio独自のwave機能）
- ビットバンギング版I2C/SPI（ハードウェア版で全用途をカバーできている）
- 1-Wire
- スクリプトエンジン

## 3. 結論・次のステップ

Tier 1でMVP（Minimum Viable Product）としての移行を成立させ、Tier 2で既存の痛みを解決する、という方針で、両方とも実装・実機検証済みになった。Tier 3・4は引き続き使用実績が具体化してから優先順位を再検討する。

次のステップは、Tier 1/2をアプリケーション側から実際に使えるようにするPythonクライアントライブラリの実装（`MIGRATION_PLAN.md`の二重モード化の前提）。`SESSION_HANDOFF.md` §4の未決定事項（実装言語・ネットワーク越し制御・`rpi-sensor-lib`移行段取り・リポジトリ構成）は全て決定済み。
