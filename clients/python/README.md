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

## 開発中のローカルインストール

```bash
pip install -e .
```
