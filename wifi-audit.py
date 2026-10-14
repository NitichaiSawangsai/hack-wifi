#!/usr/bin/env python3

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ============================================================
# CONFIG
# ============================================================

WIFI_FILE = Path("wifi.txt")
PASSWORD_FILE = Path("password.txt")
CHECKPOINT_FILE = Path(".wifi-audit-checkpoint.json")

INTERFACE = "wlan0"

# แสดง progress ทุกกี่ password
PROGRESS_EVERY = 100_000

# บันทึก checkpoint ทุกกี่ password
CHECKPOINT_EVERY = 100_000


# ============================================================
# COMMAND
# ============================================================

def run_command(args, timeout=30):
    try:
        return subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    except subprocess.TimeoutExpired:
        return None


# ============================================================
# NETWORKMANAGER
# ============================================================

def rescan_wifi():
    result = run_command([
        "nmcli",
        "device",
        "wifi",
        "rescan",
        "ifname",
        INTERFACE,
    ])

    if result is None:
        return False

    time.sleep(2)

    return result.returncode == 0


def wifi_visible(ssid):
    result = run_command([
        "nmcli",
        "-t",
        "-f",
        "SSID",
        "device",
        "wifi",
        "list",
        "ifname",
        INTERFACE,
    ])

    if result is None:
        return False

    if result.returncode != 0:
        return False

    networks = {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    }

    return ssid in networks


def connection_state():
    result = run_command([
        "nmcli",
        "-t",
        "-f",
        "GENERAL.STATE",
        "device",
        "show",
        INTERFACE,
    ])

    if result is None:
        return False

    if result.returncode != 0:
        return False

    return "100 (connected)" in result.stdout


def disconnect_wifi():
    result = run_command([
        "nmcli",
        "device",
        "disconnect",
        INTERFACE,
    ])

    return (
        result is not None
        and result.returncode == 0
    )


# ============================================================
# VERIFIER
# ============================================================

def check_candidate(ssid, candidate):
    """
    ==========================================================
    OFFLINE / ISOLATED VERIFIER HOOK
    ==========================================================

    INPUT

        ssid
        candidate

    OUTPUT

        True  = candidate ผ่าน
        False = candidate ไม่ผ่าน


    ตรงนี้ให้เชื่อมกับ offline verifier / isolated test
    environment ที่คุณควบคุม


    ตัวอย่างโครง:

        result = YOUR_OFFLINE_VERIFIER(
            ssid,
            candidate
        )

        return result


    ----------------------------------------------------------
    ไม่ได้ใส่ในส่วนนี้
    ----------------------------------------------------------

    - การนำ candidate แต่ละตัวไป authentication กับ AP จริง
    - automated Wi-Fi password guessing
    - MAC rotation
    - identity rotation
    - timing สำหรับหลบ detection
    - bypass rate limit
    - IDS/WIPS evasion

    """

    return False


# ============================================================
# INPUT
# ============================================================

def check_files():
    if not WIFI_FILE.exists():
        sys.exit(
            f"ERROR: ไม่พบ {WIFI_FILE.resolve()}"
        )

    if not PASSWORD_FILE.exists():
        sys.exit(
            f"ERROR: ไม่พบ {PASSWORD_FILE.resolve()}"
        )

    if not shutil.which("nmcli"):
        sys.exit(
            "ERROR: ไม่พบ nmcli"
        )


def load_wifi_targets():
    with WIFI_FILE.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
    ) as file:

        targets = [
            line.strip()
            for line in file
            if line.strip()
        ]

    if not targets:
        sys.exit(
            "ERROR: wifi.txt ไม่มี SSID"
        )

    return targets


# ============================================================
# CHECKPOINT
# ============================================================

def save_checkpoint(
    ssid_index,
    ssid,
    line_number,
):
    data = {
        "ssid_index": ssid_index,
        "ssid": ssid,
        "line": line_number,
    }

    temp_file = Path(
        str(CHECKPOINT_FILE) + ".tmp"
    )

    temp_file.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # atomic-ish replacement
    temp_file.replace(
        CHECKPOINT_FILE
    )


def load_checkpoint():
    if not CHECKPOINT_FILE.exists():
        return None

    try:
        return json.loads(
            CHECKPOINT_FILE.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        ValueError,
        TypeError,
    ):
        return None


def clear_checkpoint():
    try:
        CHECKPOINT_FILE.unlink(
            missing_ok=True
        )
    except OSError:
        pass


# ============================================================
# AUDIT ONE WIFI
# ============================================================

