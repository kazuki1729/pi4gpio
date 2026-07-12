//! バス単位・トランザクション単位のロック機構。
//!
//! `rpi-hw-lock`の「サービス単位の排他（stop/restartで明け渡す）」から、
//! 「複数クライアントが1つのデーモンを時分割共有する」設計への転換を担う
//! （SESSION_HANDOFF.md §3、MIGRATION_PLAN.md）。
//!
//! TODO: 優先度付け・タイムアウト・デッドロック回避。
//!
//! クライアント切断時の自動解放は、`socket.rs`の接続ハンドラが保持中の
//! `BusId`集合を追跡し、ループ終了時（正常/異常問わず）に`release`を
//! 呼ぶことで実現している。

use crate::client::ClientId;
use std::collections::HashMap;
use std::sync::Mutex;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum BusId {
    Gpio(u32),
    I2c(u8),
    Spi(u8, u8),
    Uart(u8),
}

#[derive(Default)]
pub struct LockTable {
    holders: Mutex<HashMap<BusId, ClientId>>,
}

impl LockTable {
    pub fn new() -> Self {
        Self::default()
    }

    /// 既に他クライアントが保持している場合は、保持者の`ClientId`を返す。
    pub fn try_acquire(&self, bus: BusId, client: ClientId) -> Result<(), ClientId> {
        let mut holders = self.holders.lock().expect("lock table poisoned");
        match holders.get(&bus) {
            Some(existing) if *existing != client => Err(existing.clone()),
            _ => {
                holders.insert(bus, client);
                Ok(())
            }
        }
    }

    pub fn release(&self, bus: BusId, client: &ClientId) {
        let mut holders = self.holders.lock().expect("lock table poisoned");
        if holders.get(&bus) == Some(client) {
            holders.remove(&bus);
        }
    }
}
