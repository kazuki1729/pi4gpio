# セッション引き継ぎメモ（pi4gpioプロジェクト）

`pi4gpio`は設計フェーズを終え、`FEATURE_PRIORITY.md`のTier 1（GPIO・I2C・SPI・UART基本読み書き）とTier 2（GPIOエッジ検出/通知）、Pythonクライアントライブラリ（`clients/python`）、`rpi-sensor-lib`側の全7センサークラスの二重モード化（`MIGRATION_PLAN.md`移行順序5/5）まで実装・実機検証済み（`VERIFICATION_LOG.md`参照）。実センサーが物理的に未接続のため実データでの検証は未実施。次は、センサー再接続後の実データ検証と`MIGRATION_PLAN.md` §6の並行稼働・カナリア検証。本ファイルは、このプロジェクトの議論だけを別セッションに引き継ぐための独立メモ（week09全体の引き継ぎとは別スコープ）。作業日: 2026-07-12〜13。

## 1. プロジェクトの位置づけ

- **名称**: `pi4gpio`（決定済み）。pigpio・lgpioの命名系譜に連なる形で、かつ「Raspberry Pi 4専用」であることを名前自体に刻んだ
- **目的**: Raspberry Pi 4向けに、pigpioの機能を引き継ぐ独自のGPIO/SPI/I2C/UART共有アクセス・デーモンを自作する
- **`rpi-hw-lock`との関係**: `rpi-hw-lock`（PyPI公開済み・Trusted Publishing化済み、`C:\Users\Kazuki\rpi-hw-lock\`）は、**`pi4gpio`が完成した時点で退役予定**。それ以外のpigpioの機能は`pi4gpio`が引き継ぐ
- **`rpi-sensor-lib`との関係**: 現在`spidev`/`smbus2`/`RPi.bme280`/`lgpio`を直接叩いている`rpi-sensor-lib`（`C:\Users\Kazuki\github-ripo\`）は、将来`pi4gpio`デーモンに喋りに行くクライアントへ全面書き換えが必要。移行計画は`MIGRATION_PLAN.md`で決定済み、実装（`rpi-sensor-lib`側の二重モード化）は未着手

## 2. なぜpigpioをそのまま使わないか

- このRaspberry Pi（Pi 4、Debian 13 trixie）には`pigpiod`がaptから提供されておらず、upstream (`joan2937/pigpio`) も2021年頃からメンテナンス停止状態。ソースビルド以外の入手手段が無く不採用と判断（`rpi-hw-lock`作成の経緯、詳細は`week09/SESSION_HANDOFF.md` §12.7）
- 追加調査で判明した根本原因・弱点:
  - pigpioはDMAコントローラにGPIOのset/clearレジスタを直接書き込ませ、PWM/PCMペリフェラルの一定レート信号でDMA転送をトリガーするという、BCM SoC固有のレジスタ直叩き方式で精密タイミングを実現している（[DMA-based PWM with PIGPIO](https://www.js4iot.com/2021/06/04/DMA_PWM-PIGPIO.html)）
  - この方式はカーネルの新しいGPIOキャラクタデバイス(`gpiochip`/`libgpiod`)インターフェースへの移行に追従できておらず、**Raspberry Pi 5では動作しない**（RP1チップで完全に別物のレジスタマップになったため）（[pigpio will not run on a Pi 5 #589](https://github.com/joan2937/pigpio/issues/589)）
  - `pigpiod`はデフォルトでTCPポート8888を無認証で開く既知の脆弱性を持つ（root権限のデーモンが認証なしでネットワーク待受）（[pigpio Daemon公式doc](https://abyz.me.uk/rpi/pigpio/pigpiod.html)）

## 3. 想定される課題（設計前に洗い出し済み）

以下は設計段階で洗い出した懸念。実装を経て解決・回避できたものには**解決状況**を追記している（未着手のTier 3以降にはまだ当てはまらない懸念も残る）。

### コア実装
- DMA制御ブロックの連結リストによるGPIOレジスタ直叩きをPi4(BCM2711)向けに自前実装する必要があり、メモリマップドI/Oの微妙なバグ（volatile未指定・メモリバリア漏れ等）はSoC全体のハングに直結し得る
  - **解決状況**: Tier 1/2ではDMAを一切使わずに済んだ。GPIO基本読み書きは`/dev/gpiomem`への単純なレジスタ読み書きのみ、GPIOエッジ検出はカーネルの`gpiochip`割り込み機構（GPIO v2 uAPI）に委ねる設計にしたため、この懸念自体が発生しない。DMA制御ブロックが必要になるのはTier 3（ハードウェアPWM）着手時
- userspaceからの`/dev/mem`直接アクセスと、カーネル自身のpinctrl/gpioサブシステムが同じピンを同時に触る競合の可能性
  - **解決状況**: 実装時に整理済み（`crates/pi4gpio-hw/src/gpio_watch.rs`のモジュールdoc、`VERIFICATION_LOG.md`のTier 2記録）。`/dev/gpiomem`への書き込みはカーネルの`gpiochip`管理からは見えないため、同じピンをTier 1（gpiomem）とTier 2（gpiochip）で使い分けてもカーネル側の衝突検知（EBUSY）は発生しない。ただし裏を返せば「カーネルが守ってくれない」ということでもあり、複数クライアント間の排他は引き続きpi4gpio-daemon自身の`LockTable`に依存する
- DMAチャネルは他のカーネルドライバ（オーディオ、SPI/I2Cのハードウェアドライバ等）とも共有資源であり、衝突回避が必要
  - **解決状況**: 上記と同様、Tier 1/2の範囲ではDMA自体を使わないため未着手。Tier 3着手時に改めて検討が必要

### 機能スコープ
- pigpioの全機能（波形生成、ハードウェアPWM、サーボパルス、汎用クロック出力、GPIO通知/コールバック、I2C・SPIのハードウェア版とビットバンギング版、UART、1-Wire、スクリプトエンジン）を本気で引き継ぐと、pigpio本家に匹敵する開発規模になる。段階的な機能優先順位付けが必須

### 排他制御の再設計（`rpi-hw-lock`退役に伴う最重要ポイント）
`rpi-hw-lock`の「サービス単位の排他（他を止めて自分だけが使う）」から、「複数クライアントが1つのデーモンを時分割共有する」設計に転換するため:
- バス単位・トランザクション単位のロック機構をデーモン内部に実装する必要（I2C/SPIの複数ステップ通信を他クライアントの割り込みから守る）
  - **解決状況**: `LockTable`（`crates/pi4gpio-daemon/src/lock.rs`）として実装済み。バス単位（I2Cは`addr`ではなく`bus`単位）でロックし、実機で競合検知・保持者表示まで確認済み（`VERIFICATION_LOG.md`）
- クライアントがロックを持ったままクラッシュした場合の自動解放（ソケット切断検知→ロック解放）
  - **解決状況**: `socket.rs`の接続ハンドラが保持中の`BusId`集合を追跡し、切断時に全解放する形で実装・実機確認済み
- 複数クライアントが同じバスを取り合った際の優先度付け・タイムアウト・デッドロック回避
  - **未着手**。現状は先着順（`try_acquire`が失敗したら呼び出し側にエラーを返すのみ）で、優先度付けやタイムアウトによる強制解放は無い
- `rpi-hw-lock`が担保していた「誤って止めていたサービスを再起動しない」等の安全策も作り直しが必要
  - この論点自体は`rpi-hw-lock`のstop/restart方式に固有のもので、pi4gpioの常駐デーモン方式では発生しない（`MIGRATION_PLAN.md` §2参照）

### 信頼性・単一障害点
- 完成後は**このPi上の全GPIO/SPI/I2C/UARTアクセスが1つのデーモンを経由**することになり、バグの影響範囲が現状（新規スクリプト＋止めた1サービスのみ）より桁違いに大きくなる
- デーモンクラッシュ時に各ピン/ペリフェラルがどの状態で固まるか（リレーON/OFF、サーボ角度保持等）のfail-safe設計が必要

### セキュリティ
- ネットワーク越しの制御を許すかどうかを最初に決定。許すなら認証を最初から組み込む（week09で実績のあるAPI_KEYS方式やTailscale限定バインドが応用できる）
- ローカルソケット経由のクライアントについても、クライアント(UID)ごとのピン/バスアクセス権限分離が必要（`rpi-hw-lock`のsudoers限定方式よりずっと細かい設計が要る）

### 移行・テスト・保守
- `rpi-sensor-lib`の全面書き換え（新デーモンに喋りに行くクライアントへ）は別プロジェクト規模。本番稼働中の`sensor-tiered-client.service`を止めずに移行する計画（並行稼働・切り戻し手順）が必要
- DMA/PWMタイミングの正しさは実機・ロジックアナライザでしか検証できず、GitHub Actions CI（実機無し）の恩恵を受けにくい。バグの影響範囲が全システムに及ぶため、段階的ロールアウト（並行稼働・カナリア運用）が前提になる
- `rpi-sensor-lib`・`rpi-hw-lock`同様、実質的に一人（ユーザー）で保守する前提のため、長期コミットメントとして規模が現実的か要検討

### 言語選択（→ 決定済み、§4-2参照。検討時の記録として残す）
- **C**（pigpio本家と同じ）: 参考実装を踏襲しやすいが、素のポインタ操作によるメモリ安全性バグのリスクを引き継ぐ
- **Rust**: 周辺部分（ソケットサーバ・ロック機構・クライアント管理）は安全に書けるが、DMA制御ブロックへの生ポインタ書き込み自体は`unsafe`が必要で、ハードウェアレジスタ操作の安全性は保証されない。参考実装もCほど豊富ではない
- **Go**: GCポーズが、DMA制御ブロックのタイミング調整部分（µs単位の精度が要求される）と相性が悪い
- 現実的な落とし所として、**タイミングクリティカルな部分だけ小さいCコアで書き、それ以外を安全な言語で書くハイブリッド構成**も検討の余地あり（ただしFFI・ビルド構成の複雑さが増す）
- どの言語でも、実際に使うクライアント言語（少なくともPython）分のクライアントライブラリは自前で維持し続ける必要がある

## 4. 未決定事項・次に詰めるべきこと

1. ~~機能の優先順位付け~~ → 決定。`FEATURE_PRIORITY.md`参照（`rpi-sensor-lib`の実依存に基づきTier分け）
2. ~~実装言語~~ → **Rust**に決定。層B（ソケットサーバ・ロック機構等）の並行処理バグをコンパイル時に防げる点、層A（DMAレジスタ直叩き）を`unsafe`ブロックとして局所化できる点を重視
3. ~~ネットワーク越し制御の要否と、要る場合の認証方式~~ → 決定。`NETWORK_POLICY.md`参照（リモート制御を許可、Tailscale限定bind＋APIキー、mTLSは見送り、配布は「自分専用ソフトとして」各利用者が自己完結）
4. ~~`rpi-sensor-lib`移行の具体的な段取り~~ → 決定。`MIGRATION_PLAN.md`参照（`rpi-hw-lock`のstop/restart方式との関係整理、二重モード化、センサー単位の段階的移行順序、`rpi-hw-lock`退役条件）
5. リポジトリの新規作成 → 完了。`https://github.com/kazuki1729/pi4gpio`（`main`ブランチ）。設計ドキュメント（`SESSION_HANDOFF.md`・`FEATURE_PRIORITY.md`・`NETWORK_POLICY.md`・`MIGRATION_PLAN.md`）とRustワークスペース雛形（`crates/pi4gpio-daemon`・`crates/pi4gpio-hw`、GitHub Actions CI、systemdユニット）をpush済み。Trusted Publishingは対象がクライアントライブラリ側（別リポジトリ）になるため、pi4gpio本体では引き続き未着手

## 5. 関連ファイル・参照

- `rpi-hw-lock`の詳細な経緯（pigpio不採用の判断含む）: `C:\Users\Kazuki\ClaudeWorks\week09\SESSION_HANDOFF.md` §12.7・§13
- `rpi-sensor-lib`のTrusted Publishing化の経緯: 同上 §14
- `rpi-hw-lock`ローカルパス: `C:\Users\Kazuki\rpi-hw-lock\`
- `rpi-sensor-lib`ローカルパス: `C:\Users\Kazuki\github-ripo\`
