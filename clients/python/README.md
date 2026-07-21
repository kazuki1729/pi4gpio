# pi4gpio-client

[pi4gpio](https://github.com/kazuki1729/pi4gpio)デーモン（`pi4gpiod`）へのUnixソケット経由Pythonクライアントライブラリ。

## 使い方

```python
from pi4gpio_client import Pi4gpioClient

with Pi4gpioClient() as client:
    client.gpio_write(pin=17, value=True)
    level = client.gpio_read(pin=17)

    chip_id = client.i2c_write_read(bus=1, addr=0x76, data=bytes([0xD0]), length=1)

    adc = client.spi_transfer(bus=0, chip_select=0, data=bytes([0x06, 0x00, 0x00]))

    client.uart_write(port=0, baud_rate=9600, data=b"\xff\x01\x86\x00\x00\x00\x00\x00\x79")
    response = client.uart_read(port=0, baud_rate=9600, length=9)
```

BME280のキャリブレーション計算・DHT22のビット列デコード等、センサー固有のロジックはこのライブラリの範囲外。pi4gpiodは汎用バスプリミティブ（GPIO/I2C/SPI/UARTの生の読み書き）のみを提供する設計で、センサー固有の解釈は呼び出し側に残る（詳細は[SESSION_HANDOFF.md](https://github.com/kazuki1729/pi4gpio/blob/main/SESSION_HANDOFF.md)参照）。

## デーモン再起動からの復旧

接続断を検出すると、クライアントは壊れたソケットを破棄し、既定では最大8回
（0.1秒から最大1秒までの指数バックオフ）再接続する。再接続に成功しても、
切断時に処理中だった要求は自動再送せず`Pi4gpioConnectionError`を送出する。
UART読み取りの二重消費や書き込みの二重実行を防ぐためである。呼び出し側は
その計測周期を失敗として扱い、次の通常周期から処理を継続する。

```python
from pi4gpio_client import Pi4gpioClient, Pi4gpioConnectionError

client = Pi4gpioClient(reconnect_attempts=8)
try:
    value = client.gpio_read(pin=17)
except Pi4gpioConnectionError as exc:
    # exc.reconnected=Trueなら接続自体は復旧済み。要求の再送はしていない。
    record_failed_sample(str(exc))
```

`auto_reconnect=False`で自動再接続を無効化できる。デーモン再起動後は接続単位の
ロックが失われるが、次の操作時に新しい接続で対象バスを再取得する。

## 開発中のローカルインストール

```bash
pip install -e .
```
