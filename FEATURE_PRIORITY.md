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

### Tier 1 — 必須（現行センサー全種が今使っている機能）
- I2Cハードウェアアクセス（BME280用）
- SPIハードウェアアクセス（MCP3208系3用途）
- GPIO基本読み書き（デジタル入出力・プルアップ設定）
- UART（MH-Z19C用）

Tier 1が揃った時点で、`rpi-sensor-lib`の全センサーの移行が成立する。

### Tier 2 — このプロジェクトの一番の価値が出る部分
- GPIOエッジ検出＋タイムスタンプ付き通知（notification/callback）

`robust_dht22.py`の自前busy-loopを、デーモン側の正確なタイミング機構に置き換えられる。既存の具体的な痛み（DHT22読み取りの信頼性問題）を解決する機能であり、単なる「ついで」ではなくpi4gpioを作る動機そのものに直結する。

### Tier 3 — 使用実績なし、必要になったら着手
- ハードウェアPWM／サーボパルス
- 汎用クロック出力

### Tier 4 — 初期スコープから明確に除外
- 波形生成（pigpio独自のwave機能）
- ビットバンギング版I2C/SPI（ハードウェア版で全用途をカバーできている）
- 1-Wire
- スクリプトエンジン

## 3. 結論・次のステップ

Tier 1でMVP（Minimum Viable Product）としての移行を成立させ、Tier 2で既存の痛みを解決する。Tier 3・4は使用実績が具体化してから優先順位を再検討する。

この結果を踏まえ、`SESSION_HANDOFF.md` §4の残り未決定事項（実装言語・ネットワーク越し制御・`rpi-sensor-lib`移行段取り・リポジトリ構成）を順次詰めていく。実装言語については同ファイルの議論でRustが有力候補として挙がっている。
