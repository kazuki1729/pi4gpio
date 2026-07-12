# 実機検証ログ（Verification Log）

実機（Raspberry Pi 4、`kazuki1729.local`）でのpi4gpio雛形の動作確認記録。今後も実機に触れる際はこの形式で記録する（事前確認の手順自体は継続ルール化済み）。

## 2026-07-12: 雛形の初回起動確認

### 事前確認（作業前）
- `sensor-tiered-client.service`（本番、PID 2768）が稼働中であることを確認
- 保持デバイス: `/dev/spidev0.0`（SPI）・`/dev/gpiochip0`（GPIO）・`/dev/i2c-1`（I2C）・`/dev/ttyS0`（UART）
- pi4gpio雛形はハードウェア層が全て`todo!()`未配線でこれらのデバイスを一切開かないため、衝突なしと判断
- ディスク42GB空き、メモリ6.4GB空き、Rust未インストール、`~`に既存ファイルなしを確認

### 実施内容
1. `pi`ユーザー権限でrustupをインストール（`$HOME/.cargo`配下のみ、システム全体には影響なし）
2. GitHubリポジトリが非公開のため、ローカル作業ツリーを`tar`＋`scp`で転送（`.git`/`target`除外）
3. 実機ネイティブで`cargo build --release`（36.5秒で成功）
4. `/run/pi4gpio`を手動作成（`sudo mkdir` + `chown pi:pi`、systemdの`RuntimeDirectory=`相当を手動再現）
5. `pi4gpiod`をフォアグラウンド起動、ソケットのbind・接続受付を確認
6. `socat`でソケットに接続し、デーモンが接続を認識してログ出力することを確認（リクエスト処理は未実装のため即切断——想定通り）
7. 起動前後で本番サービスのPID・保持デバイスに変化がないことを再確認
8. プロセス停止、一時ファイル・ソケットディレクトリを削除して痕跡なしを確認

### 結果
雛形は実機aarch64で正常にビルド・起動し、本番サービスへの影響はゼロだった。

### 見つかった課題
1. **`config.rs`にソケットパスの上書きオプションが無い**: `/run`直下は非root権限で作成できず、今回は`sudo mkdir`で手動回避した。本番はsystemdの`RuntimeDirectory=`が自動処理するため実運用上は問題にならないが、非systemd環境での手動起動がしづらい
2. **シグナルハンドラ未実装**: プロセス終了時にソケットファイルが掃除されない。`socket.rs`は起動時に既存ソケットを削除する処理があるため実害は無いが、正規のgraceful shutdown（`SIGTERM`受信→接続中クライアントへの通知→ソケットファイル削除）は今後必要

いずれも同日中に解消（後述）。

## 2026-07-12: ワイヤープロトコル・ロック機構・課題1/2の修正確認

### 変更内容
- `config.rs`: `PI4GPIO_SOCKET_PATH`環境変数でソケットパスを上書き可能に（課題1の修正）
- `socket.rs`: `SIGTERM`/`SIGINT`受信時にソケットファイルを削除してから終了（課題2の修正）
- `protocol.rs`（新規）: 改行区切りJSONのワイヤープロトコル。`Read`/`Write`/`Release`操作、`Gpio`/`I2c`/`Spi`/`Uart`のバス参照
- `client.rs`: `UnixStream::peer_cred()`による実際の`SO_PEERCRED`取得を実装
- `lock.rs`: `#[allow(dead_code)]`を撤去し、`socket.rs`の接続ハンドラから実配線

### 事前確認（作業前）
前回と同一（`sensor-tiered-client.service` PID 2768が変化なく稼働中、保持デバイスも同一、`/run/pi4gpio`残存なし）を再確認した上で着手。

### 実施内容・確認結果
1. ローカル（Windows、`rustup`導入済み）で`cargo check`/`clippy`/`fmt`を`aarch64-unknown-linux-gnu`ターゲットに対して実行し、警告ゼロを確認してから転送
2. 実機へ転送・`cargo build --release`成功
3. `PI4GPIO_SOCKET_PATH=/home/pi/pi4gpio-dev.sock`で`sudo`無し起動に成功（課題1解消を確認）
4. `socat`で3接続を使ったシナリオを実施:
   - 接続1がGPIOピン17をreadで確保・保持
   - 接続2（別`pid`）が同じピンにアクセス→`{"ok":false,"error":"locked_by:Local { uid: 1000, pid: ... }"}`で正しく拒否
   - 接続1切断後、接続3が同じピンにアクセス→ロックが自動解放されていることを確認
5. 不正なJSON送信→`malformed_request`で適切にエラー応答
6. 明示的な`Release`操作→`{"ok":true}`で成功応答
7. `SIGTERM`送信→ログに「shutting down」記録、ソケットファイルが自動削除、プロセスも正常終了（課題2解消を確認）
8. 本番サービスのPID・保持デバイスに変化がないことを再確認、テスト用の一時ファイル・プロセスを全て削除

### 結果
ロック機構・クライアント識別（`SO_PEERCRED`）・ワイヤープロトコルの一連の流れが実機で end-to-end に動作することを確認した。ハードウェア操作自体（GPIO/I2C/SPI/UARTの実レジスタ操作）は`pi4gpio-hw`が未実装のため、全リクエストは`not_implemented`を返す。次はTier 1（`FEATURE_PRIORITY.md`）の実装に進む。
