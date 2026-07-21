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

#[cfg(test)]
mod tests {
    use super::*;

    fn client(pid: u32) -> ClientId {
        ClientId::Local { uid: 1000, pid }
    }

    #[test]
    fn same_client_can_reacquire_a_bus() {
        let locks = LockTable::new();
        let owner = client(10);
        let bus = BusId::Uart(0);

        assert_eq!(locks.try_acquire(bus, owner.clone()), Ok(()));
        assert_eq!(locks.try_acquire(bus, owner), Ok(()));
    }

    #[test]
    fn another_client_cannot_take_a_held_bus() {
        let locks = LockTable::new();
        let owner = client(10);
        let contender = client(20);
        let bus = BusId::I2c(1);

        assert_eq!(locks.try_acquire(bus, owner.clone()), Ok(()));
        assert_eq!(locks.try_acquire(bus, contender), Err(owner));
    }

    #[test]
    fn non_owner_release_does_not_unlock_the_bus() {
        let locks = LockTable::new();
        let owner = client(10);
        let contender = client(20);
        let bus = BusId::Gpio(17);

        assert_eq!(locks.try_acquire(bus, owner.clone()), Ok(()));
        locks.release(bus, &contender);
        assert_eq!(locks.try_acquire(bus, contender), Err(owner));
    }

    #[test]
    fn disconnect_cleanup_releases_every_bus_owned_by_the_client() {
        let locks = LockTable::new();
        let disconnected = client(10);
        let next_client = client(20);
        let held = [BusId::Gpio(6), BusId::Spi(0, 0), BusId::Uart(0)];

        for bus in held {
            assert_eq!(locks.try_acquire(bus, disconnected.clone()), Ok(()));
        }
        // socket.rsの切断処理と同じく、接続が追跡していた全BusIdを解放する。
        for bus in held {
            locks.release(bus, &disconnected);
        }
        for bus in held {
            assert_eq!(locks.try_acquire(bus, next_client.clone()), Ok(()));
        }
    }
}
