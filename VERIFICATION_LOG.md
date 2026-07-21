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

## 2026-07-13: 6時のシャットダウン実行、WatchEdgesへのプルモード追加、ロック解放バグの発見・修正

### 6時シャットダウンの実行確認
前回セッションで予約したスケジュールタスク（`pi4gpio-safe-shutdown`、2026-07-13 06:00 JST）が実行済みだった（`lastRunAt`と一致、`enabled: false`）。Pi再起動直後の`uptime -s`が`2026-07-13 09:20:58`（起動から3分）だったことから、Piが約3時間15分電源オフだったことを確認した。本番サービスは再起動時に自動起動し、`ActiveState=active`で健全に復帰していた。

### WatchEdgesへのプルモード追加
`robust_dht22.py`の実装に向け、DHT22モジュールに外部プルアップが無い場合に備え、Tier 1のReadと同様`WatchEdges`にも`pull`フィールド（`PullMode`）を追加した。`gpioinfo`で`WatchEdges`実行中のGPIO17を確認したところ`bias=pull-up edges=both consumer="pi4gpio"`と表示され、カーネルレベルで正しく設定されていることを確認した。

### 実機での発見: クライアント通信エラー時のロック解放漏れ（重大バグ）
検証中、`timeout`コマンドでクライアントを強制終了させた後、そのクライアントが保持していたGPIO17のロックが解放されないまま残る現象が発生した（`locked_by`エラーが以降ずっと返り続ける）。

原因調査の結果、`socket.rs`の`handle_client`内で`writer.write_all(&payload).await?`が`?`演算子でエラーを即座に呼び出し元へ伝播させており、後片付け（`held_buses`の解放ループ）がスキップされることが判明した。クリーンな切断（`next_line()`が`None`を返すEOF）では問題無く後片付けが実行されていたため、これまでのTier 1/2検証では気づかれていなかった——通信エラー（Broken pipe等）による切断という、まさに`SESSION_HANDOFF.md` §3が要求していた「クライアントがロックを持ったままクラッシュした場合の自動解放」の対象そのものが、実は機能していなかった。

修正として、リクエスト処理ループを`process_requests`という別関数に切り出し、その戻り値（`Ok`/`Err`どちらでも）を`handle_client`側で受け取ってから、後片付けループを**必ず**実行するよう変更した。

### 実施内容・結果
1. 意図的にクライアントを強制切断する再現テストを実施（`timeout_ms=2000`のリクエストを送信後、0.5秒でクライアント側を強制終了）
2. デーモンログで「Broken pipe」エラーと同時に「client disconnected」（後片付け完了のログ）が出力されることを確認
3. 別接続からGPIO17に即座にアクセスし、`locked_by`エラーにならず正常に読み取れることを確認（ロックが正しく解放されている証拠）
4. 本番サービスのPID・保持デバイスに最後まで変化なし

### 結果
**このバグは実機での意図的な障害注入テスト（クライアント強制切断）を行わなければ見つからなかった**——GPIOのPullMode符号バグ・UARTの無期限ブロックバグ・PythonクライアントのOperationタグ付け誤りに続き、実機検証で発見した4件目の実運用上の欠陥。かつ今回は「クリーンな切断は動くが異常切断では動かない」という、通常の使用パターンでは気づきにくい種類のバグだった。`robust_dht22.py`の実装に進む。

## 2026-07-13: `rpi-sensor-lib`二重モード化（`robust_dht22.py`）の実機検証

### 設計: DHT22の40ビットデコードロジックの移植
`gpio_watch_edges()`が返すタイムスタンプ付きエッジ列から温湿度をデコードする`_decode_dht22_edges()`を新規実装した。既存の`_read_raw_direct`（lgpioベース、生サンプル列に対する5ステートマシン）をそのまま流用せず、**同じ状態遷移をエッジ列に対して適用する形に移植**した——サンプル列とエッジ列ではデータの粒度が異なるため、ロジックの忠実な移植が必要だった。

