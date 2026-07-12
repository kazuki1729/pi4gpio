//! ワイヤープロトコル（雛形段階）。
//!
//! 改行区切りJSON（1行1リクエスト/1行1レスポンス）。バイナリ化・多重化などの
//! 最適化は、Tier 1操作が実装されパフォーマンス要件が明確になってから検討する
//! （現段階ではPythonクライアント側での可読性・実装のしやすさを優先）。
//!
//! `Operation`はGPIO用（`Read`/`Write`、1ビット単位）、I2C/UART用
//! （`ReadBytes`/`WriteBytes`、方向が別々のバイト列。I2Cはさらに結合
//! トランザクション`WriteReadBytes`を持つ）、SPI用（`Transfer`、送信と
//! 同時に同じ長さを受信する全二重転送）に分かれる。バスの種類に合わない
//! 操作が来た場合は`socket.rs`の`dispatch`が`malformed`で拒否する。
//!
//! いずれの操作もバスを暗黙に確保する（未確保なら`LockTable::try_acquire`）。
//! I2Cはバス単位でロックする（`addr`単位ではない）——同じバス上の別デバイス
//! への割り込みも防ぐのが目的（SESSION_HANDOFF.md §3の「I2C/SPIの複数ステップ
//! 通信を他クライアントの割り込みから守る」）。確保したバスは`Release`または
//! 切断（`socket.rs`の接続ハンドラ側で処理）までそのクライアントが保持する。

use crate::lock::BusId;
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
pub struct Request {
    pub bus: BusRef,
    pub op: Operation,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum BusRef {
    Gpio {
        pin: u32,
    },
    I2c {
        bus: u8,
        addr: u8,
    },
    Spi {
        bus: u8,
        chip_select: u8,
    },
    /// `port`は`/dev/ttyS{port}`に対応する（daemon側の命名規約、
    /// `socket.rs`参照）。`baud_rate`はそのポートの初回オープン時のみ有効
    /// （I2C/SPIと同じく、以降は既に開いた接続を使い回す）。
    Uart {
        port: u8,
        baud_rate: u32,
    },
}

impl From<&BusRef> for BusId {
    fn from(bus: &BusRef) -> Self {
        match *bus {
            BusRef::Gpio { pin } => BusId::Gpio(pin),
            // addrはロック粒度に含めない。同じバスの別アドレスへのアクセスも
            // トランザクション途中の割り込みから守るため、バス全体を排他する。
            BusRef::I2c { bus, .. } => BusId::I2c(bus),
            BusRef::Spi { bus, chip_select } => BusId::Spi(bus, chip_select),
            BusRef::Uart { port, .. } => BusId::Uart(port),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    // GPIO用: 1ビット単位。
    Read,
    Write {
        value: bool,
    },
    // I2C用: 方向が別々のバイト列（将来UARTでも流用予定）。
    ReadBytes {
        length: usize,
    },
    WriteBytes {
        data: Vec<u8>,
    },
    WriteReadBytes {
        data: Vec<u8>,
        length: usize,
    },
    // SPI用: 送信と同時に同じ長さを受信する全二重転送。
    Transfer {
        data: Vec<u8>,
    },
    // GPIO用（Tier 2）: エッジをタイムスタンプ付きで記録する。
    // `pre_pulse_low_ms`を指定すると、監視開始前にそのピンをLOWに駆動して
    // から`Some(ms)`ミリ秒待つ（DHT22等のスタート信号パターン）。
    WatchEdges {
        pre_pulse_low_ms: Option<u64>,
        max_events: usize,
        timeout_ms: u64,
    },
    Release,
}

#[derive(Debug, Serialize)]
pub struct Response {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// GPIO読み取りの結果（High=true）等、単一値を伴う成功レスポンス用。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<bool>,
    /// I2C読み取りの結果等、バイト列を伴う成功レスポンス用。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes: Option<Vec<u8>>,
    /// `WatchEdges`の結果。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub edges: Option<Vec<EdgeEventWire>>,
}

#[derive(Debug, Serialize)]
pub struct EdgeEventWire {
    pub timestamp_ns: u64,
    pub rising: bool,
}

impl Response {
    pub fn ok() -> Self {
        Self {
            ok: true,
            error: None,
            value: None,
            bytes: None,
            edges: None,
        }
    }

    pub fn value(value: bool) -> Self {
        Self {
            ok: true,
            error: None,
            value: Some(value),
            bytes: None,
            edges: None,
        }
    }

    pub fn bytes(data: Vec<u8>) -> Self {
        Self {
            ok: true,
            error: None,
            value: None,
            bytes: Some(data),
            edges: None,
        }
    }

    pub fn edges(events: Vec<EdgeEventWire>) -> Self {
        Self {
            ok: true,
            error: None,
            value: None,
            bytes: None,
            edges: Some(events),
        }
    }

    pub fn locked_by(holder: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("locked_by:{holder}")),
            value: None,
            bytes: None,
            edges: None,
        }
    }

    pub fn malformed(msg: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("malformed_request:{msg}")),
            value: None,
            bytes: None,
            edges: None,
        }
    }

    pub fn hw_error(msg: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("hw_error:{msg}")),
            value: None,
            bytes: None,
            edges: None,
        }
    }
}
