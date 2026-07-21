#!/usr/bin/env python3
"""pi4gpiodのsystemd自動再起動を検証する、安全弁付き障害注入ツール。

既定は読み取り専用のdry-runである。``--execute``を指定しても、week09本番
またはカナリアが稼働中なら拒否する。保守時間帯にそれらを停止したうえで使う
ことを想定し、対象外サービスの停止・再起動・設定変更は一切行わない。
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import time
from pathlib import Path


DEFAULT_SOCKET_PATH = "/run/pi4gpio/pi4gpio.sock"
DAEMON_SERVICE = "pi4gpio.service"
PROTECTED_SERVICES = (
    "sensor-tiered-client.service",
    "canary-compare.service",
)


class SafetyError(RuntimeError):
    """障害注入の安全条件を満たさない。"""


class SystemdController:
    def is_active(self, service: str) -> bool:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service], check=False
        )
        return result.returncode == 0

    def property_int(self, service: str, name: str) -> int:
        result = subprocess.run(
            ["systemctl", "show", service, f"--property={name}", "--value"],
            check=True,
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip() or "0")

    def kill_main(self, service: str) -> None:
        subprocess.run(
            [
                "systemctl",
                "kill",
                "--kill-who=main",
                "--signal=KILL",
                service,
            ],
            check=True,
        )


def probe_protocol(socket_path: str, timeout: float = 1.0) -> None:
    """ハードウェアへ触れない不正要求を送り、daemonの応答経路だけを検査する。"""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(socket_path)
        sock.sendall(b"{}\n")
        reader = sock.makefile("rb")
        line = reader.readline()
        reader.close()
    if not line:
        raise ConnectionError("pi4gpiodから応答がありません")
    response = json.loads(line)
    if response.get("ok") is not False:
        raise RuntimeError("不正要求に対する想定外の応答です")


def run_fault_injection(
    *,
    execute: bool,
    socket_path: str = DEFAULT_SOCKET_PATH,
    timeout: float = 15.0,
    controller=None,
    probe=probe_protocol,
    sleeper=time.sleep,
):
    """安全条件を確認し、許可された場合だけmain PIDへSIGKILLを送る。"""
    controller = controller or SystemdController()
    if not controller.is_active(DAEMON_SERVICE):
        raise SafetyError(f"{DAEMON_SERVICE}がactiveではありません")

    protected_active = [
        service for service in PROTECTED_SERVICES if controller.is_active(service)
    ]
    probe(socket_path)
    before_pid = controller.property_int(DAEMON_SERVICE, "MainPID")
    before_restarts = controller.property_int(DAEMON_SERVICE, "NRestarts")
    result = {
        "mode": "execute" if execute else "dry-run",
        "daemon_service": DAEMON_SERVICE,
        "socket_path": socket_path,
        "protected_services_active": protected_active,
        "before_pid": before_pid,
        "before_restarts": before_restarts,
        "hardware_operations": 0,
    }

    if not execute:
        result["would_execute"] = not protected_active
        return result

    if protected_active:
        raise SafetyError(
            "week09への影響を避けるため障害注入を拒否しました。先に停止が必要: "
            + ", ".join(protected_active)
        )
    if before_pid <= 0:
        raise SafetyError("有効なpi4gpiod MainPIDを取得できません")

    controller.kill_main(DAEMON_SERVICE)
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        sleeper(0.2)
        try:
            after_pid = controller.property_int(DAEMON_SERVICE, "MainPID")
            after_restarts = controller.property_int(DAEMON_SERVICE, "NRestarts")
            if (
                controller.is_active(DAEMON_SERVICE)
                and after_pid > 0
                and after_pid != before_pid
                and after_restarts > before_restarts
                and Path(socket_path).exists()
            ):
                probe(socket_path)
                result.update(
                    {
                        "recovered": True,
                        "after_pid": after_pid,
                        "after_restarts": after_restarts,
                    }
                )
                return result
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = str(exc)

    raise RuntimeError(
        f"{timeout:.1f}秒以内にpi4gpiodの復旧を確認できませんでした: {last_error}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="安全条件を満たす場合だけpi4gpiod main PIDへSIGKILLを送る",
    )
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    if platform.system() != "Linux" or os.name != "posix":
        parser.error("このツールはsystemdを使うLinux実機専用です")
    if args.timeout <= 0:
        parser.error("--timeoutは0より大きい値が必要です")

    try:
        result = run_fault_injection(
            execute=args.execute, socket_path=args.socket, timeout=args.timeout
        )
    except (SafetyError, OSError, ValueError, RuntimeError) as exc:
        print(f"ABORTED: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