### 実機での発見: HIGH区間長の測り方を取り違えていたバグ
実装直後、既知の温湿度値（23.4℃・65.5%）を表す合成エッジ列でローカル検証したところ、デコード結果が`0.0, 0.0`になった。原因を調査した結果、DHT22の各ビットのHIGH区間長は「そのビット自身のfalling→rising」ではなく「rising→**次のビットの**falling」で決まる、というプロトコルの仕様を取り違えていたことが判明した。原典の`_read_raw_direct`の5ステートマシンを改めて詳細にトレースし、正しい状態遷移（ACKをstate 1〜3で読み飛ばし、state 4→5→4のサイクルでHIGH区間長を測る）を確認した上で実装し直した。

これは実機テストではなく**合成データによるローカル検証**で発見できたバグだった——実センサーが無い状況でも、既知の入力に対する既知の出力を検証するテストの価値を裏付ける結果。

### 実施内容・結果
1. **合成データ検証（ローカルpre-flight相当、実機のPython環境で実施）**: 既知の温湿度値を表すビット列からエッジ列を合成し、`_decode_dht22_edges()`が正しく23.4℃・65.5%を復元することを確認。スタート信号解放時のrisingエッジが「検出される場合」「されない場合」の両方で正しくデコードできることも確認（実機でこの検出有無に揺らぎがあることは前回のWatchEdges検証で判明済み）。チェックサム不一致時に正しく`ValueError`を送出することも確認
2. **`RobustDHT22`クラス全体のdirect/pi4gpio両モード実機テスト**: GPIO17（本番が使うピン26とは別の未使用ピン）に対し、`max_retries=2`で両モードとも`DHT22ReadError`が正しく送出され、ハングやクラッシュが無いことを確認。pi4gpioモードは`timeout_ms=1000`の安全マージンにより直接モードよりリトライに時間がかかる（想定通りの設計上の違い）
3. 本番サービスのPID・保持デバイス・GPIO26の状態（`bias=pull-up consumer="lg"`）に最後まで変化なし。テスト用に転送したディレクトリ・一時ファイルを全て削除

### 結果
`MIGRATION_PLAN.md`のセンサー移行順序5番目（最終、`robust_dht22.py`）の二重モード化が完了した。これで全7センサークラスの二重モード化が完了——`rpi-sensor-lib`の全センサーがpi4gpio経由でも利用可能になった。実センサーが物理的に接続されていないため実際の温湿度値での検証はできていないが、既知の合成データによるロジック検証と、実機での通信経路・エラーハンドリングの検証は完了している。

## 2026-07-13: pi4gpiodのsystemdサービス化

### 事前確認（作業前）
- `pi4gpiod`はそれまで毎回手動起動（フォアグラウンド or `nohup`）で検証していたため、セッション終了・Pi再起動のたびに消えていた。`sensor-tiered-client.service`は稼働中であることを確認
- `systemd/pi4gpio.service`ユニットファイル自体はリポジトリに存在していたが、実機への配置・`systemctl enable`は未実施だった（`MIGRATION_PLAN.md` §3 Phase 0の残項目）

### 実施内容
1. `User=pi`（`pi`ユーザーは`gpio`/`i2c`/`spi`/`dialout`全グループ所属済みのため、root権限は不要——最小権限の原則）、`RuntimeDirectory=pi4gpio`（`/run/pi4gpio`をsystemdが自動作成・所有権設定、これまでの`sudo mkdir`手動回避が不要に）、`Restart=on-failure`で構成したユニットファイルを`/etc/systemd/system/pi4gpio.service`に配置
2. `sudo systemctl daemon-reload && sudo systemctl enable --now pi4gpio.service`
3. `ps -up <PID>`で実際に`pi`ユーザー権限で起動していることを確認（root起動になっていないか要確認だったため）
4. `Restart=on-failure`の動作確認: 意図的に`kill -9`でプロセスを強制終了させ、systemdが自動的に再起動することを確認（PID 4396→4440へ変化、`RestartSec=2`の通り約2秒後に復帰）
5. 本番サービスのPID・保持デバイスに最後まで変化なし

### 結果
`pi4gpiod`がPi再起動時にも自動起動する永続サービスになった。`MIGRATION_PLAN.md` §3 Phase 0の残項目（「pi4gpioがsystemdサービスとして安定起動・自動再起動する状態」）を満たした。これにより、以降のカナリア検証・アドホックスクリプトはPi再起動を挟んでも都度手動起動し直す必要がなくなる。

