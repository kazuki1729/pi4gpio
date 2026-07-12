//! I2Cハードウェアアクセス（FEATURE_PRIORITY.md Tier 1）。
//!
//! BSC (Broadcom Serial Controller) レジスタへの直接操作は未実装。
//! `rpi-sensor-lib`の`bme280_pressure.py`（`smbus2`経由）が主な移行対象
//! （MIGRATION_PLAN.md 移行順序2番目）。

use crate::error::HwError;

pub struct I2cBus {
    _private: (),
}

impl I2cBus {
    pub fn open(_bus: u8) -> Result<Self, HwError> {
        Err(HwError::NotImplemented)
    }

    pub fn read(&mut self, _addr: u8, _buf: &mut [u8]) -> Result<(), HwError> {
        todo!()
    }

    pub fn write(&mut self, _addr: u8, _data: &[u8]) -> Result<(), HwError> {
        todo!()
    }
}
