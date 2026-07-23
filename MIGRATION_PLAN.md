# `rpi-sensor-lib`移行計画（Migration Plan）

`SESSION_HANDOFF.md` §4-4「`rpi-sensor-lib`移行の具体的な段取り」について検討した内容をまとめる。作業日: 2026-07-12。

## 1. 現状の関係整理

```
[sensor-tiered-client.service（本番常駐）]
    → rpi-sensor-lib（spidev/smbus2/lgpio/serialを直接叩く）

[アドホックスクリプト（新規に何か試したい時）]
    → rpi-hw-lock の exclusive_hardware_access()
         → sensor-tiered-client.service を systemctl stop
         → アドホックスクリプトが rpi-sensor-lib 経由でハードウェアを使う
         → 処理後、サービスを systemctl start で復元
```

`rpi-hw-lock`は`rpi-sensor-lib`に組み込まれたものではなく、**「同じハードウェアを使う本番サービスを一時的に止めて明け渡す」という別レイヤーの排他制御**である点が重要。pigpiodが不採用だった当時（週09 §12.7）、常駐デーモン方式の代替として選ばれた経緯がある。

## 2. pi4gpio導入後の姿

```
[sensor-tiered-client.service] ─┐
                                  ├→ pi4gpio デーモン（常駐、複数クライアントを時分割仲裁）
[アドホックスクリプト]      ─┘
```

pi4gpioが完成すれば、アドホックスクリプトは`rpi-hw-lock`で本番サービスを止める必要がなくなる。**両方とも同時にpi4gpioデーモンへ接続し、デーモン側が排他制御を担う**ため、「止めて明け渡す」という運用そのものが不要になる。これが`rpi-hw-lock`退役の意味するところ。

## 3. 移行の前提条件（Phase 0）

- ✅ pi4gpioのTier 1機能（`FEATURE_PRIORITY.md`：I2C・SPI・GPIO基本読み書き・UART）が実装され、実機（Pi 4、`kazuki1729.local`）で動作確認済み（2026-07-12、`VERIFICATION_LOG.md`参照）
- ✅ pi4gpioがsystemdサービスとして安定起動・自動再起動する状態になっていること（2026-07-13、`VERIFICATION_LOG.md`参照）。`User=pi`・`RuntimeDirectory=pi4gpio`・`Restart=on-failure`で実機に配置・有効化済み。`kill -9`による強制終了からの自動復帰も確認済み
- この段階ではリモート通信は不要（`sensor-tiered-client.service`もアドホックスクリプトも同一Pi上で動くため、`NETWORK_POLICY.md`のTailscale/APIキーはローカルソケット運用の範囲では関与しない）

## 4. `rpi-sensor-lib`側の対応：二重モード化 — ✅ 実装完了（2026-07-13）

各センサークラス（`bme280_pressure.py`・`grove_mcp3208_sensors.py`・`joystick_mcp3208.py`・`potentiometer_mcp3208.py`・`robust_dht22.py`・`tactile_button.py`・`mh_x19c_co2.py`）に、直接ハードウェアアクセスとpi4gpio経由アクセスを切り替えられるバックエンド抽象化を導入した。

- 環境変数`RPI_SENSOR_BACKEND=direct|pi4gpio`で切り替え可能（`kazuki1729/rpi-sensor-lib`の`rpi_sensors/_pi4gpio_backend.py`）
- デフォルトは`direct`のまま維持し、`pi4gpio`は明示的にオプトインした場合のみ有効化
- これにより、pi4gpio側に問題が出ても設定変更＋サービス再起動だけで即座に切り戻せる
- I2C/SPI/UARTそれぞれに`smbus2`/`spidev`/`pyserial`互換シム（`Pi4gpioSMBusShim`/`Pi4gpioSpiTransferShim`/`Pi4gpioSerialShim`）を用意し、`bme280`パッケージ等のサードパーティ依存は無改造で動く設計にした

## 5. センサー単位の段階的移行順序 — ✅ 全5項目実装・実機検証完了（2026-07-13）

全センサーを一度に切り替えず、影響範囲とリスクが小さいものから順に検証した。実機検証の詳細は`VERIFICATION_LOG.md`参照。

| 順序 | 対象 | 理由 | 状態 |
|---|---|---|---|
| 1 | `tactile_button.py` | 単純なデジタル入力のみ。リアルタイム性要求が緩く、影響が最小 | ✅ 完了 |
| 2 | `bme280_pressure.py` | I2C、読み取り頻度が低く許容誤差も大きい | ✅ 完了 |
| 3 | `grove_mcp3208_sensors.py` / `joystick_mcp3208.py` / `potentiometer_mcp3208.py` | SPI経由のADC。プロトコル自体はシンプルだが読み取り頻度がやや高い | ✅ 完了 |
| 4 | `mh_x19c_co2.py` | UART。パケット単位のプロトコルなのでSPI/I2Cとは異なる検証が要る | ✅ 完了 |
| 5 | `robust_dht22.py` | 最重要かつ最高難度。`FEATURE_PRIORITY.md` Tier 2（GPIO通知/コールバック）に依存し、pi4gpioの真価が問われる部分。既存のbusy-loop実装との読み取り成功率比較が必須なため最後に回す | ✅ 完了 |

**注意**: ✅ 2026-07-13、ユーザーによるセンサー再接続後、全7センサークラスをdirect/pi4gpio両モードで実データ検証完了（`VERIFICATION_LOG.md`参照）。この過程で`robust_dht22`のpi4gpioモードがカーネルGPIO v2エッジ割り込みの取りこぼしにより実機では常に失敗する重大バグを発見し、Tier 1相当の高速ポーリング方式（`WatchEdgesPolled`、新規実装）へ切り替えて解決した。

