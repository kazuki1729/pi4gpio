//! Unixドメインソケットサーバ。
//!
//! NETWORK_POLICY.mdの決定に基づき、この雛形段階ではローカルソケットのみを
//! 実装対象とする。Tailscale限定bindは`MIGRATION_PLAN.md` Phase 0以降、
//! 実際にリモート制御が必要になった時点で追加する。

use crate::config::Config;
use std::io;
use tokio::net::UnixListener;

pub async fn serve(config: &Config) -> io::Result<()> {
    if let Some(parent) = std::path::Path::new(&config.socket_path).parent() {
        std::fs::create_dir_all(parent)?;
    }
    let _ = std::fs::remove_file(&config.socket_path);

    let listener = UnixListener::bind(&config.socket_path)?;
    println!("pi4gpiod: listening on {}", config.socket_path);

    loop {
        let (_stream, _addr) = listener.accept().await?;
        // TODO: SO_PEERCRED取得 → ClientId生成 → ワイヤープロトコルのリクエストループへディスパッチ
        eprintln!("pi4gpiod: client connected (request handling not yet implemented)");
    }
}
