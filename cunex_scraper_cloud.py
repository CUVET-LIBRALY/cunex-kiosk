import os
import json
import re
from datetime import datetime
from playwright.sync_api import sync_playwright
import requests

# ==========================================
# 1. รับค่า Credentials จาก GitHub Secrets
# ==========================================
CUNEX_USER = os.getenv("CUNEX_USER", "bhathaic")
CUNEX_PASS = os.getenv("CUNEX_PASS", "0815611613")
BUILDING_ID = os.getenv("BUILDING_ID", "3")
GAS_WEBHOOK_URL = os.getenv("GAS_WEBHOOK_URL", "https://script.google.com/macros/s/AKfycbzjnkoN0q3jQPkQTMnxmpbn1Tthlg4vSBBY6U68EX60kBHwpl_fHv_6DYMcqAdJ00g7/exec")

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
]

def get_thai_date_now():
    now = datetime.now()
    thai_year = now.year + 543
    thai_month = THAI_MONTHS[now.month]
    return f"{now.day} {thai_month} {thai_year}"

def parse_status_from_rgb(rgb_str):
    if not rgb_str:
        return 'free'
    nums = re.findall(r'\d+', rgb_str)
    if len(nums) < 3:
        return 'free'
    r, g, b = int(nums[0]), int(nums[1]), int(nums[2])

    if r > 200 and g > 200 and b < 100:
        return 'pending'
    if r > 200 and g < 70 and b < 70:
        return 'busy'
    if r > 180 and g > 60 and b > 80 and g < 180:
        return 'busy'
    if g > 100 and r < 120:
        return 'free'
    if abs(r - g) < 25 and abs(g - b) < 25 and r < 200:
        return 'closed'
    return 'free'

def scrape_reservation():
    thai_date_str = get_thai_date_now()
    print(f"[*] เริ่มดึงข้อมูลการจองสำหรับวันที่: {thai_date_str}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            print("[1/5] กำลังเข้าสู่ระบบ...")
            page.goto("https://cunexbackoffice.azurewebsites.net/login.aspx", timeout=60000)
            page.fill("input[name*='txtUserName'], input[type='text']", CUNEX_USER)
            page.fill("input[name*='txtPassword'], input[type='password']", CUNEX_PASS)
            page.click("input[type='submit'], button[type='submit']")
            page.wait_for_load_state("domcontentloaded")

            print("[2/5] กำลังเปิดหน้า SearchReservation...")
            page.goto("https://cunexbackoffice.azurewebsites.net/SearchReservation.aspx", timeout=60000)
            page.wait_for_load_state("domcontentloaded")

            print("[3/5] กรอกวันที่และเลือกอาคาร...")
            page.wait_for_selector("select[name*='ddlBuilding']", timeout=20000)
            page.select_option("select[name*='ddlBuilding']", BUILDING_ID)

            date_input = page.locator("input[name*='txtDateReserv']")
            date_input.fill(thai_date_str)

            print("[4/5] กำลังกดค้นหา...")
            search_btn = page.locator("a[id*='searchLinkButton']")
            search_btn.dispatch_event("click")

            page.wait_for_timeout(5000)
            page.wait_for_load_state("domcontentloaded")

            print("[5/5] กำลังอ่านข้อมูลตาราง...")
            tables = page.locator("table").all()
            target_table = None
            for tbl in tables:
                txt = tbl.inner_text()
                if "08:00" in txt and "09:00" in txt:
                    target_table = tbl
                    break

            rooms_data = []
            if target_table:
                rows = target_table.locator("tr").all()
                header_idx = -1
                for idx, r in enumerate(rows):
                    if "08:00" in r.inner_text():
                        header_idx = idx
                        break

                if header_idx != -1:
                    header_cells = rows[header_idx].locator("th, td").all()
                    time_slots = []
                    for h in header_cells[1:]:
                        t_text = h.inner_text().strip()
                        if t_text:
                            time_slots.append(t_text)

                    for r in rows[header_idx + 1:]:
                        cells = r.locator("td").all()
                        if len(cells) <= len(time_slots):
                            continue

                        raw_name = cells[0].inner_text().strip()
                        if not raw_name or "ตึก" in raw_name or "อาคาร" in raw_name:
                            continue

                        lines = [line.strip() for line in raw_name.splitlines() if line.strip()]
                        floor = ""
                        room_name = raw_name
                        if len(lines) >= 2:
                            floor = lines[0]
                            room_name = " ".join(lines[1:])
                        elif raw_name.startswith("9 "):
                            floor = "9"
                            room_name = raw_name[2:].strip()

                        slots = {}
                        for idx, slot_name in enumerate(time_slots):
                            cell_idx = idx + 1
                            if cell_idx < len(cells):
                                bg_color = cells[cell_idx].evaluate("el => window.getComputedStyle(el).backgroundColor")
                                slots[slot_name] = parse_status_from_rgb(bg_color)

                        rooms_data.append({
                            "floor": floor,
                            "room_name": room_name,
                            "building": "อาคาร 60 ปี",
                            "slots": slots
                        })

            print(f"[+] อ่านข้อมูลสำเร็จ ได้ทั้งหมด {len(rooms_data)} ห้อง")
            
            output_payload = {
                "date": thai_date_str,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "rooms": rooms_data
            }

            if GAS_WEBHOOK_URL:
                res = requests.post(GAS_WEBHOOK_URL, json=output_payload, timeout=20)
                print(f"[✓] ส่งข้อมูลเข้า Google Apps Script สำเร็จ: {res.text}")
            else:
                print("[!] ไม่พบ URL ของ GAS Webhook")

        except Exception as e:
            print(f"[!] เกิดข้อผิดพลาด: {e}")
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    scrape_reservation()