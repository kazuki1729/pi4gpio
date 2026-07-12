//! ワイヤープロトコル（雛形段階）。
//!
//! 改行区切りJSON（1行1リクエスト/1行1レスポンス）。バイナリ化・多重化などの
//! 最適化は、Tier 1操作が実装されパフォーマンス要件が明確になってから検討する
//! （現段階ではPythonクライアント側での可読性・実装のしやすさを優先）。
//!
//! `Read`/`Write`はバスを暗黙に確保する（未確保なら`LockTable::try_acquire`）。
//! 確保したバスは`Release`または切断（`socket.rs`の接続ハンドラ側で処理）まで
//! そのクライアントが保持し続ける——SESSION_HANDOFF.md §3の
//! 「I2C/SPIの複数ステップ通信を他クライアントの割り込みから守る」を満たすため。

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
    I2c { bus: u8 },
    Spi { bus: u8, chip_select: u8 },
    Uart { port: u8 },
}

impl From<&BusRef> for BusId {
    fn from(bus: &BusRef) -> Self {
        match *bus {
            BusRef::Gpio { pin } => BusId::Gpio(pin),
            BusRef::I2c { bus } => BusId::I2c(bus),
            BusRef::Spi { bus, chip_select } => BusId::Spi(bus, chip_select),
            BusRef::Uart { port } => BusId::Uart(port),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Operation {
    Read,
    Write { value: bool },
    Release,
}

#[derive(Debug, Serialize)]
pub struct Response {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// GPIO読み取りの結果（High=true）等、値を伴う成功レスポンス用。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<bool>,
}

impl Response {
    pub fn ok() -> Self {
        Self {
            ok: true,
            error: None,
            value: None,
        }
    }

    pub fn value(value: bool) -> Self {
        Self {
            ok: true,
            error: None,
            value: Some(value),
        }
    }

    pub fn not_implemented() -> Self {
        Self {
            ok: false,
            error: Some("not_implemented".to_string()),
            value: None,
        }
    }

    pub fn locked_by(holder: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("locked_by:{holder}")),
            value: None,
        }
    }

    pub fn malformed(msg: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("malformed_request:{msg}")),
            value: None,
        }
    }

    pub fn hw_error(msg: &str) -> Self {
        Self {
            ok: false,
            error: Some(format!("hw_error:{msg}")),
            value: None,
        }
    }
}
