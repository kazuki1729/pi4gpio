//! 設定読み込み。
//!
//! 雛形段階ではデフォルト値のみを返す。
//! TODO: 設定ファイルからの読み込み、Tailscale限定bindオプション、
//! APIキー設定（NETWORK_POLICY.md）を実装する。

pub struct Config {
    pub socket_path: String,
}

impl Config {
    pub fn load() -> Self {
        Self {
            socket_path: "/run/pi4gpio/pi4gpio.sock".to_string(),
        }
    }
}
