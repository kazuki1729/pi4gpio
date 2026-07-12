//! ワイヤープロトコル（雛形段階）。
//!
//! 改行区切りJSON（1行1リクエスト/1行1レスポンス）。バイナリ化・多重化などの
//! 最適化は、Tier 1操作が実装されパフォーマンス要件が明確になってから検討する
//! （現段階ではPythonクライアント側での可読性・実装のしやすさを優先）。
//!
//! `Operation`はGPIO用（`Read`/`Write`、1ビット単位）とI2C用
//! （`ReadBytes`/`WriteBytes`/`WriteReadBytes`、バイト列単位）に分かれる。
//! バスの種類に合わない操作が来た場合は`socket.rs`の`dispatch`が
//! `malformed`で拒否する。
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
    Gpio { pin: u32 },
    I2c { bus: u8, addr: u8 },
    Spi { bus: u8, chip_select: u8 },
    Uart { port: u8 },
}

impl From<&BusRef> for BusId {
    fn from(bus: &BusRef) -> Self {
        match *bus {
            BusRef::Gpio { pin } => BusId::Gpio(pin),
            // addrはロック粒度に含めない。同じバスの別アドレスへのアクセスも
            // トランザクション途中の割り込みから守るため、バス全体を排他する。
            BusRef::I2c { bus, .. } => BusId::I2c(bus),
            BusRef::Spi { bus, chip_select } => BusId::Spi(bus, chip_select),
            BusRef::Uart { port } => BusId::Uart(port),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    // GPIO用: 1ビット単位。
    Read,
    Write { value: bool },
    // I2C用: バイト列単位（将来SPI/UARTでも流用予定）。
    ReadBytes { length: usize },
    WriteBytes { data: Vec<u8> },
    WriteReadBytes { data: Vec<u8>, length: usize },
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
}

impl Response {
    pub fn ok() -> Self {
        Self {
            ok: true,
            error: None,
            value: None,
            bytes: None,
        }
    }

    pub fn value(value: bool) -> Self {
        Self {
            ok: true,
            error: None,
            value: Some(value),
            bytes: None,
        }
    }

    pub fn bytes(data: Vec<u8>) -> Self {
        Self {
            ok: true,
            error: None,
            value: None,
            bytes: Some(data),
        }
    }

    pub fn not_implemented() -> Self {
        Self {
            ok: false,
            error: Some("not_implemented".to_string()),
            value: None,
            bytes: None,
        }
    }

    pub fn locked_by(holder: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("locked_by:{holder}")),
            value: None,
            bytes: None,
        }
    }

    pub fn malformed(msg: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("malformed_request:{msg}")),
            value: None,
            bytes: None,
        }
    }

    pub fn hw_error(msg: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("hw_error:{msg}")),
            value: None,
            bytes: None,
        }
    }
}
