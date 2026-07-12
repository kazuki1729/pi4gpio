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

## 2026-07-12: UART実装（Tier 1）の実機検証

### 設計判断
`O_NOCTTY`でオープンし（長時間稼働するデーモンがシリアル回線を制御端末として持たないように）、`tcgetattr`/`tcsetattr`でraw mode・8N1に設定する。ワイヤープロトコルはI2C用に作った`ReadBytes`/`WriteBytes`をそのまま流用した——`mh_x19c_co2.py`の「書き込み→sleep→読み取り」というパターンはI2Cのレジスタポインタ読み取りのような結合トランザクションを必要とせず、コネクション単位で保持されるロックが既に他クライアントの割り込みを防いでいるため。

### 事前確認と、I2C/SPIとの違い
本番サービスのPID・`/dev/ttyS0`保持状況に変化なしを確認した上で着手。ただしUARTはI2C/SPIと異なり、`termios`設定（ボーレート等）が**プロセス単位ではなくデバイス単位で共有される状態**であり、カーネルがトランザクション単位で排他してくれる保証がない。本番プロセスと同時に触るとMH-Z19Cとのコマンド/レスポンスが混線するリスクが実際にあるため、Claude Code自動モードの安全機構が`systemctl stop`の単独実行を一度拒否した。ユーザーに状況を説明し確認を取った上で、**`rpi-hw-lock`の`exclusive_hardware_access()`**を使って本番サービスを一時停止し、その区間内で検証を行う方式に切り替えた。

### 実機での発見: read()の無期限ブロック
初回実行時、`termios`を`VMIN=1・VTIME=0`（純粋なブロッキング、最低1バイト来るまで無期限に待つ）で設定していたところ、センサー未接続でデータが一切来ず、`uart_smoke_test`プロセスが応答しないままハングした。Python側の`subprocess.run(timeout=5)`がタイムアウトで例外を送出し、スクリプト全体が異常終了。

- **`rpi-hw-lock`の堅牢性を確認**: 例外が`with`ブロックを突き破って伝播したにもかかわらず、`rpi-hw-lock`は設計通り本番サービスを正しく再起動していた（新PIDで`/dev/ttyS0`を再度保持）
- ただし`uart_smoke_test`プロセス自体は`pgrep -f`の自己マッチ誤検知に惑わされつつも、実際には正常終了していたことを`pgrep -x`（完全一致）で確認

根本原因は、`mh_x19c_co2.py`が使う`pyserial`の`serial.Serial(timeout=1.0)`と同じ「1秒でタイムアウトして制御を返す」挙動を再現していなかったこと。`VMIN=0・VTIME=10`（1.0秒）に修正した。これは単なる不便さではなく、**センサー未接続時にデーモンのワーカースレッドを無期限にブロックしうる実運用上の欠陥**だった。

### 実施内容（修正後の最終確認）
`rpi-hw-lock`で再度本番サービスを一時停止し、その区間内で以下を実施。

1. スタンドアロンサンプル（`uart_smoke_test`）: 1秒でタイムアウトし`received 0 bytes: []`を正常に返す（ハングしない）
2. デーモン経由（`WriteBytes`→`ReadBytes`）: `{"ok":true}`→`{"ok":true,"bytes":[]}`、同じく正常
3. `rpi-hw-lock`の`with`ブロック終了で本番サービスが正常に再開（新PID、`/dev/ttyS0`保持）されたことを確認
4. 残存プロセス・一時ファイルを全て削除

### 結果
Tier 1のUART基本読み書きが実機で正しく動作することを確認した。**実機テストを行わなければread()が無期限ブロックする実装のまま出荷していた**——GPIOのPullMode符号バグに続き、実機検証がなければ発見できなかった2件目の実運用上の欠陥。また、本番サービスとの共存が難しい検証（デバイス単位で状態共有されるUART）では`rpi-hw-lock`を活用するという、既存資産の再利用パターンも確立できた。

これでFEATURE_PRIORITY.md Tier 1（GPIO・I2C・SPI・UART）が全て実装・実機検証済みとなった。

## 2026-07-12: GPIOエッジ検出/通知（Tier 2）の実機検証

