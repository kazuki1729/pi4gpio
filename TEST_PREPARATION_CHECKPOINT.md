# Pi4gpioテスト事前準備チェックポイント

最終更新: 2026-07-22
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
- DBとjournalからdirect基準値を集計。現行の連続送信payload保持期間は19.308時間で、24時間には未到達
- journal解析が指定時間より1時間多く失敗行を数える不具合を発見し、正確な指定時間へ修正して回帰テストを追加
- 修正版解析スクリプトをPiへ配置し、ローカル／Pi SHA-256一致を確認

## 直近の部分基準値（2026-07-22 01:00 JST時点）

- 期間: 2026-07-21 05:38:35～2026-07-22 00:57:02（19.308時間）
- timer: 6,948件、平均間隔10.005秒、最小9秒、最大11秒、10秒±0.5秒外39件
- 成功率: light/sound/joystick/potentiometer 100%、DHT22 99.122%、BME280 99.928%、MH-Z19C 98.647%
- 直近24時間journal: センサー失敗を含む周期1,831件、DHT22言及1,737件、BME280言及1,681件、MH-Z19C言及1,770件、通信エラー0件
- journal失敗周期には複数センサー失敗で送信を抑止した周期も含むため、送信済み6,948件の欠損数とは一致しない
- week09 PID 1253、pi4gpiod PID 967、両方active、`NRestarts=0`
- 現行クライアントは取得処理時間を記録しないため、direct基準の処理時間は取得不能

## 次回再開時の作業

1. 2026-07-22 05:39 JST以降に`python3 /home/pi/pi4gpio/scripts/analyze_direct_journal.py --hours 24`を再実行
2. `window.actual_hours`が約24時間なら正式なdirect基準値として`VERIFICATION_LOG.md`へ保存
3. 正式基準値を追加する場合は、テスト後にPull Request #2の同じブランチへpush
4. GitHub Actionsと差分を確認し、実機試験を行う保守時間帯を別途決める

## GitHub側の状態

- `gh auth status`はユーザー`kazuki1729`で正常
- `agent/prepare-pi4gpio-testing`をoriginへpush済み
- Draft Pull Request #2: https://github.com/kazuki1729/pi4gpio/pull/2
- 正式24時間基準と実機ハードウェア試験は未完了のため、Draftを維持する

## 変更してはいけないもの

- `/home/pi/sensor-tiered-store`の本番venv・本番クライアント
- `/etc/systemd/system/sensor-tiered-client.service`
- 現在のdirectバックエンドと10秒周期
- activeなweek09／pi4gpiodの停止・再起動

実機ハードウェア操作は一度も実行していない。
