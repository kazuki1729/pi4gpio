# 障害注入テスト

## 自動テスト（ハードウェア・systemd不要）

模擬ソケットを使い、要求受信後・応答前にデーモン相当側を切断する。次を検査する。

- 壊れた接続を破棄して再接続する
- デーモンの復帰が遅れた場合も設定回数内で再試行する
- 実行済みか判別できない処理中要求を新接続へ自動再送しない
- 再接続後の次の通常要求が成功する
- 接続試行が上限で停止し、型付き例外を返す

```bash
python -m unittest discover -s clients/python/tests -v
python -m unittest discover -s scripts/tests -v
```

この検査はGitHub ActionsのPython 3.9／3.12でも毎回実行する。

## 実機systemd試験

`scripts/fault_injection_systemd.py`は次の安全制約を持つ。

- 既定はdry-runで、プロセスを停止しない
- `sensor-tiered-client.service`または`canary-compare.service`がactiveなら、実行モードを拒否する
- 他サービスの停止・再起動・設定変更を行わない
- ハードウェア操作を送らず、不正な空JSONに対するプロトコルエラー応答だけを確認する
- `MainPID`の変更、`NRestarts`の増加、Unixソケットの再生成、応答復旧をすべて合格条件にする

まず読み取り専用確認を行う。

```bash
python3 scripts/fault_injection_systemd.py
```

出力の`would_execute`が`false`なら、保護対象サービスが動いている。week09稼働中に
試験を続行してはならない。保守時間帯に管理者が別途サービス停止と影響確認を行い、
保護対象がすべてinactiveになった後だけ次を実行する。

```bash
sudo python3 scripts/fault_injection_systemd.py --execute
```

このツールは意図的にweek09を停止しない。実機試験後のweek09起動・センサー値・
未送信データ再送の確認も、既存の運用手順に従って人が実施する。
