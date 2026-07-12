//! クライアント識別。
//!
//! ローカルソケット接続では`SO_PEERCRED`（UID/PID）を識別子として使う
//! （NETWORK_POLICY.md：これがロック所有権モデルの識別子問題を同時に解決する）。
//! リモート経路（Tailscale限定bind＋APIキー）が有効な場合はAPIキーを識別子にする。

use std::io;
use tokio::net::UnixStream;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum ClientId {
    Local {
        uid: u32,
        pid: u32,
    },
    // NETWORK_POLICY.mdのTailscale限定bind＋APIキー実装まで未使用。
    #[allow(dead_code)]
    Remote {
        api_key_id: String,
    },
}

impl ClientId {
    pub fn from_unix_stream(stream: &UnixStream) -> io::Result<Self> {
        let cred = stream.peer_cred()?;
        Ok(ClientId::Local {
            uid: cred.uid(),
            pid: cred.pid().unwrap_or(0) as u32,
        })
    }
}
