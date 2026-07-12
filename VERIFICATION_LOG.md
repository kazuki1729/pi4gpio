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

## 2026-07-12: GPIO実装（Tier 1）の実機検証

### 事前確認（作業前）
本番サービスのPID・保持デバイスに変化なしを確認。加えて今回は物理レジスタへの直接書き込みを伴うため、`gpioinfo`で本番使用ピン（GPIO6=ボタン、GPIO26=DHT22）・固定機能ピン（I2C: 2,3／SPI0: 7,8,9,10,11／UART: 14,15／1-Wireオーバーレイ: 4）を洗い出し、いずれとも重複しない**GPIO17**（consumer無しの未使用ピン）をテスト対象に選定した。

### 実施内容
- `pi4gpio-hw/gpio.rs`: `/dev/gpiomem`をmmapし、GPFSEL/GPSET/GPCLR/GPLEV/GPIO_PUP_PDN_CNTRL_REGnへの実レジスタアクセスを実装（`/dev/mem`ではなく`/dev/gpiomem`を選択——GPIOレジスタのページのみに露出範囲が限定され、`gpio`グループ権限でroot不要かつ他の物理アドレスへの誤アクセスリスクが無いため）
- `socket.rs`: `BusRef::Gpio`のディスパッチを実配線（I2C/SPI/UARTは引き続き`not_implemented`）
- `examples/gpio_smoke_test.rs`（新規）: 実機でのみ実行可能な検証用サンプル。プルアップ/ダウン・出力→読み戻しループバックを検証

### 実機での発見: PullMode符号の誤り
GPIO17でスタンドアロンサンプルを実行したところ、プルアップ/ダウンの結果が**きれいに入れ替わって**いた（`claim_input(Up)`→Low、`claim_input(Down)`→High）。出力ループバック側は正常だったため、ノイズではなくコード側の符号誤りと判断。BCM2711の`GPIO_PUP_PDN_CNTRL_REGn`は`0b01=プルダウン・0b10=プルアップ`だが、旧世代BCM2835の`GPPUD`のビット意味と混同し逆に実装していたことが判明。`PullMode`の数値を修正して再実行し、4項目すべて成功を確認した。

### 実施内容（修正後の最終確認）
1. スタンドアロンサンプル（`gpio_smoke_test 17`）: pull-up→High、pull-down→Low、output-high loopback→High、output-low loopback→Low、すべて成功
2. デーモン経由（`socat`+JSON）: `write(true)`→`{"ok":true,"value":true}`、`write(false)`→`{"ok":true,"value":false}`、範囲外ピン（9999）→`{"ok":false,"error":"hw_error:invalid pin or channel number: 9999"}`で適切に拒否
3. 本番サービスのPID・保持デバイス・GPIO6/26の状態（`bias=pull-up consumer="lg"`）に変化がないことを再確認
4. GPIO17をプルなし入力に後始末、一時ファイル・ソケット・プロセスを全て削除

### 結果
Tier 1のGPIO基本読み書きが実機で正しく動作することを確認した。**実機テストを行わなければプルアップ/ダウンが逆の実装のまま出荷していた**——このプロジェクトの検証方針（MIGRATION_PLAN.md §10、実機でしか検証できない）の妥当性を裏付ける結果となった。次はI2C（`bme280_pressure.py`相当）に進む。

## 2026-07-12: I2C実装（Tier 1）の実機検証

### 設計判断
GPIOと異なり、I2Cはカーネルのi2c-dev（`/dev/i2c-N`へのioctl）経由で実装した。クロックストレッチング・マルチマスター調停・NAK処理などプロトコル自体が複雑で、自前でBSCレジスタから再実装するリスクが、既に実績あるカーネルドライバに委ねるメリットに見合わないため（pigpio本家もハードウェアI2Cでは同じくi2c-dev ioctlを使う）。`RPi.bme280`パッケージがレジスタポインタ書き込み＋リピートスタート読み取りの結合トランザクションを必要とするため、単純な`read`/`write`とは別に`write_read`（`I2C_RDWR`で2メッセージを1回のioctlにまとめる）を実装した。ロックは`addr`単位ではなく`bus`単位——同じバス上の別デバイスへのアクセスも、トランザクション途中の割り込みから守るため。

### 事前確認（作業前）
本番サービスのPID・保持デバイスに変化なしを確認。I2Cは共有バスのためGPIOのような「未使用ピンを選ぶ」戦略が使えないが、Linuxカーネルはアダプタ単位でトランザクションを排他制御するため物理的な衝突（電気的な混線）は起きない。`i2cdetect`は未インストールのため追加インストールはせず、自作の`I2cBus`を使った読み取り専用プローブ（BME280/BMP280のチップIDレジスタ`0xD0`、副作用なし）で代替した。