## 6. 並行稼働・カナリア検証

- `direct`本番とpi4gpioカナリアが同じハードウェアへ触れる並行比較は行わない。daemon内部のLockTableはdirectプロセスを仲裁できず、UARTで実害が発生したほか、GPIO/DHT22にも同じ構造的リスクがあるため
- 本番がdirectの期間、`scripts/canary_compare.py`を使う場合はセンサーハードウェアを一切開かない受動診断として動作させる。全センサー値は本番journalから取得し、pi4gpiodはUnixソケットの応答性だけを監視する
- `canary-compare.service`には`PrivateDevices=true`・`DevicePolicy=closed`・`ProtectKernelTunables=true`等を設定し、コードが後退してもGPIO/I2C/SPI/UARTへアクセスできないようOSレベルで強制する
- 既存の`sensor-monitor.service`と監視対象が重複し、direct本番ではpi4gpiodを利用しないため、2026-07-21に実機の常駐カナリア・自動起動・CSVを撤去した。unitとスクリプトは保守時間帯の一時検証用テンプレートとしてのみ保持する
- direct/pi4gpioの値比較は、本番を停止した保守時間帯、または本番自体がpi4gpioへ移行しdirectアクセスをOSレベルで禁止した後だけ実施する
- ✅ センサー再接続後の実データ比較を実施（2026-07-13）。本番停止中の一時的な検証で全7センサーの実データ突き合わせが完了し、DHT22の重大バグを発見・修正した
- ⚠️ `canary-compare.service`としての初期運用ではMH-Z19Cの二重読み取りが混入していた。旧CSVのUART比較値は競合の影響を受け得るため、移行判定の根拠から除外する。完全受動版の短時間検証後、常駐カナリアと旧CSVは撤去済み

本番のpi4gpio移行時は`systemd/sensor-tiered-client-pi4gpio-exclusive.conf`を
drop-inとして適用し、対象サービスから物理デバイスを不可視化する。共有Pi全体の
udev権限は変更せず、他プロジェクトへの影響を避ける。詳細は`EXCLUSIVE_ACCESS.md`。

## 7. 切り戻し手順

- `RPI_SENSOR_BACKEND`を`direct`に戻して該当サービスを再起動するだけで即座に切り戻せる設計にする
- pi4gpioデーモンがクラッシュした場合のdirectアクセスへの自動フォールバックは**実装しない**。クライアントはデーモンへ有界再接続するが、切断時に処理中だった要求は二重実行を避けて自動再送せず、`Pi4gpioConnectionError`としてその周期を失敗にする。再接続後の次の通常操作でバスロックを再取得する。directへの切り戻しは人間の判断で行う
- ⚠️ 2026-07-23の実機試験で、最後のクライアント切断後もdaemonのI2C/SPI/UARTキャッシュがデバイスFDを保持することを確認した。単純にdirectサービスを開始するとdaemonとdirectの二重保持になるため、修正完了までは「Pi4gpioクライアント停止→pi4gpiod再起動→`fuser`で対象FD解放確認→direct開始」を必須とする
- 本番移行の受入条件に、クライアントRelease／切断後のキャッシュ済みハンドルcloseと、daemon再起動なしで安全にdirectへ切り戻せる実機確認を追加する
- 恒久修正では、接続単位の`session_id`でロック所有者を識別し、所有者確認中にキャッシュ済みハンドルをdropしてからロックを明け渡す。ロック解放後にcloseする順序は、次クライアントが古いFDを参照する競合窓を作るため採用しない
- ✅ コミット`ab94cd4`を実機配備し、60周期後の保持者0と、daemon再起動なしでdirect PIDだけが全対象デバイスを保持することを確認した。残留FDに関する本番移行ブロッカーは解除する

## 8. 障害注入テスト

- CIでは`socket.socketpair()`上の模擬デーモンを応答前に切断し、再接続、再試行上限、処理中要求の非再送、次要求の成功を毎回検査する
- 実機用`scripts/fault_injection_systemd.py`は既定dry-runで、`sensor-tiered-client.service`または`canary-compare.service`がactiveなら`--execute`を拒否する
- 実機試験はweek09を停止できる保守時間帯に限る。スクリプト自身はweek09サービスの停止・再起動・設定変更を行わない
- 詳細は`FAULT_INJECTION.md`を参照する

## 9. `rpi-hw-lock`退役のタイミング

`rpi-hw-lock`は「アドホックスクリプトのために本番サービスを止める」という運用そのものを解消する手段のため、退役の条件は以下の両方が揃った時点。

1. `sensor-tiered-client.service`が全センサーで`pi4gpio`モードに完全移行済み
2. アドホックスクリプト側も`rpi-hw-lock`の`exclusive_hardware_access()`呼び出しをやめ、pi4gpioデーモンへの直接接続に置き換え済み

この2つが揃うまでは`rpi-hw-lock`を残す。

## 10. 完全移行後のクリーンアップ

- カナリア期間で問題なければ、`rpi-sensor-lib`から`direct`モードのコードパス（`spidev`/`smbus2`/`lgpio`/`serial`への直接依存）を削除し、`pi4gpio`クライアント経由のみに統一する
- `rpi-hw-lock`パッケージ自体はPyPIから即座に削除はせず、非推奨（deprecated）表示に留めて既存利用者への影響を避ける

## 11. テストの限界

DMA/PWMタイミングの正しさは実機・ロジックアナライザでしか検証できないため（`SESSION_HANDOFF.md` §3）、この移行の検証もCIでは完結しない。各Phaseの動作確認は実機（Pi 4、`kazuki1729.local`、Tailscale経由IP直指定）で行う。
