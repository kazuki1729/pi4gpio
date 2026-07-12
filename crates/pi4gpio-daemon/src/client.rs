//! クライアント識別。
//!
//! ローカルソケット接続では`SO_PEERCRED`（UID/PID）を識別子として使う
//! （NETWORK_POLICY.md：これがロック所有権モデルの識別子問題を同時に解決する）。
//! リモート経路（Tailscale限定bind＋APIキー）が有効な場合はAPIキーを識別子にする。
//! TODO: `SO_PEERCRED`の実際の取得（`UnixStream::peer_cred`）。

// socket.rsの接続受付ループがまだClientIdを生成していないため未使用。
// TODO: 配線が終わったらこの#[allow(dead_code)]は外す。
#[allow(dead_code)]
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum ClientId {
    Local { uid: u32, pid: u32 },
    Remote { api_key_id: String },
}