## 2026-07-13: カナリア比較スクリプト（`scripts/canary_compare.py`）の準備・構造テスト

### 背景
`MIGRATION_PLAN.md` §6の並行稼働・カナリア検証の準備として、`direct`/`pi4gpio`両モードの読み取り結果（値・レイテンシ・成功率）をCSVへ記録するスクリプトを新規作成した。実センサーは依然として物理的に未接続のため、実データでの比較検証はまだ行えない——今回の作業は「センサー再接続後すぐ本格稼働できる状態にしておく」準備・構造検証に限定される。

### 設計上の制約整理
- I2C（BME280）・SPI（MCP3208系4種）はカーネルがバス単位でトランザクションをシリアライズするため、`direct`/`pi4gpio`両方を本番プロセスと並行して独立に読んでも安全（既存のTier 1実機検証で確認済みの前提）
- GPIO（`tactile_button`、本番ピン6）・UART（`mh_z19c_co2`）・DHT22（`robust_dht22`、本番ピン26）は、本番プロセスが同じピン/デバイスを`lgpio`/`pyserial`経由で既に掴んでいるため、本スクリプトが`direct`モードで独自に読むと競合しうる。これらは`pi4gpio`モードのみ読み、`direct`側の参考値は本番プロセスが`journalctl`に残す「送信準備: {json}」ログから抽出して緩やかに突き合わせる方式にした（厳密な同時比較ではない）

### 実機での構造テスト
1. 本番venv（`/home/pi/sensor-tiered-store/.venv`、`lgpio`/`spidev`/`smbus2`/`pyserial`/`RPi.bme280`インストール済み）に`rpi-sensor-lib`0.1.0（二重モード化前）が入っており、本番のsite-packagesは使えないことを確認。二重モード対応版の`rpi_sensors`パッケージを`~/rpi-sensor-lib-canary`へ別途配置し、本番環境に一切触れず検証できるようにした
2. `~/pi4gpio/scripts/canary_compare.py`を転送し、本番venvのPythonインタプリタで`--interval 5 --duration 12`の短時間構造テストを実行
3. センサー未接続のため全項目が floor値（0や`-1.0`等）・`I/O error`・`DHT22ReadError`のいずれかを返したが、**クラッシュせず**全13項目×2回分がCSVへ正しい形式で記録されることを確認
4. `bme280_pressure.py`の`read()`は初期化失敗時も例外を送出せず`(None, None, None)`を返す（内部でエラーを握りつぶして`ok=True`のまま返す既存の設計）ことが判明。カナリアCSVの`ok`列だけでは判定不十分で、値が`None`かどうかも見る必要があると分かった——今後の実データ分析時の注意点として記録
5. 本番サービス（`NRestarts=0`、`ActiveState=active`のまま）・`pi4gpiod`（新規クライアント接続・切断のログのみ、ロックエラー無し）ともに影響が無いことを確認。テスト用一時ファイル・`__pycache__`を削除

### 結果
`canary_compare.py`は実センサー無しでもクラッシュせず動作し、CSV出力形式・本番無影響を確認できた。実データでの並行稼働検証は、ユーザーによるセンサーの物理的な再接続後に着手する（`MIGRATION_PLAN.md` §5の注記の通り、依然として未達成）。

## 2026-07-13: センサー再接続後の実データ検証、DHT22 pi4gpioモードの重大バグ発見・修正

### 事前確認（作業前）
- ユーザーがセンサー基盤を物理的に再接続。検証のため`sensor-tiered-client.service`を一時停止（`sudo systemctl stop`、事前に`ActiveState=active`・`MainPID`を確認）
- I2Cバススキャン（`i2cdetect -y 1`）でBME280（0x76）を検出、UART（`/dev/serial0`→`ttyS0`）の存在、GPIO6・GPIO26が他プロセスに未保持であることを確認

### 実データ検証（1回目）: 6/7センサーで成功、DHT22のpi4gpioモードのみ失敗
`direct`/`pi4gpio`両モードで全7センサークラスを実際に読み取り比較した（本番停止中のみ可能な検証——通常はGPIO/UART/DHT22をdirectモードで独自に読むと本番と競合するため避けている）。

