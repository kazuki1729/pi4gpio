"""pi4gpiodへのUnixソケット経由クライアント。

改行区切りJSON（NDJSON）プロトコルでpi4gpiodと通信する。プロトコル定義は
pi4gpioリポジトリの`crates/pi4gpio-daemon/src/protocol.rs`が正本。

BME280のキャリブレーション計算・DHT22の40ビットデコードのようなセンサー
固有のロジックはこのクライアントの責務ではない。pi4gpiodは汎用バス
プリミティブ（GPIO/I2C/SPI/UARTの生の読み書き）のみを提供する設計で、
センサー固有の解釈は呼び出し側（`rpi-sensor-lib`等）に残る
（詳細はpi4gpioリポジトリのSESSION_HANDOFF.md参照）。

`bus`（`BusRef`）と`op`（`Operation`）はserdeでのタグ付け方式が異なる点に
注意。`BusRef`は`#[serde(tag = "type")]`で内部タグ付き
（例: ``{"type": "gpio", "pin": 17}``）だが、`Operation`にはタグ属性が無く
serdeのデフォルト（外部タグ付き）になる。データを持たないバリアント
（`Read`/`Release`）は裸の文字列（例: ``"read"``）、データを持つバリアント
は1キーのオブジェクト（例: ``{"write": {"value": true}}``）で表現される。
"""

from __future__ import annotations

import json
import socket
from typing import Any, BinaryIO, Optional

DEFAULT_SOCKET_PATH = "/run/pi4gpio/pi4gpio.sock"


class Pi4gpioError(Exception):
    """pi4gpiodがエラーレスポンス（``ok: false``）を返した場合に送出する。"""


class Pi4gpioClient:
    """pi4gpiodへの1接続を表すクライアント。

    バスのロックは接続単位で保持される（サーバー側の`LockTable`参照）ため、
    同じ接続を使い回している限り、確保したバスは他クライアントの割り込み
    から守られる。明示的に`*_release()`を呼ぶか、接続を閉じる（`close()`
    または`with`ブロックを抜ける）とロックが解放される。

    with文での利用を想定している::

        with Pi4gpioClient() as client:
            client.gpio_write(pin=17, value=True)
    """

    def __init__(
        self, socket_path: str = DEFAULT_SOCKET_PATH, timeout: Optional[float] = 5.0
    ):
        self._socket_path = socket_path
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._reader: Optional[BinaryIO] = None

    def connect(self) -> "Pi4gpioClient":
        if self._sock is not None:
            return self
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        sock.connect(self._socket_path)
        self._sock = sock
        self._reader = sock.makefile("rb")
        return self

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "Pi4gpioClient":
        return self.connect()

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        self.close()
        return False

    # --- 内部: リクエスト送受信 ---

    def _request(
        self,
        bus: dict[str, Any],
        op_name: str,
        op_args: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """`op_name`は`Operation`のバリアント名（snake_case）、`op_args`は
        そのバリアントが持つフィールド。`op_args`が`None`なら
        データを持たないバリアント（`Read`/`Release`）として裸の文字列で
        送る。
        """
        if self._sock is None:
            self.connect()
        assert self._sock is not None and self._reader is not None

        op: Any = op_name if op_args is None else {op_name: op_args}
        payload = json.dumps({"bus": bus, "op": op}, separators=(",", ":")) + "\n"
        self._sock.sendall(payload.encode("utf-8"))

        line = self._reader.readline()
        if not line:
            raise Pi4gpioError("pi4gpiodとの接続が切断されました（空の応答）")
        response: dict[str, Any] = json.loads(line)

        if not response.get("ok", False):
            raise Pi4gpioError(response.get("error", "不明なエラー"))
        return response

    # --- GPIO ---

    def gpio_read(self, pin: int) -> bool:
        response = self._request({"type": "gpio", "pin": pin}, "read")
        return bool(response["value"])

    def gpio_write(self, pin: int, value: bool) -> bool:
        response = self._request(
            {"type": "gpio", "pin": pin}, "write", {"value": value}
        )
        return bool(response["value"])

    def gpio_watch_edges(
        self,
        pin: int,
        max_events: int,
        timeout_ms: int,
        pre_pulse_low_ms: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """エッジをタイムスタンプ付きで記録する（Tier 2）。

        戻り値は``[{"timestamp_ns": int, "rising": bool}, ...]``。DHT22の
        40ビットデコード等、センサー固有の解釈は呼び出し側の責務。
        """
        response = self._request(
            {"type": "gpio", "pin": pin},
            "watch_edges",
            {
                "pre_pulse_low_ms": pre_pulse_low_ms,
                "max_events": max_events,
                "timeout_ms": timeout_ms,
            },
        )
        edges: list[dict[str, Any]] = response.get("edges") or []
        return edges

    def gpio_release(self, pin: int) -> None:
        self._request({"type": "gpio", "pin": pin}, "release")

    # --- I2C ---

    def i2c_read(self, bus: int, addr: int, length: int) -> bytes:
        response = self._request(
            {"type": "i2c", "bus": bus, "addr": addr},
            "read_bytes",
            {"length": length},
        )
        return bytes(response.get("bytes") or [])

    def i2c_write(self, bus: int, addr: int, data: bytes) -> None:
        self._request(
            {"type": "i2c", "bus": bus, "addr": addr},
            "write_bytes",
            {"data": list(data)},
        )

    def i2c_write_read(self, bus: int, addr: int, data: bytes, length: int) -> bytes:
        response = self._request(
            {"type": "i2c", "bus": bus, "addr": addr},
            "write_read_bytes",
            {"data": list(data), "length": length},
        )
        return bytes(response.get("bytes") or [])

    def i2c_release(self, bus: int) -> None:
        # ロックはbus単位で管理されaddrは無視されるため（protocol.rsの
        # From<&BusRef> for BusId参照）、addrはダミー値でよい。
        self._request({"type": "i2c", "bus": bus, "addr": 0}, "release")

    # --- SPI ---

    def spi_transfer(self, bus: int, chip_select: int, data: bytes) -> bytes:
        response = self._request(
            {"type": "spi", "bus": bus, "chip_select": chip_select},
            "transfer",
            {"data": list(data)},
        )
        return bytes(response.get("bytes") or [])

    def spi_release(self, bus: int, chip_select: int) -> None:
        self._request(
            {"type": "spi", "bus": bus, "chip_select": chip_select}, "release"
        )

    # --- UART ---

    def uart_read(self, port: int, baud_rate: int, length: int) -> bytes:
        response = self._request(
            {"type": "uart", "port": port, "baud_rate": baud_rate},
            "read_bytes",
            {"length": length},
        )
        return bytes(response.get("bytes") or [])

    def uart_write(self, port: int, baud_rate: int, data: bytes) -> None:
        self._request(
            {"type": "uart", "port": port, "baud_rate": baud_rate},
            "write_bytes",
            {"data": list(data)},
        )

    def uart_release(self, port: int, baud_rate: int) -> None:
        self._request(
            {"type": "uart", "port": port, "baud_rate": baud_rate}, "release"
        )