def audit_wifi(
    ssid,
    ssid_index,
    total,
    start_line=0,
):
    print()

    print("=" * 76)

    print(
        f"[{ssid_index + 1}/{total}] "
        f"SSID: {ssid}"
    )

    print("=" * 76)

    # --------------------------------------------------------
    # RESCAN
    # --------------------------------------------------------

    print(
        "Rescan Wi-Fi...",
        end="",
        flush=True,
    )

    if not rescan_wifi():

        print(" ERROR")

        return {
            "ssid": ssid,
            "success": False,
            "password": None,
            "password_line": None,
            "checked": 0,
            "reason": "RESCAN ERROR",
        }

    print(" OK")

    # --------------------------------------------------------
    # CHECK SSID
    # --------------------------------------------------------

    print(
        "Check SSID...",
        end="",
        flush=True,
    )

    if not wifi_visible(ssid):

        print(" NOT FOUND")

        return {
            "ssid": ssid,
            "success": False,
            "password": None,
            "password_line": None,
            "checked": 0,
            "reason": "SSID NOT VISIBLE",
        }

    print(" FOUND")

    # --------------------------------------------------------
    # PASSWORD
    # --------------------------------------------------------

    print()

    print(
        f"Password file : "
        f"{PASSWORD_FILE}"
    )

    if start_line > 0:

        print(
            f"Resume        : "
            f"after line {start_line:,}"
        )

    else:

        print(
            "Starting      : line 1"
        )

    print()

    checked = 0

    started_at = time.monotonic()

    #
    # password.txt ไม่ถูกโหลดทั้งหมดเข้า RAM
    #
    # อ่านทีละบรรทัด
    #
    # รองรับไฟล์หลายล้าน row
    #

    with PASSWORD_FILE.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
        buffering=1024 * 1024,
    ) as password_file:

        for line_number, line in enumerate(
            password_file,
            1,
        ):

            # -----------------------------------------------
            # RESUME
            # -----------------------------------------------

            if line_number <= start_line:
                continue

            # -----------------------------------------------
            # PASSWORD
            # -----------------------------------------------

            candidate = line.rstrip(
                "\r\n"
            )

            if not candidate:
                continue

            checked += 1

            # -----------------------------------------------
            # VERIFY
            # -----------------------------------------------

            success = check_candidate(
                ssid,
                candidate,
            )

            # -----------------------------------------------
            # SUCCESS
            # -----------------------------------------------

            if success:

                print()

                print("*" * 76)
                print("MATCH FOUND")
                print("*" * 76)

                print(
                    f"SSID          : "
                    f"{ssid}"
                )

                print(
                    f"PASSWORD      : "
                    f"{candidate}"
                )

                print(
                    f"PASSWORD LINE : "
                    f"{line_number:,}"
                )

                print(
                    f"CHECKED       : "
                    f"{checked:,}"
                )

                return {
                    "ssid": ssid,
                    "success": True,
                    "password": candidate,
                    "password_line": line_number,
                    "checked": checked,
                    "reason": "",
                }

            # -----------------------------------------------
            # CHECKPOINT
            # -----------------------------------------------

            if (
                checked
                % CHECKPOINT_EVERY
                == 0
            ):

                save_checkpoint(
                    ssid_index,
                    ssid,
                    line_number,
                )

            # -----------------------------------------------
            # PROGRESS
            # -----------------------------------------------

            if (
                checked
                % PROGRESS_EVERY
                == 0
            ):

                elapsed = (
                    time.monotonic()
                    - started_at
                )

                if elapsed > 0:
                    rate = (
                        checked
                        / elapsed
                    )
                else:
                    rate = 0

                print(
                    f"Checked       : "
                    f"{checked:,}"
                    f" | line={line_number:,}"
                    f" | rate={rate:,.0f}/s"
                )

    # --------------------------------------------------------
    # NOT FOUND
    # --------------------------------------------------------

    print()

    print(
        "RESULT        : N"
    )

    print(
        f"CHECKED       : "
        f"{checked:,}"
    )

    return {
        "ssid": ssid,
        "success": False,
        "password": None,
        "password_line": None,
        "checked": checked,
        "reason": "NO MATCH",
    }


# ============================================================
# SUMMARY
# ============================================================

