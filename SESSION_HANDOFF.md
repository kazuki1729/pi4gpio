# セッション引き継ぎメモ（pi4gpioプロジェクト）

`pi4gpio`は設計フェーズを終え、Rustワークスペースの雛形（`crates/pi4gpio-daemon`・`crates/pi4gpio-hw`）を作成した段階。ハードウェアレジスタ操作などの実装本体はまだ未着手。本ファイルは、このプロジェクトの議論だけを別セッションに引き継ぐための独立メモ（week09全体の引き継ぎとは別スコープ）。作業日: 2026-07-12。

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

## 3. 想定される課題（設計前に洗い出し済み、未解決）

### コア実装
- DMA制御ブロックの連結リストによるGPIOレジスタ直叩きをPi4(BCM2711)向けに自前実装する必要があり、メモリマップドI/Oの微妙なバグ（volatile未指定・メモリバリア漏れ等）はSoC全体のハングに直結し得る
- userspaceからの`/dev/mem`直接アクセスと、カーネル自身のpinctrl/gpioサブシステムが同じピンを同時に触る競合の可能性
- DMAチャネルは他のカーネルドライバ（オーディオ、SPI/I2Cのハードウェアドライバ等）とも共有資源であり、衝突回避が必要

### 機能スコープ
- pigpioの全機能（波形生成、ハードウェアPWM、サーボパルス、汎用クロック出力、GPIO通知/コールバック、I2C・SPIのハードウェア版とビットバンギング版、UART、1-Wire、スクリプトエンジン）を本気で引き継ぐと、pigpio本家に匹敵する開発規模になる。段階的な機能優先順位付けが必須

### 排他制御の再設計（`rpi-hw-lock`退役に伴う最重要ポイント）
`rpi-hw-lock`の「サービス単位の排他（他を止めて自分だけが使う）」から、「複数クライアントが1つのデーモンを時分割共有する」設計に転換するため:
- バス単位・トランザクション単位のロック機構をデーモン内部に実装する必要（I2C/SPIの複数ステップ通信を他クライアントの割り込みから守る）
- クライアントがロックを持ったままクラッシュした場合の自動解放（ソケット切断検知→ロック解放）
- 複数クライアントが同じバスを取り合った際の優先度付け・タイムアウト・デッドロック回避
- `rpi-hw-lock`が担保していた「誤って止めていたサービスを再起動しない」等の安全策も作り直しが必要

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

### 言語選択（未決定）
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