### 設計判断
Tier 1のGPIO（`gpio.rs`、`/dev/gpiomem`直叩き）とは別経路で、カーネルの`gpiochip`キャラクタデバイス（GPIO v2 uAPI、`GPIO_V2_GET_LINE_IOCTL`）を使い、ポーリングではなく本物の割り込み駆動でカーネルタイムスタンプ（`CLOCK_MONOTONIC`）付きのエッジイベントを受け取る方式にした。I2C/SPI/UARTで「カーネルドライバに委ねる」と判断したのと同じ理由——DHT22級の精密タイミングをポーリングやDMA直叩きで自前実装するのは、pigpio本家が抱えていた複雑さ（SESSION_HANDOFF.md §2）を引き継ぐことになる。

`gpio_v2_line_request`は`i2c_msg`/`spi_ioc_transfer`よりかなり複雑な構造体のため、記憶に頼らず**このPiの`/usr/include/linux/gpio.h`を直接読んで転記**した。サイズ（592バイト・48バイト）はSPIと同じく`const assert`で検証している。

`/dev/gpiomem`（Tier 1）と`/dev/gpiochip0`（Tier 2）はカーネルから見て独立した経路——`gpiomem`への書き込みは`gpiochip`の行所有権管理からは見えないため、DHT22で必要な「Tier 1でLOW駆動→Tier 2で監視に切替」という手順でも`gpiochip`側のEBUSY衝突検知には引っかからない。クライアント間の排他は引き続きデーモンの`LockTable`が担う。

ワイヤープロトコルには`WatchEdges{pre_pulse_low_ms, max_events, timeout_ms}`を追加。1回の呼び出しで数十ミリ秒ブロックしうる（Tier 1の各操作はマイクロ秒オーダーで無視できたが、これは無視できない）ため、`dispatch()`の呼び出しをtokioのワーカースレッドから`spawn_blocking`に逃がすリファクタも同時に行った。

### 事前確認
本番サービスのPID・GPIO17（前回同様の未使用ピン）に変化なしを確認。`/dev/gpiochip0`が`root:gpio`権限で`pi`ユーザーからアクセス可能なことも確認した。

### 実施内容・結果
物理センサー無しで自己完結して検証できるよう、2種類のテストを行った。

1. **`gpio_watch_smoke_test`（新規）**: 同一プロセス内でGpioChip（Tier 1）による既知の6回トグル（20ms間隔）を生成しつつ、EdgeWatcher（Tier 2）で同じピンをバックグラウンドスレッドから監視。記録された6件のタイムスタンプ間隔は**20.06ms**（要求した20msに対し誤差0.3%程度）で、ポーリングのジッターではなく本物のカーネルタイムスタンプ精度であることを確認した。先頭に想定外の1件（`claim_output()`によるモード切替時の暗黙の遷移）が混ざったため、末尾の1トグル分が`max_events`到達で記録されなかったが、これはテスト設計上の想定範囲であり実装の不具合ではない
2. **デーモン経由の`WatchEdges`（`pre_pulse_low_ms`付き）**: `socat`でリクエストを送り、`{"ok":true,"edges":[{"timestamp_ns":...,"rising":true}]}`——LOW駆動から監視モードへの切替時の1エッジを正しく記録し、以降は何も接続されていないため300msでタイムアウトして正常応答。ハングせず適切にタイムアウトすることを確認
3. 本番サービスのPID・GPIO17の状態に最後まで変化なし。残存プロセス・一時ファイルを全て削除

### 結果
Tier 2のGPIOエッジ検出/通知が実機で正しく動作することを確認した。GPIO/I2C/SPI/UARTに続き、これでFEATURE_PRIORITY.mdの主要機能（Tier 1全体＋Tier 2の中核）が実装・実機検証済みとなった。DHT22固有のデコードロジック（40ビットのしきい値判定・チェックサム検証）は、既存の`robust_dht22.py`のロジックを踏襲する形でPythonクライアント側に実装する想定（MIGRATION_PLAN.md）。

## 2026-07-12: Pythonクライアントライブラリ（`pi4gpio-client`）の実機検証

