# Pi4gpioテスト事前準備チェックポイント

最終更新: 2026-07-23
作業ブランチ: `agent/prepare-pi4gpio-testing`

## 完了済み

- 常駐カナリアをPiから撤去。week09本番とpi4gpiodは無停止
- Pi4gpio統合試験ランナー`scripts/pi4gpio_sensor_test.py`を実装
- ランナーはdry-run既定、本番serviceがinactiveでなければセンサー生成前に拒否、出力はローカルJSONLのみ
- ローカル運用スクリプトテスト18件、Pythonクライアントテスト6件、week09周期設定テスト4件に成功
- Python構文、Rust format、aarch64向けcheck/clippy、`git diff --check`に成功
- Piへランナーを配置し、5秒dry-runで`hardware_operations=0`を確認
- Pi上の`--execute`はactiveなweek09を検出し終了コード2、出力ファイル未作成
- `/home/pi/pi4gpio-test/.venv`を本番から分離して作成
- test venv: `rpi-sensor-lib==0.2.0`、`pi4gpio-client==0.1.0`、`RPi.bme280==0.2.4`、`smbus2==0.6.1`、`pytz==2026.2`
- week09ローカル版へ`SENSOR_SEND_INTERVAL_SEC`を追加。既定10秒、5秒指定可能、monotonic周期、超過周期の重複実行防止を実装
- 実機week09には周期変更を未配備。現在も10秒direct運転
- DBとjournalの両方から正式24時間direct基準値を取得し、`baselines/direct_24h_20260723.json`へ保存
- 初回Pi4gpio実機試験を隔離状態で実施。スモーク1周期、予備7周期、本試験60周期の全取得に成功
- 本試験は全センサー60/60成功、開始周期平均10.0秒、overrun 0、FD 6固定、daemon再起動0
- 切り戻し時、クライアント終了後もpi4gpiodがI2C/SPI/UARTのFDを保持する問題を検出。daemon再起動で解放し、direct本番へ正常復帰
- 機械可読結果を`baselines/pi4gpio_initial_10min_20260723.json`へ保存
- journal解析が指定時間より1時間多く失敗行を数える不具合を発見し、正確な指定時間へ修正して回帰テストを追加
- 修正版解析スクリプトをPiへ配置し、ローカル／Pi SHA-256一致を確認

## 正式24時間基準値（2026-07-23 10:21 JST）

- journal/SQLiteとも23.999時間、timer 8,636件、button 0件
- 平均間隔10.005秒、最小10秒、最大11秒、10秒±0.5秒外46件
- 全7センサーの有効レコード8,636件、成功率100%
- センサー失敗周期0件、DHT22/BME280/MH-Z19C失敗言及0件、通信エラー0件
- 両経路でレコード数・周期統計・有効レコード数が一致。50秒ずれた窓による平均値の丸め差だけを確認
- 取得前後ともweek09 PID 1253、pi4gpiod PID 967、sensor-server PID 223299、全てactive・`NRestarts=0`
- 現行クライアントは取得処理時間を記録しないため、direct基準の処理時間は取得不能

## 次の作業

1. クライアントRelease／切断時にI2C/SPI/UARTのキャッシュ済みハンドルもcloseするdaemon修正を設計・実装する
2. 切断cleanupと非所有者Releaseの回帰テストを追加する
3. 修正版daemonを保守時間帯に配備し、60周期試験と「daemon再起動なしのdirect切り戻し」を再検証する
4. 修正完了までは、Pi4gpio試験後にdaemonを再起動してFD解放を確認してからdirectを開始する

## GitHub側の状態

- `gh auth status`はユーザー`kazuki1729`で正常
- `agent/prepare-pi4gpio-testing`をoriginへpush済み
- Draft Pull Request #2: https://github.com/kazuki1729/pi4gpio/pull/2
- 正式24時間基準と初回実機試験は完了。切り戻し時の残留FD問題が未修正のため、Draftを維持する

## 変更してはいけないもの

- `/home/pi/sensor-tiered-store`の本番venv・本番クライアント
- `/etc/systemd/system/sensor-tiered-client.service`
- 現在のdirectバックエンドと10秒周期
- activeなweek09／pi4gpiodの停止・再起動

2026-07-23の実機試験はweek09停止・対象デバイス保持者0を確認した隔離状態でのみ実行した。
