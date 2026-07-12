//! SPIハードウェアアクセス（FEATURE_PRIORITY.md Tier 1）。
//!
//! `rpi-sensor-lib`のMCP3208系3用途（`spidev`経由）が主な移行対象
//! （MIGRATION_PLAN.md 移行順序3番目）。

use crate::error::HwError;

pub struct SpiDevice {
    _private: (),
}

impl SpiDevice {
    pub fn open(_bus: u8, _chip_select: u8) -> Result<Self, HwError> {
        Err(HwError::NotImplemented)
    }

    pub fn transfer(&mut self, _tx: &[u8], _rx: &mut [u8]) -> Result<(), HwError> {
        todo!()
    }
}
