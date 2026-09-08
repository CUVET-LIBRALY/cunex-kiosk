import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

CUNEX_USER = os.environ.get("CUNEX_USER")
CUNEX_PASS = os.environ.get("CUNEX_PASS")
BUILDING_ID = os.environ.get("BUILDING_ID", "3")
GAS_WEBHOOK_URL = os.environ.get("GAS_WEBHOOK_URL")

LOGIN_URL = "https://cunex.chula.ac.th/admin/login"
TARGET_URL = f"https://cunex.chula.ac.th/admin/booking/table?building_id={BUILDING_ID}"

def parse_cell_status(cell):
    try:
        # อ่านค่าสีจริงและโครงสร้างภายใน cell
        info = cell.evaluate("""el => {
            const getBg = (node) => {
                if (!node) return '';
                const st = window.getComputedStyle(node);
                return st.backgroundColor || node.getAttribute('bgcolor') || '';
            };
            const p = el.querySelector('p');
            return {
                tdBg: getBg(el),
                pBg: getBg(p),
                className: (el.className || '') + ' ' + (p ? p.className : ''),
                text: el.innerText || ''
            };
        }""")

        td_bg = info.get('tdBg', '').lower()
        p_bg = info.get('pBg', '').lower()
        combined = f"{td_bg} {p_bg} {info.get('className', '')} {info.get('text', '')}".lower()

        # 1. ดักจับสีเทา / ปิดทำการ
        if any(k in combined for k in ["gray", "grey", "#808080", "#6c757d", "#555", "#666", "#777", "disabled", "closed", "ปิด"]):
            return "closed"

        for bg in [p_bg, td_bg]:
            if "rgb" in bg:
                nums = [int(n.strip()) for n in bg.replace("rgba(", "").replace("rgb(", "").replace(")", "").split(",") if n.strip().isdigit()]
                if len(nums) >= 3:
                    r, g, b = nums[0], nums[1], nums[2]
                    # สีเทา: R, G, B ค่าใกล้กันมาก (ความต่าง <= 20) และไม่ดำสนิทและไม่ขาวสว่าง
                    if abs(r - g) <= 20 and abs(g - b) <= 20 and abs(r - b) <= 20 and 40 <= r <= 210:
                        return "closed"
                    # สีแดง/ชมพู (จองแล้ว)
                    if r > g + 40 and r > b:
                        return "busy"
                    # สีเหลือง (สนใจ)
                    if r > 160 and g > 160 and b < 100:
                        return "pending"
                    # สีเขียว (ว่าง)
                    if g > r + 30 and g > b + 30:
                        return "free"

        if "red" in combined or "#ff0000" in combined:
            return "busy"
        if "yellow" in combined or "#ffff00" in combined:
            return "pending"
        if "green" in combined or "#008000" in combined:
            return "free"

    except Exception as e:
        print(f"Error parsing cell: {e}")

    return "free"


def run_scraper():
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

            rows = page.locator("table tr[id*='MainContentPlaceHolder']").all()
            if not rows:
                rows = page.locator("table tr").all()[1:]

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

            print(f"ประมวลผลข้อมูลสำเร็จ {len(scraped_data['rooms'])} ห้อง")

        except Exception as e:
            print(f"เกิดข้อผิดพลาด: {e}")
        finally:
            browser.close()

    # 1. บันทึกลง data.js สำหรับ Fast Cache
    try:
        with open("data.js", "w", encoding="utf-8") as f:
            f.write(f"window.CUNEX_DATA = {json.dumps(scraped_data, ensure_ascii=False, indent=2)};")
        print("บันทึกข้อมูลลง data.js เรียบร้อย")
    except Exception as e:
        print(f"บันทึก data.js ไม่สำเร็จ: {e}")

    # 2. ส่งข้อมูลเข้า Google Apps Script
    if GAS_WEBHOOK_URL:
        try:
            print("กำลังส่งข้อมูลเข้า Google Sheets...")
            res = requests.post(GAS_WEBHOOK_URL, json=scraped_data, timeout=20)
            print(f"สถานะ GAS: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"ส่งข้อมูล GAS ล้มเหลว: {e}")

if __name__ == "__main__":
    run_scraper()