- BME280・照度・音量・ジョイスティック・ポテンショメータ・MHZ19C CO2・ボタンの6種は、両モードでほぼ一致する実データが取得できた（例: BME280 23.84℃/68.5%/990.0hPa vs 23.83℃/68.6%/989.9hPa、CO2 693ppm vs 693ppm）
- **`robust_dht22`のpi4gpioモードのみ、3回とも失敗**（directモードは成功）

### 原因調査: カーネルGPIO v2エッジ割り込みの取りこぼし
`gpio_watch_edges()`の生エッジ列を直接取得して調査した結果:
- DHT22プロトコルは理論上「ACK(2エッジ)+40ビット×2エッジ(80エッジ)+送信終了パルス(1エッジ)」＝83エッジが必要（`robust_dht22.py`の設計コメントが「ACK(2)+40ビット(80)=82」と、送信終了パルス分を数え忘れていたことが判明）
- 実機では82エッジで打ち切られ、40ビット目のHIGH区間を確定できず`ValueError("データ欠損")`
- `timeout_ms`を1000ms→5000msに延ばしても追加のエッジは来ず、最初は「カーネル割り込みが取りこぼしている」と推定した

### 対策1（不採用）: Tier 1生レベル読み取りによるフォールバックポーリング
タイムアウト直前にTier 1（`/dev/gpiomem`）で現在のピンレベルを直接読み、割り込みが取りこぼした遷移を検出する仕組みを`pi4gpio-daemon`に実装した。しかし実機テストの結果、**Tier 1の生レジスタ読み取りでも実際にピンがHIGHのまま**であることが判明——カーネルの取りこぼしではなく、本当に電気的な遷移が来ていなかった。この対策は効果が無いため実装を差し戻した。

### 対策2（部分的に有効）: チェックサムからの逆算
`rpi_sensors/robust_dht22.py`の`_decode_dht22_edges()`に、40ビット目（チェックサムバイトの最下位ビット）が欠落している場合、他の39ビットから計算した期待チェックサムとの整合性から算術的に逆算する処理を追加した。合成データ6ケース（通常ケース・負の温度・欠落あり×bit0/bit1期待・チェックサム破損検知・複数欠落時のエラー継続）で検証しロジックは正しいことを確認したが、実機で本番相当のリトライ設定（`max_retries=5`×5回）を試したところ**0/5成功**——エッジ数を複数回サンプリングした結果82・81・82と欠落数が一定しないことが判明し、「40ビット目だけが欠ける」は失敗パターンの一部に過ぎず、実際にはより広範なビット化け・複数エッジ欠落が起きていた（`データ欠損`・`CRCエラー`の両方を実際に確認）。

### 根本原因: この配線環境における信号品質とTier 2のノイズ耐性の低さ
directモード（lgpioの高速busy-loopポーリング）も完璧ではない（実測約5〜7割成功）が、pi4gpioのカーネル割り込み方式より明確に安定していた。ポーリング方式は電圧遷移が多少鈍くても「その時点のレベル」を捉えられればよく、割り込みの発火に依存しないため、ノイズへの耐性が高いと考えられる。

### 対策3（採用・成功）: Tier 1相当の高速ポーリングをdaemon側に新規実装
`WatchEdges`（Tier 2、カーネル割り込み）とは別に、`WatchEdgesPolled`という新しいOperationを追加した。`/dev/gpiomem`の生レベルをdaemon側で高速busy-loopポーリングし、レベル変化をタイムスタンプ付きエッジとして記録する（`_read_raw_direct`のRust版に相当）。打ち切り条件は「前回の遷移から300us（daemon内部定数、DHT22の最大ビット間隔に十分な余裕）変化が無い」または「合計ポーリング時間が`budget_ms`到達」のいずれか早い方。戻り値の形式は`WatchEdges`と完全に同一にしたため、`_decode_dht22_edges`等のデコードロジックは変更不要だった。

`robust_dht22.py`の`_read_raw_pi4gpio()`を`gpio_watch_edges_polled()`（新規追加したPythonクライアントメソッド）経由に切り替えた結果:
- 単発（`max_retries=1`）で**5/5成功**、値も安定（25.4〜25.5℃・64.0〜64.2%）
- 本番相当のリトライ設定（`max_retries=5`）でも**5/5成功**
- directモードの結果（25.4℃/64.2%）ともほぼ完全に一致