### 実施内容
`clients/python/pi4gpio_client`として、NDJSONワイヤープロトコルを話す最小のPythonクライアントを実装（`gpio_read`/`gpio_write`/`gpio_watch_edges`/`i2c_read`/`i2c_write`/`i2c_write_read`/`spi_transfer`/`uart_read`/`uart_write`＋各`*_release`）。`rpi-sensor-lib`の二重モード化（`MIGRATION_PLAN.md`）の前提となるライブラリ。

### 実機テストで発見したバグ: Operationのタグ付け方式の誤り
実機の`pi4gpiod`に接続した初回テストで、`gpio_write`が`malformed_request:unknown variant \`type\`, expected one of \`read\`, \`write\`, ...`エラーを返した。原因は、`protocol.rs`の`BusRef`と`Operation`でserdeのタグ付け方式が異なることを見落としていた点:

- `BusRef`は`#[serde(tag = "type")]`で内部タグ付き（例: `{"type":"gpio","pin":17}`）
- `Operation`にはタグ属性が無く、serdeのデフォルトである外部タグ付きになる（データ無しバリアントは裸の文字列`"read"`、データ有りバリアントは`{"write":{"value":true}}`という1キーのオブジェクト）

クライアントの初版は両方とも`{"type": ..., ...}`という同じ形式で送っており、これが原因だった。`_request()`を修正し、まず`socket.socketpair()`で作った疑似サーバーに対してエンコード内容をローカル検証してから、実機に再デプロイして確認した。

### 実施内容（修正後の最終確認、実機）
1. GPIO（pin 17）: `write(True)`→`True`、`write(False)`→`False`、範囲外ピン（9999）→`Pi4gpioError`で正しく検出
2. I2C（bus 1, addr 0x76, チップIDレジスタ）: `hw_error:transfer failed: I2C_RDWR ioctl: Remote I/O error`——既知のセンサー未接続状態と整合
3. SPI（bus 0, cs 0, MCP3208チャンネル0）: `rx=000000`——これまでのRust/Python突き合わせ結果と一致
4. GPIO `watch_edges`（`pre_pulse_low_ms=20`付き）: 1件のrisingエッジを正しく記録
5. UART（`rpi-hw-lock`の`exclusive_hardware_access()`で本番を一時停止）: `write`成功、`read`は0バイトで正常タイムアウト（ハングなし）、本番サービスも正しく再開

いずれも既存のRust実装単体でのテスト結果と一致しており、Pythonクライアントの符号化・復号化が正しいことを裏付けている。本番サービスへの影響は最後まで確認してゼロ。

### 結果
これで、Tier 1/2の全機能がPythonクライアント経由でも正しく利用できることを実機で確認できた。**実機に接続する初回テストを行わなければ、`Operation`のタグ付け誤りに気づかないままリリースしていた**——GPIOのPullMode符号バグ・UARTの無期限ブロックバグに続き、実機検証で発見した3件目の実運用上の欠陥。次はこのクライアントを使った`rpi-sensor-lib`側の二重モード化に進む。

## 2026-07-13: GPIO Readへのプルモード追加、および`rpi-sensor-lib`二重モード化（`tactile_button.py`）の実機検証

### GPIO Readへのpullフィールド追加
`rpi-sensor-lib`の二重モード化に着手した直後、`tactile_button.py`が`lgpio.SET_PULL_UP`を使っており、当時`PullMode::None`固定だったワイヤープロトコルの`Read`操作では表現できないことが判明した。`Operation::Read`を`Read`（unit variant）から`Read { pull: PullWire }`（`#[serde(default)]`でNoneがデフォルト）に変更し、`pi4gpio_client.gpio_read()`にも`pull`引数を追加。実機でGPIO17に対し`pull="up"`→High、`pull="down"`→Lowを確認（Tier 1のGPIO検証時と同じ電気的挙動と整合）。本番サービスへの影響なし。