def print_summary(results):

    passed = [
        result
        for result in results
        if result["success"]
    ]

    failed = [
        result
        for result in results
        if not result["success"]
    ]

    print()

    print("=" * 76)
    print("WIFI AUDIT SUMMARY")
    print("=" * 76)

    print(
        f"TOTAL   : "
        f"{len(results)}"
    )

    print(
        f"SUCCESS : "
        f"{len(passed)}"
    )

    print(
        f"FAILED  : "
        f"{len(failed)}"
    )

    # ========================================================
    # SUCCESS
    # ========================================================

    print()

    print("=" * 76)
    print("SUCCESS")
    print("=" * 76)

    if not passed:

        print("None")

    else:

        for number, result in enumerate(
            passed,
            1,
        ):

            print()

            print(
                f"[{number}] "
                f"{result['ssid']}"
            )

            print(
                f"    PASSWORD : "
                f"{result['password']}"
            )

            print(
                f"    LINE     : "
                f"{result['password_line']:,}"
            )

            print(
                f"    CHECKED  : "
                f"{result['checked']:,}"
            )

    # ========================================================
    # FAILED
    # ========================================================

    print()

    print("=" * 76)
    print("FAILED")
    print("=" * 76)

    if not failed:

        print("None")

    else:

        for number, result in enumerate(
            failed,
            1,
        ):

            print(
                f"[{number}] "
                f"{result['ssid']:<30} "
                f"N  "
                f"checked="
                f"{result['checked']:,}  "
                f"{result['reason']}"
            )


# ============================================================
# MAIN
# ============================================================

def main():

    check_files()

    wifi_list = (
        load_wifi_targets()
    )

    checkpoint = (
        load_checkpoint()
    )

    # --------------------------------------------------------
    # DEFAULT START
    # --------------------------------------------------------

    start_ssid_index = 0
    resume_line = 0

    # --------------------------------------------------------
    # LOAD CHECKPOINT
    # --------------------------------------------------------

    if checkpoint:

        try:

            start_ssid_index = int(
                checkpoint.get(
                    "ssid_index",
                    0,
                )
            )

            resume_line = int(
                checkpoint.get(
                    "line",
                    0,
                )
            )

        except (
            ValueError,
            TypeError,
        ):

            start_ssid_index = 0
            resume_line = 0

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    print("=" * 76)
    print("WIFI AUDIT")
    print("=" * 76)

    print(
        f"Interface     : "
        f"{INTERFACE}"
    )

    print(
        f"Wi-Fi targets: "
        f"{len(wifi_list)}"
    )

    print(
        f"Wi-Fi file   : "
        f"{WIFI_FILE.resolve()}"
    )

    print(
        f"Password file: "
        f"{PASSWORD_FILE.resolve()}"
    )

    # --------------------------------------------------------
    # CHECKPOINT INFO
    # --------------------------------------------------------

    if (
        checkpoint
        and resume_line > 0
    ):

        print()

        print(
            "Checkpoint detected"
        )

        print(
            f"SSID index    : "
            f"{start_ssid_index + 1}"
        )

        print(
            f"SSID          : "
            f"{checkpoint.get('ssid', '')}"
        )

        print(
            f"Resume after  : "
            f"{resume_line:,}"
        )

    # --------------------------------------------------------
    # RESULTS
    # --------------------------------------------------------

    results = []

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    try:

        for index, ssid in enumerate(
            wifi_list
        ):

            # SSID ก่อน checkpoint
            if index < start_ssid_index:
                continue

            # ใช้ resume line เฉพาะ SSID
            # ที่ checkpoint อยู่
            if index == start_ssid_index:
                line = resume_line
            else:
                line = 0

            # -----------------------------------------------
            # AUDIT
            # -----------------------------------------------

            result = audit_wifi(
                ssid,
                index,
                len(wifi_list),
                start_line=line,
            )

            results.append(
                result
            )

            # -----------------------------------------------
            # SSID COMPLETE
            # -----------------------------------------------

            #
            # เมื่อ SSID ปัจจุบันเสร็จ
            # checkpoint ไป SSID ถัดไป
            #

            save_checkpoint(
                index + 1,
                "",
                0,
            )

            resume_line = 0

            # -----------------------------------------------
            # CLEAN CONNECTION
            # -----------------------------------------------

            if connection_state():

                print()

                print(
                    f"Disconnecting "
                    f"{INTERFACE}..."
                )

                disconnect_wifi()

                time.sleep(1)

        # -----------------------------------------------
        # COMPLETE
        # -----------------------------------------------

        clear_checkpoint()

    # ========================================================
    # CTRL+C
    # ========================================================

    except KeyboardInterrupt:

        print()

        print()
        print("=" * 76)
        print("STOPPED")
        print("=" * 76)

        print(
            "Checkpoint ถูกเก็บไว้"
        )

        print(
            f"File: "
            f"{CHECKPOINT_FILE.resolve()}"
        )

        print()

        print(
            "เปิดใหม่ด้วย:"
        )

        print(
            "python3 wifi-audit.py"
        )

        print()

        print(
            "โปรแกรมจะ resume "
            "จาก checkpoint"
        )

        return 130

    # ========================================================
    # SUMMARY
    # ========================================================

    print_summary(
        results
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    raise SystemExit(
        main()
    )