//! バス単位・トランザクション単位のロック機構。
//!
//! `rpi-hw-lock`の「サービス単位の排他（stop/restartで明け渡す）」から、
//! 「複数クライアントが1つのデーモンを時分割共有する」設計への転換を担う
//! （SESSION_HANDOFF.md §3、MIGRATION_PLAN.md）。
//!
//! TODO: 優先度付け・タイムアウト・デッドロック回避。
//! TODO: クライアント切断検知時の自動解放（ソケットクローズをトリガーに）。

use crate::client::ClientId;
use std::collections::HashMap;
use std::sync::Mutex;

// socket.rsの接続受付ループがまだLockTableを配線していないため未使用。
// TODO: 配線が終わったらこの#[allow(dead_code)]は外す。
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum BusId {
    Gpio(u32),
    I2c(u8),
    Spi(u8, u8),
    Uart(u8),
}

#[allow(dead_code)]
#[derive(Default)]
pub struct LockTable {
    holders: Mutex<HashMap<BusId, ClientId>>,
}

#[allow(dead_code)]
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