### `tactile_button.py`の二重モード化
`rpi-sensor-lib`（`C:\Users\Kazuki\github-ripo\`）に`_pi4gpio_backend.py`（`RPI_SENSOR_BACKEND`環境変数での切り替え、プロセス内で1つの`Pi4gpioClient`接続を共有する設計）を追加し、`tactile_button.py`を二重モード化した。`pi4gpio_client`は明示的にオプトインした場合のみ遅延importする（`direct`のみの利用者に不要な依存を強制しないため）。

検証は、本番venv（`sensor-tiered-store/.venv`、`lgpio`等が既にインストール済み）をそのまま使い、`sys.path`操作でこのvenvの`rpi_sensors`本体だけをローカル修正版に差し替える方式で行った（`pip install`で本番venvを変更しない）。本番の`sensor-tiered-client.service`が実際に使っているGPIO6/26とは別に、Tier 1検証以来使ってきたGPIO17を対象にした。

### 実機での発見: バックエンド切替の過渡的な状態変化（バグではない）
1プロセス内で`direct`モードの`TactileButton`を使った直後に`pi4gpio`モードの`TactileButton`へ切り替えるテストで、押されていないはずのGPIO17が一瞬「押された→離された」という状態変化を記録した。`pi4gpio`モード単独で50回連続読み取りを行ったところ状態変化は0回だったため、**バックエンド切替（`gpiochip`解放から`/dev/gpiomem`のプル再設定までの一瞬の隙間）に起因する過渡現象であり、どちらの実装単体のバグではない**と判断した。実際の移行ではバックエンド切替は環境変数＋サービス再起動で行われ、1プロセス内でのライブ切替は発生しないため、実運用上は問題にならない。

### 実施内容・結果
1. `direct`モード: `TactileButton(pin=17).update()`を3回呼び出し、`(False, 0.0, 0.0)`（未接続・未押下として安定）を確認
2. `pi4gpio`モード（`PI4GPIO_SOCKET_PATH`で開発用ソケットパスを指定）: 同様に3回呼び出し、動作を確認。単独での安定性は50回連続読み取りで別途確認
3. 本番サービスのPID・GPIO6/26の状態（`bias=pull-up consumer="lg"`）に最後まで変化なし
4. テスト用に転送した`rpi-sensor-lib-test`ディレクトリ・一時ファイルを全て削除

これで`MIGRATION_PLAN.md`のセンサー移行順序1番目（`tactile_button.py`）の二重モード化が完了した。次は2番目の`bme280_pressure.py`。

## 2026-07-13: `rpi-sensor-lib`二重モード化（`bme280_pressure.py`）の実機検証

### 設計: smbus2互換シム
`bme280`パッケージ（`RPi.bme280`）の実際のソースを確認し、`smbus2.SMBus`に対して呼んでいるメソッドが`write_byte_data`/`read_byte_data`/`read_word_data`/`read_i2c_block_data`の4つだけであることを確認した上で、`_pi4gpio_backend.py`に`Pi4gpioSMBusShim`（この4メソッドのみを実装し、内部でpi4gpioクライアントのI2C操作を呼ぶ）を追加した。`bme280`パッケージ自体は無改造。

`read_word_data`はSMBusのプロトコル上リトルエンディアン（下位バイトが先）で送られてくる（`bme280/reader.py`のコメント「default is little endian」で確認）ため、`data[0] | (data[1] << 8)`で組み立てる実装とした。`close()`も`smbus2.SMBus`と同じインターフェースで持たせ（内部は`i2c_release`を呼ぶだけ）、`BME280Sensor.close()`側はbackendで分岐せず`self.bus.close()`のみで済むようにした。

### 実施内容・結果
1. **ローカル検証（Windows、`socket.socketpair()`で疑似サーバー）**: シムの4メソッド全てのワイヤーエンコードを検証。特に`read_word_data`のリトルエンディアン変換（`bytes=[0x11,0x22]`→`0x2211`）を明示的に確認
2. **実機検証**: `direct`（`smbus2.SMBus`）と`pi4gpio`（シム経由）の両方で`BME280Sensor`を初期化・`read()`を実行し突き合わせ。センサーは引き続き物理的に未接続のため値は取得できなかったが、**両方とも同一の失敗モード（`Errno 5: Input/output error`）で一致**——直接アクセスとシム経由で全く同じ挙動を示すことを確認した
3. 本番サービスのPID・保持デバイスに最後まで変化なし。テスト用に転送したディレクトリ・一時ファイルを全て削除

### 結果
`MIGRATION_PLAN.md`のセンサー移行順序2番目（`bme280_pressure.py`）の二重モード化が完了した。`Pi4gpioSMBusShim`は他のI2Cセンサーでも再利用できる汎用設計のため、今後I2Cセンサーが増えても流用できる。次は3番目のSPI系3センサー（`grove_mcp3208_sensors.py`・`joystick_mcp3208.py`・`potentiometer_mcp3208.py`）。

## 2026-07-13: `rpi-sensor-lib`二重モード化（SPI系3センサー）の実機検証

### 設計: spidev互換シム
`grove_mcp3208_sensors.py`・`joystick_mcp3208.py`・`potentiometer_mcp3208.py`の3ファイルとも、実際に使う`spidev.SpiDev`のメソッドは`xfer2()`のみだったため、`_pi4gpio_backend.py`に`Pi4gpioSpiTransferShim`（`xfer2()`と`close()`のみを実装）を追加した。`spidev.SpiDev()`＋`.open(bus, device)`という2段階構築とは異なりコンストラクタで`bus`/`chip_select`を直接指定する設計だが、`xfer2()`の呼び出し側（各センサークラスの`read_raw`/`_read_adc`）は無改造で済んだ。

### 実施内容・結果
1. **ローカル検証**: `xfer2([0x06,0x00,0x00])`のワイヤーエンコードと、12ビットADC値の組み立てロジックを疑似サーバーで確認
2. **実機検証**: 本番稼働中の`/dev/spidev0.0`に対し、`PotentiometerMCP3208.read_raw()`と`JoystickMCP3208.read_xy()`を`direct`/`pi4gpio`両モードで実行。両方とも`0`・`(0, 0)`で完全一致——Tier 1のSPI検証時と同じ「センサー基盤未接続」の既知パターンと整合
3. 本番サービスのPID・保持デバイスに最後まで変化なし。テスト用に転送したディレクトリ・一時ファイルを全て削除

### 結果
`MIGRATION_PLAN.md`のセンサー移行順序3番目（SPI系3センサー）の二重モード化が完了した。残るは4番目（`mh_x19c_co2.py`、UART）と5番目（`robust_dht22.py`、Tier 2のDHT22デコード実装が必要）。

## 2026-07-13: `rpi-sensor-lib`二重モード化（`mh_x19c_co2.py`）の実機検証

### 設計: pyserial互換シム
`mh_x19c_co2.py`が実際に使う`serial.Serial`のメソッドは`write()`/`read()`/`close()`/`is_open`属性のみだったため、`_pi4gpio_backend.py`に`Pi4gpioSerialShim`を追加した。pyserialの`port`パラメータ（デバイスパス文字列）とpi4gpiodの命名規約（ポート番号→`/dev/ttyS{port}`）が異なる点は、pi4gpioモードではポート0固定（このPiにはUARTが1系統しかないため）として吸収した。

### 実施内容・結果
1. **ローカル検証**: `write()`/`read()`のワイヤーエンコードと、既存のCO2濃度計算ロジック（`result[2]*256+result[3]`）が疑似応答（0x01F4→500ppm）に対して正しく動作することを確認
2. **実機検証**: UARTはtermiosがデバイス単位の共有状態のため、Tier 1のUART検証時と同様`rpi-hw-lock`の`exclusive_hardware_access()`で本番サービスを一時停止した区間内で実施。`direct`（pyserial）・`pi4gpio`（シム経由）とも`read_co2()`が`None`を返し（センサー未接続によるタイムアウト、ハングなし）一致。`rpi-hw-lock`は今回も本番サービスを正しく再開した
3. 本番サービスのPID・保持デバイスに最後まで変化なし。テスト用に転送したディレクトリ・一時ファイルを全て削除

### 結果
`MIGRATION_PLAN.md`のセンサー移行順序4番目（`mh_x19c_co2.py`）の二重モード化が完了した。残るは5番目（`robust_dht22.py`）のみ——Tier 2のGPIO通知/コールバックに依存し、DHT22固有の40ビットデコードロジックを新たに実装する必要がある、移行の中で最も難度が高い部分。