### 実施内容・結果
1. 対策1（Tier 1フォールバックポーリング）を実装→実機検証で無効と判明→`git checkout`で差し戻し
2. 対策2（チェックサム逆算）を実装、合成データ6ケースで検証→実機では改善効果が限定的と判明（採用は継続、無害かつ一部ケースで有効なため）
3. 対策3（`WatchEdgesPolled`新規実装）を実装、実機で10/10成功を確認
4. 全7センサークラスをdirect/pi4gpio両モードで再度実データ比較し、DHT22含め全て安定した値の一致を確認
5. 本番サービスは検証中ずっと停止したまま（意図的、ユーザー許可済み）。検証完了後にユーザーへ再起動を明示的に案内する運用とした

### 結果
DHT22の実機データ検証で、`MIGRATION_PLAN.md`が「最重要かつ最高難度」と位置付けていた通りの重大な実運用バグ（pi4gpioモードでのDHT22読み取りが実機では常に失敗する）を発見・修正した。カーネル割り込み（Tier 2）に対する過信が原因——理論上はカーネル空間でのタイムスタンプ打刻によりOSスケジューリングジッタに強いはずだったが、実機では逆に、電圧遷移が緩やかな信号に対するTier 1ポーリング方式の耐性の高さの方が勝った。これで`MIGRATION_PLAN.md`のセンサー移行順序5項目全てが実データで検証済みとなった。実センサー未接続の期間が長く続いたこのプロジェクトで、初めて全センサーの実際の温湿度・照度・音量・CO2濃度等の値が確認できた節目でもある。

## 2026-07-13: カナリア比較のsystemd常駐化・本格運用開始

### 事前確認（作業前）
- `sensor-tiered-client.service`・`pi4gpio.service`ともに`ActiveState=active`であることを確認
- `~/pi4gpio/scripts/canary_compare.py`・`~/rpi-sensor-lib-canary/rpi_sensors/`が既に配置済みであることを確認

### 実施内容
1. `systemd/canary-compare.service`を新規作成（`User=pi`、`Restart=on-failure`、`WorkingDirectory=~/pi4gpio/scripts`、本番venvのPythonで`canary_compare.py --interval 30`を実行）。永続的な自動起動設定に当たるため、実行前にユーザーへ機能説明と承認を求めた
2. `/etc/systemd/system/canary-compare.service`に配置し`systemctl enable --now`
3. 起動後35秒で記録開始を確認。CSV初回分の全13項目（I2C/SPI系はdirect/pi4gpio両方、GPIO/UART/DHT22系はpi4gpioのみ+本番journalctl参考値）が正しい形式で出力されていることを確認
4. DHT22（pi4gpio 25.3℃/64.4% vs 本番参考値25.2℃/63.8%）・MHZ19C CO2（pi4gpio 610ppm vs 本番参考値607ppm）とも、非同時比較ながら妥当な近似値であることを確認
5. `sensor-tiered-client.service`・`pi4gpio.service`とも`NRestarts=0`・`ActiveState=active`のままで、カナリア起動による影響が無いことを確認

### 結果
`canary-compare.service`が2026-07-13 12:34 JST頃から常駐稼働を開始した。Pi再起動を挟んでも自動再開するため、`MIGRATION_PLAN.md` §6が求める1〜2週間規模の並行稼働データ収集が実際に始まった。次のステップ（本番の段階的cutover判断）は、この期間のCSVが蓄積されてから行う。

## 2026-07-13: テストスイート整備、Pythonクライアントのソケットタイムアウト競合バグの修正

カナリア本格運用の待ち時間（1〜2週間）を待つ間に、実機検証・調査の過程で見つけていた積み残しに着手した。

