import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# อ่านค่าคอนฟิกจาก Environment Variables (GitHub Secrets)
CUNEX_USER = os.environ.get("CUNEX_USER")
CUNEX_PASS = os.environ.get("CUNEX_PASS")
BUILDING_ID = os.environ.get("BUILDING_ID", "3")
GAS_WEBHOOK_URL = os.environ.get("GAS_WEBHOOK_URL")

LOGIN_URL = "https://cunex.chula.ac.th/admin/login"
TARGET_URL = f"https://cunex.chula.ac.th/admin/booking/table?building_id={BUILDING_ID}"

def parse_cell_status(cell):
    """
    ตรวจสอบสถานะของช่องตาราง (Cell)
    รองรับ: สีเทา/ปิดทำการ (closed), แดง/ชมพู (busy), เหลือง (pending), เขียว (free)
    """
    try:
        # อ่านค่าสีและ attribute ทั้งหมดแบบปลอดภัย
        style_attr = (cell.get_attribute("style") or "").lower()
        class_attr = (cell.get_attribute("class") or "").lower()
        bgcolor_attr = (cell.get_attribute("bgcolor") or "").lower()

        # อ่าน computed background-color จากเบราว์เซอร์
        bg_color = cell.evaluate("el => window.getComputedStyle(el).backgroundColor || ''").lower()

        combined_info = f"{style_attr} {class_attr} {bgcolor_attr} {bg_color}"

        # 1. ตรวจสอบเงื่อนไขสีเทา (ปิดทำการ)
        gray_keywords = ["gray", "grey", "#808080", "#6c757d", "#555", "#666", "#777", "#888", "#999", "#aaa", "#4a4a4a", "#343a40", "disabled", "closed", "lock"]
        if any(k in combined_info for k in gray_keywords):
            return "closed"

        # ตรวจสอบค่า RGB สำหรับสีเทา
        if "rgb" in bg_color:
            nums = [int(n.strip()) for n in bg_color.replace("rgba(", "").replace("rgb(", "").replace(")", "").split(",") if n.strip().isdigit()]
            if len(nums) >= 3:
                r, g, b = nums[0], nums[1], nums[2]
                if abs(r - g) <= 25 and abs(g - b) <= 25 and abs(r - b) <= 25 and 30 <= r <= 220:
                    return "closed"
                if r > g + 40 and r > b:
                    return "busy"
                if r > 160 and g > 160 and b < 100:
                    return "pending"
                if g > r + 30 and g > b + 30:
                    return "free"

        # 2. ตรวจสอบคำระบุสีทั่วไป
        if any(c in combined_info for c in ["red", "#ff0000", "#dc3545", "ชมพู"]):
            return "busy"
        if any(c in combined_info for c in ["yellow", "#ffff00", "#ffc107"]):
            return "pending"
        if any(c in combined_info for c in ["green", "#008000", "#28a745", "#22c55e"]):
            return "free"

    except Exception as e:
        print(f"Cell check error: {e}")

    return "free"


def run_scraper():
    # คำนวณเวลาไทย UTC+7 โดยไม่ต้องพึ่งพาไลบรารีภายนอก
    tz_th = timezone(timedelta(hours=7))
    now_th = datetime.now(tz_th).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_th}] เริ่มต้นการทำงาน CU NEX Scraper...")

    scraped_data = {
        "updated_at": now_th,
        "rooms": []
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        try:
            print("กำลังเปิดหน้าเว็บไซต์ CU NEX...")
            page.goto(TARGET_URL, timeout=45000)
            page.wait_for_load_state("networkidle")

            # ตรวจสอบการเข้าสู่ระบบ
            if "login" in page.url.lower() or page.locator("input[type='password']").count() > 0:
                print("กำลังเข้าสู่ระบบ...")
                if page.locator("input[name*='user'], input[type='text'], input[type='email']").count() > 0:
                    page.locator("input[name*='user'], input[type='text'], input[type='email']").first.fill(CUNEX_USER)
                if page.locator("input[type='password']").count() > 0:
                    page.locator("input[type='password']").first.fill(CUNEX_PASS)
                
                submit_btn = page.locator("button[type='submit'], input[type='submit'], .btn-login, button:has-text('เข้าสู่ระบบ')")
                if submit_btn.count() > 0:
                    submit_btn.first.click()
                    page.wait_for_load_state("networkidle")
                    time.sleep(3)

                page.goto(TARGET_URL, timeout=45000)
                page.wait_for_load_state("networkidle")

            print("กำลังค้นหาตารางการจอง...")
            page.wait_for_selector("table", timeout=20000)
            time.sleep(2)

            time_slots = [
                "08:00 - 09:00", "09:00 - 10:00", "10:00 - 11:00", "11:00 - 12:00",
                "12:00 - 13:00", "13:00 - 14:00", "14:00 - 15:00", "15:00 - 16:00",
                "16:00 - 17:00", "17:00 - 18:00", "18:00 - 19:00"
            ]

            rows = page.locator("table tr").all()
            print(f"พบแถวทั้งหมดในตาราง {len(rows)} แถว")

            for row in rows:
                cells = row.locator("td").all()
                if len(cells) <= 1:
                    continue

                room_name = cells[0].inner_text().strip()
                if not any(char.isdigit() for char in room_name):
                    continue

                room_slots = {}
                for idx, slot_name in enumerate(time_slots):
                    cell_idx = idx + 1
                    if cell_idx < len(cells):
                        cell = cells[cell_idx]
                        status = parse_cell_status(cell)
                        room_slots[slot_name] = status

                scraped_data["rooms"].append({
                    "room_name": room_name,
                    "floor": "9",
                    "building": "อาคาร 60 ปี",
                    "slots": room_slots
                })

            print(f"ประมวลผลข้อมูลห้องสำเร็จทั้งหมด {len(scraped_data['rooms'])} ห้อง")

        except Exception as e:
            print(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {e}")
        finally:
            browser.close()

    # 1. บันทึกลง data.js
    try:
        with open("data.js", "w", encoding="utf-8") as f:
            f.write(f"window.CUNEX_DATA = {json.dumps(scraped_data, ensure_ascii=False, indent=2)};")
        print("บันทึกข้อมูลลง data.js สำเร็จ")
    except Exception as e:
        print(f"บันทึกลง data.js ล้มเหลว: {e}")

    # 2. ส่งเข้า Google Apps Script
    if GAS_WEBHOOK_URL:
        try:
            print("กำลังส่งข้อมูลเข้า Google Sheets...")
            res = requests.post(GAS_WEBHOOK_URL, json=scraped_data, timeout=20)
            print(f"ผลลัพธ์จาก GAS: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"ส่งข้อมูลไป GAS ล้มเหลว: {e}")

if __name__ == "__main__":
    run_scraper()
