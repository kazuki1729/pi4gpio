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

**注意**: 実センサーが物理的に未接続の状態が移行作業全体を通じて続いていたため、実装・通信経路・エラーハンドリングの検証（実機）と、DHT22デコードロジックの検証（既知の合成データ）は完了しているが、**実際の温湿度値等での動作確認はまだ行えていない**。センサー再接続後に改めて実データでの検証が必要。

## 6. 並行稼働・カナリア検証

- 各センサーの移行時、`direct`モードと`pi4gpio`モードを同じPi上で並行稼働させ、一定期間（目安1〜2週間）値の一致・レイテンシ・エラー率を比較する
- 特に`robust_dht22.py`は、pi4gpioのGPIO通知機能に切り替えた後の読み取り成功率（現状`max_retries=5`でのリトライ率）が、既存実装より改善しているか（悪化していないか）を定量的に確認する
- 本番の`sensor-tiered-client.service`は`direct`モードのまま動かし続け、並行稼働は別プロセス（テスト用の一時的なサービスまたはcronジョブ）で行う。本番を止めない
- ✅ 比較用スクリプト`scripts/canary_compare.py`を準備済み・構造テスト完了（2026-07-13、`VERIFICATION_LOG.md`参照）。I2C/SPI系センサーはdirect/pi4gpio両方を並行読み取りしCSVへ記録、GPIO/UART/DHT22系（本番と競合しうる）はpi4gpioモードのみ読み取り本番の`journalctl`ログと突き合わせる設計。**実センサーが物理的に未接続のため実データでの比較はまだ未着手** — センサー再接続後に本格稼働させる

## 7. 切り戻し手順

- `RPI_SENSOR_BACKEND`を`direct`に戻して該当サービスを再起動するだけで即座に切り戻せる設計にする
- pi4gpioデーモンがクラッシュした場合の自動フォールバックは**あえて実装しない**（`pi4gpio`モード中に接続断が起きたら明示的にエラーとして扱う）。自動フォールバックは「デーモンが複数クライアントの排他を一元管理する」という設計意図と矛盾しうるため、切り戻しは人間の判断で行う

## 8. `rpi-hw-lock`退役のタイミング

`rpi-hw-lock`は「アドホックスクリプトのために本番サービスを止める」という運用そのものを解消する手段のため、退役の条件は以下の両方が揃った時点。

1. `sensor-tiered-client.service`が全センサーで`pi4gpio`モードに完全移行済み
2. アドホックスクリプト側も`rpi-hw-lock`の`exclusive_hardware_access()`呼び出しをやめ、pi4gpioデーモンへの直接接続に置き換え済み

この2つが揃うまでは`rpi-hw-lock`を残す。

## 9. 完全移行後のクリーンアップ

- カナリア期間で問題なければ、`rpi-sensor-lib`から`direct`モードのコードパス（`spidev`/`smbus2`/`lgpio`/`serial`への直接依存）を削除し、`pi4gpio`クライアント経由のみに統一する
- `rpi-hw-lock`パッケージ自体はPyPIから即座に削除はせず、非推奨（deprecated）表示に留めて既存利用者への影響を避ける

## 10. テストの限界

DMA/PWMタイミングの正しさは実機・ロジックアナライザでしか検証できないため（`SESSION_HANDOFF.md` §3）、この移行の検証もCIでは完結しない。各Phaseの動作確認は実機（Pi 4、`kazuki1729.local`、Tailscale経由IP直指定）で行う。