### 実施内容・結果
`examples/i2c_smoke_test.rs`（新規）でバス1のアドレス`0x76`・`0x77`に対し`write_read([0xD0], 1)`を実行したところ、両方とも`os error 121`（Remote I/O error / EREMOTEIO）を返した。これは「リクエストの形式は正しいが対象デバイスがACKを返さなかった」というプロトコルレベルの正常なエラーであり、`ioctl`構造体やコマンド番号が誤っていれば出るはずの`EINVAL`/`ENOTTY`ではないため、実装の骨格（構造体レイアウト・アドレス/フラグ/長さ/バッファのエンコード・リピートスタートでのメッセージ連結）が正しく機能している傍証と判断した。

本番ログの「センサー基盤が未接続の疑いを検知しました」という既知の事象と一致しており、このPiには現在I2Cデバイスが物理的に接続されていない状態だった。ユーザー確認の上、**実際に正しいバイト列が読めることの確認（値レベルの検証）は持ち越し**とし、エラーパスの正しさが確認できた時点で先に進めることにした。センサーを接続し直せる状況になった時点で`i2c_smoke_test`を再実行し、チップIDレジスタが`0x60`（BME280）または`0x58`（BMP280）として正しく読めるかを確認する。

本番サービスのPID・保持デバイスに最後まで変化なし。

## 2026-07-12: SPI実装（Tier 1）の実機検証

### 設計判断
I2Cと同じ理由で、カーネルのspidev（`/dev/spidevB.D`へのioctl）経由で実装した。`SPI_IOC_MESSAGE`ioctlで全二重転送（`spidev.SpiDev.xfer2()`相当）を行い、モード0を明示的に設定（前の利用者が別モードへ変更している可能性があるため）。ioctl番号はマジックナンバーを直書きせず、カーネルの`_IOC`ビットパッキングと同じ計算式をRust側で再現して導出した。`struct spi_ioc_transfer`が32バイトであることをコンパイル時`const assert`で検証し、レイアウトのズレが静かに紛れ込まないようにした（GPIOのPullMode符号バグと同種のリスクへの対策）。

### 事前確認（作業前）
本番サービスのPID・保持デバイス（`/dev/spidev0.0`）に変化なしを確認。SPIも共有バスだが、Linuxカーネルはspidevもi2c-devと同様アダプタ単位でトランザクションを排他制御するため物理的な衝突は起きない。ただし本番スクリプト内のコメントから、MCP3208を含む「センサー基盤」自体が物理的に未接続の疑いがあることが判明（`DISCONNECT_FLOOR_VALUES`という既知の未接続判定ロジックが存在）。

### 実施内容・結果
SPIはI2Cと違いACK/NACKが無いため、「応答があるかどうか」では正誤判定できない。そこで、`grove_mcp3208_sensors.py`と同じMCP3208チャンネル0読み取りコマンド（`[0x06, 0x00, 0x00]`）を、本番稼働中の`/dev/spidev0.0`に対して**Rust実装とPython(`spidev`)の両方で実行し、受信バイト列を突き合わせた**。

- Rust（`examples/spi_smoke_test.rs`）: `rx=[00, 00, 00]` value=0
- Python（`spidev.xfer2`、本番と同じvenv）: `rx=['0x0','0x0','0x0']` value=0

**完全に一致**。`ioctl`構造体レイアウトや転送ロジックが誤っていれば、少なくともどちらか一方は異なる結果になるはずのため、これは実装の物理層での正しさを裏付ける強い証拠と判断した。`value=0`という結果自体は、GPIO/I2Cで確認済みの「センサー基盤が未接続」という状況（`DISCONNECT_FLOOR_VALUES`の`light_raw=0.0`パターン）と整合している。

デーモン経由（`socat`+JSON）でも同じ`{"ok":true,"bytes":[0,0,0]}`を確認。SPIバスに不適合な操作（`read_bytes`）を送ると`malformed_request`で正しく拒否されることも確認した。

本番サービスのPID・保持デバイスに最後まで変化なし。テスト用の一時ファイル・プロセスを全て削除。

### 結果
Tier 1のSPI基本転送が実機で正しく動作することを確認した。ACK/NACKが無いSPIでも、既存の実績あるPython実装との突き合わせという方法で、実機センサーが無い状態でも実装の正しさを検証できることを確認した。GPIO・I2C・SPIが揃い、Tier 1の残りはUART（`mh_z19c_co2.py`相当）のみ。