### テストスイートの新規追加
- `rpi-sensor-lib`に`tests/test_robust_dht22.py`（`unittest`）を追加。DHT22の40ビットデコードロジックを、既知の温湿度値から合成したエッジ列で検証する6ケース（通常ケース・負の温度・終端エッジ欠落時のビット0/1逆算・チェックサム破損検知・複数エッジ欠落時のエラー）。実センサー不要、Windows開発機でも動作確認済み。CI（`.github/workflows/ci.yml`）にも実行ステップを追加
- `pi4gpio`の`clients/python`に`tests/test_client_timeout.py`を追加。`socket.socketpair()`でdaemon側を模擬し、後述のタイムアウト競合バグの回帰テストを含む3ケースを検証。`AF_UNIX`が無いWindowsでは実行できないため、実機（Pi）で検証（`cargo check --target aarch64-unknown-linux-gnu`と同じ「Windows非対応部分は実機/Linux環境で検証する」方針）

### 発見・修正: クライアントのソケットタイムアウト競合バグ
`Pi4gpioClient`のソケットタイムアウト（デフォルト5秒）が、呼び出し側が`timeout_ms`（`gpio_watch_edges`）や`budget_ms`（`gpio_watch_edges_polled`）でそれ以上の待ち時間を指定した場合、daemonが応答するより先にクライアント側がタイムアウトしてしまう問題（DHT22調査中の`timeout_ms=5000`診断スクリプトで実際に踏んだバグ、当時はクライアント初期化時の`timeout=15.0`で個別に回避していた）。

`_request()`に`min_response_timeout`引数を追加し、リクエスト送受信の間だけソケットタイムアウトを`timeout_ms`/`budget_ms`+2秒マージンまで一時的に引き上げ、応答後は元の値に戻す方式で修正。実機でのテスト（`test_long_watch_edges_survives_short_base_timeout`）で、base_timeout=1秒・応答遅延2秒の状況で修正前は`TimeoutError`、修正後は正常応答を確認。

### 実施内容・結果
1. Windows開発機でDHT22デコードの合成データテスト6件を実行、全件成功
2. クライアントのタイムアウト修正を実装、実機でテスト3件を実行——修正前のコードのまま転送し忘れた1回目は意図せず「修正が効いていない状態」を再現してテストが正しく落ちることも確認でき、テスト自体の有効性も裏付けられた。修正版を再配置後、3件とも成功
3. `canary-compare.service`を再起動して修正版クライアントに切り替え、記録継続（DHT22 24.5℃/68.1% vs 本番参考値24.5℃/67.9%）と本番・pi4gpiodへの無影響（`NRestarts=0`）を確認

いずれも実機無しで検証できる範囲（合成データ・ソケットモック）を最大限使い、実機は最終確認のみに留める形で進めた。

## 2026-07-21: UART二重読み取り除去・クライアント復旧・障害注入自動化

### 事前確認

- `canary_compare.py`が本番directアクセスと別に`MHZ19C.read_co2()`を実行し、同じUART応答列を消費していたことをソースで再確認
- PythonクライアントはEOF／ソケット例外後に壊れた接続を保持し、次周期の操作でも復旧できないことを確認
- daemon側は接続終了時に保持バスを解放し、systemdは`Restart=on-failure`で再起動する設計を再確認
- `sensor-tiered-client.service`は共有クライアントを使うため、同じクライアントオブジェクトが再接続できればセンサークラスの再生成なしで次周期から復旧できることを確認
- 実機`kazuki1729.local`は名前解決できなかったが、過去記録のTailscale直通経路で到達。week09稼働系への影響を最小化するため、activeサービスの停止・再起動・SIGKILLは行わないと判断

### 実施内容

1. カナリアから`MHZ19C`のimport、インスタンス生成、pi4gpio UART読み取りを除去。既存CSV列は維持し、UART行を`production_log`由来として転記するだけに変更
2. `Pi4gpioClient`に有界指数バックオフ再接続と`Pi4gpioConnectionError.reconnected`を追加。処理中要求は自動再送せず、次の通常要求から新接続を使う設計にした
3. 模擬デーモンを要求受信後・応答前に切断する自動障害注入テストを追加。非再送、遅延復帰、試行上限、次要求成功を検査
4. 実機用`scripts/fault_injection_systemd.py`を追加。既定dry-run、week09／カナリアactive時の実行拒否、ハードウェア非アクセス、PID・NRestarts・ソケット・プロトコル応答の復旧確認を実装
5. PythonテストをGitHub ActionsのPython 3.9／3.12へ追加し、daemonロック表の所有権・切断解放テストも追加
6. Pi上の旧3ファイルを`/home/pi/pi4gpio/backups/20260721_uart_recovery`へ保存し、停止中カナリア、開発用Pythonクライアント、安全弁付き障害注入ツールだけを`/home/pi/pi4gpio`へ配備

