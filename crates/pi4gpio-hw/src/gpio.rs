//! GPIO基本読み書き（FEATURE_PRIORITY.md Tier 1）。
//!
//! 実際のBCM2711レジスタ操作（GPFSELn/GPSETn/GPCLRn/GPLEVn への
//! メモリマップドI/O）は未実装。`GpioChip::open`が`/dev/mem`（または
//! 将来的に`gpiochip`キャラクタデバイス）を掴む段階から実装が必要。

use crate::error::HwError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PullMode {
    None,
    Up,
    Down,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Low,
    High,
}

pub struct GpioChip {
    _private: (),
}

impl GpioChip {
    pub fn open() -> Result<Self, HwError> {
        Err(HwError::NotImplemented)
    }

    pub fn claim_output(&mut self, _pin: u32) -> Result<(), HwError> {
        todo!("GPFSELn への出力モード設定")
    }

    pub fn claim_input(&mut self, _pin: u32, _pull: PullMode) -> Result<(), HwError> {
        todo!("GPFSELn への入力モード設定 + プルアップ/ダウン設定")
    }

    pub fn write(&mut self, _pin: u32, _level: Level) -> Result<(), HwError> {
        todo!("GPSETn / GPCLRn への書き込み")
    }

    pub fn read(&self, _pin: u32) -> Result<Level, HwError> {
        todo!("GPLEVn の読み取り")
    }
}
