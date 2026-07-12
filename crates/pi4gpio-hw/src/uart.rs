//! UARTハードウェアアクセス（FEATURE_PRIORITY.md Tier 1）。
//!
//! `rpi-sensor-lib`の`mh_x19c_co2.py`（`serial`経由）が主な移行対象
//! （MIGRATION_PLAN.md 移行順序4番目）。

use crate::error::HwError;

pub struct UartPort {
    _private: (),
}

impl UartPort {
    pub fn open(_device: &str, _baud_rate: u32) -> Result<Self, HwError> {
        Err(HwError::NotImplemented)
    }

    pub fn read(&mut self, _buf: &mut [u8]) -> Result<usize, HwError> {
        todo!()
    }

    pub fn write(&mut self, _data: &[u8]) -> Result<usize, HwError> {
        todo!()
    }
}