### ローカル検証結果

- Pythonクライアントテスト: 6件成功
- カナリア／systemd安全弁テスト: 6件成功
- Python `compileall`: 成功
- `cargo fmt --all -- --check`: 成功
- `cargo check --workspace --all-targets --target aarch64-unknown-linux-gnu`: 成功
- `git diff --check`: 成功
- 配備4ファイルのローカル／Pi SHA-256: 全件一致
- 配備前後の実機状態: `sensor-tiered-client.service=active`、`pi4gpio.service=active`、`canary-compare.service=inactive`、pi4gpiod `MainPID=967`、`NRestarts=0`で不変
- 実機dry-run: activeなweek09本番を検出し`would_execute=false`、`hardware_operations=0`。SIGKILL・サービス操作なし
- Pi上のハードウェア非依存テスト: クライアント6件、カナリア／安全弁6件の全12件成功
- 最終影響確認: week09は`active/running`、`NRestarts=0`、`ExecMainStatus=0`で10秒周期の送信成功を継続。`RPI_SENSOR_BACKEND`は未指定（既定direct）。カナリアは`inactive/disabled`、pi4gpiodは`MainPID=967`・`NRestarts=0`のまま

### 未実施・次の受入条件

修正版カナリアはPiへ配備済みだがサービスはinactiveのままで、実データ記録は未実施。systemd SIGKILL障害注入も未実施。保守時間帯にweek09とカナリアを安全に停止できる場合のみ、`FAULT_INJECTION.md`に従って実行する。修正前カナリアCSVのUART値は競合の影響を受け得るため、移行判定には使用しない。

## 2026-07-21: カナリアの完全受動化とdirectアクセスのOSレベル制限

### 判断

UARTだけでなく、GPIOボタンとDHT22も本番directとpi4gpioカナリアが同じ物理資源へ触れる構成だった。I2C/SPIを含め、pi4gpiodのLockTableではdaemonを経由しないプロセスを禁止できないため、本番がdirectである間のハードウェア並行比較そのものを停止する方針へ変更した。

### 実施内容

1. `canary_compare.py`から`rpi_sensors`および全センサーインスタンスを除去
2. 全センサー値を本番journalの「送信準備」payloadから転記する受動監視へ変更
3. pi4gpiodは空JSONに対するエラー応答だけで監視し、ハードウェア操作を送らない
4. カナリアunitへ`PrivateDevices=true`・`DevicePolicy=closed`・カーネル保護・AF_UNIX限定を追加
5. 起動時にGPIO/I2C/SPI/UARTデバイスが見えないことを検証するフラグを追加
6. 本番pi4gpio移行後に適用する`DevicePolicy=closed`付きsystemd drop-inを追加
7. 共有Pi全体のudev権限は変えず、week09サービスだけを制限する設計を`EXCLUSIVE_ACCESS.md`へ記録

### ローカル検証

- 受動カナリア／systemd安全性／障害注入テスト11件: 成功
- Pythonクライアントテスト6件: 成功
- Python構文検査: 成功
- `cargo fmt --all -- --check`: 成功
- `cargo check --target aarch64-unknown-linux-gnu --all-targets`: 成功
- `cargo clippy --target aarch64-unknown-linux-gnu --all-targets -- -D warnings`: 成功
- `git diff --check`: 成功
- カナリアモジュールに`rpi_sensors`・`lgpio`・`spidev`・`smbus2`・`serial`のimportがないことをASTで検査
- unitとdrop-inにデバイス・カーネル保護設定が存在することを自動検査

### 実機状態

直接のTailscale接続とmDNSは利用できなかったが、`buffalo-srv-cf`経由のSSH jumpでPiへ到達し、既存ファイルを`/home/pi/pi4gpio/backups/20260721_passive_canary`へ保存して配備した。

- `canary-compare.service`だけを再起動し、week09本番とpi4gpiodは停止・再起動していない
- カナリアPID 39139、week09 PID 1253、pi4gpiod PID 967で複数周期を確認。全サービス`active/running`、`NRestarts=0`
- カナリアのprivate `/dev`にGPIO/I2C/SPI/UARTデバイスが存在せず、それらを指すFDも0件
- 本番サービス状態、journalの全8センサー値、pi4gpiodソケット応答が連続して正常
- week09のjournald出力が約260秒間隔でまとめて反映されることを実測。サービス状態は30秒ごとに即時確認し、payload鮮度は360秒で停止判定するよう調整。259秒まで正常を維持し、次のflush後に59秒へ戻ることを確認
- ログローテーション設定を配置し、構文・対象・権限を`logrotate --debug`で確認
- `systemd-analyze verify`成功後、カナリアを`enabled/active`へ移行。自動起動登録時にも3サービスのPIDは不変
- 本番pi4gpio移行用drop-inはリポジトリへ配置しただけで、direct稼働中の`/etc`には適用していない

これにより、現行direct本番と併存するカナリアからのDHT22／ボタン／UARTを含む二重アクセスは、コードとOSデバイス制限の二重で解消した。Pi4gpio経由クライアントと任意のdirectプロセスをdaemon単体で排他することはできないため、将来のweek09切替時は専用drop-inでバックエンドをpi4gpioへ固定し、同じサービスからのdirectデバイスアクセスを禁止する。

## 2026-07-21: 常駐カナリアの撤去と5秒取得の評価

完全受動版カナリアは安全に動作したが、センサーハードウェアを検証せず、week09本番の状態とjournalを再確認するだけである。既存の`sensor-monitor.service`がDBを5秒周期で監視し、30秒無通信と値域異常を検出していること、direct本番はpi4gpiodを利用していないことから、常駐させる運用上の利益は小さいと判断した。

- `canary-compare.service`をdisable/stopし、実機のunitと自動起動リンクを削除
- 専用logrotate設定と旧版を含むカナリアCSV 4ファイル（約2.9MB）を削除
- systemdで`LoadState=not-found`、`ActiveState=inactive`を確認
- week09はPID 1253、pi4gpiodはPID 967のままactive/running、両方`NRestarts=0`
- スクリプトと安全なunitテンプレートは将来の保守時間帯・移行診断用としてリポジトリに保持

5秒取得については、DHT22の最小読み取り間隔2秒を満たし、MH-Z19CのUART処理も通常約0.1秒なので仕様上は可能。ただしDHT22は失敗時に2秒間隔で最大5回リトライし、最悪時は5秒周期を超える。全センサーを毎回読み直す単純変更より、送信周期を環境変数化して5秒試験を行い、DHT22/CO2は必要に応じてキャッシュまたは低頻度化する方式を推奨する。

## 2026-07-22: Pi4gpio試験事前準備の再開確認

中断チェックポイントから再開し、停止直前に追加したjournal失敗集計を含む運用スクリプトを再検証した。

- 運用スクリプトテスト18件、Pythonクライアントテスト6件、week09周期設定テスト4件: 成功
- Python構文、Rust format、aarch64向けcheck/clippy、`git diff --check`: 成功
- Windowsホスト向けRust clippyはLinux専用APIのため不適用。CIと同じLinuxターゲット向けcheck/clippyは成功
- journal解析が指定時間に1時間加算して失敗行を数えていた不具合を発見。指定時間を正確に使うよう修正し、回帰テストを追加
- 修正版を`/home/pi/pi4gpio/scripts/analyze_direct_journal.py`へ配置。ローカル／Pi SHA-256一致を確認
- 配置前後ともweek09 PID 1253、pi4gpiod PID 967、両方`active/running`・`NRestarts=0`
- 本番サービスの停止・再起動、周期変更、センサーハードウェア試験は行っていない

直近24時間を指定したjournal集計では、連続送信payloadの保持範囲がまだ19.308時間だったため正式24時間基準には未到達。部分基準はtimer 6,948件、平均周期10.005秒、DHT22成功率99.122%、BME280 99.928%、MH-Z19C 98.647%、ネットワーク通信エラー0件。センサー失敗を含む周期は1,831件で、複数センサー失敗により送信自体を抑止した周期も含むため、送信済みpayload内の欠損数とは直接一致しない。2026-07-22 05:39 JST以降に再集計する。
