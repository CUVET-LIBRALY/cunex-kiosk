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

TARGET_URL = f"https://cunex.chula.ac.th/admin/booking/table?building_id={BUILDING_ID}"

def parse_cell_status(cell):
    try:
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

        # ตรวจสอบสีเทา / ปิดทำการ
        gray_keywords = ["gray", "grey", "#808080", "#6c757d", "#555", "#666", "#777", "disabled", "closed", "ปิด"]
        if any(k in combined for k in gray_keywords):
            return "closed"

        for bg in [p_bg, td_bg]:
            if "rgb" in bg:
                nums = [int(n.strip()) for n in bg.replace("rgba(", "").replace("rgb(", "").replace(")", "").split(",") if n.strip().isdigit()]
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

        if "red" in combined or "#ff0000" in combined:
            return "busy"
        if "yellow" in combined or "#ffff00" in combined:
            return "pending"
        if "green" in combined or "#008000" in combined:
            return "free"

    except Exception as e:
        pass

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
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            print(f"กำลังเปิด: {TARGET_URL}")
            page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            time.sleep(3)

            # ตรวจสอบการเข้าสู่ระบบ
            if "login" in page.url.lower() or page.locator("input[type='password']").count() > 0:
                print(f"พบหน้าล็อกอิน URL: {page.url}")
                if page.locator("input[name*='user'], input[type='text'], input[type='email']").count() > 0:
                    page.locator("input[name*='user'], input[type='text'], input[type='email']").first.fill(CUNEX_USER or "")
                if page.locator("input[type='password']").count() > 0:
                    page.locator("input[type='password']").first.fill(CUNEX_PASS or "")
                
                submit_btn = page.locator("button[type='submit'], input[type='submit'], .btn-login, button:has-text('เข้าสู่ระบบ')")
                if submit_btn.count() > 0:
                    submit_btn.first.click()
                    print("กดปุ่ม Submit แล้ว รอ 5 วินาที...")
                    time.sleep(5)

                page.screenshot(path="after_login.png")
                page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
                time.sleep(4)

            # ถ่ายภาพหน้าตารางปัจจุบัน
            page.screenshot(path="table_page.png")
            print(f"URL ปัจจุบันที่พร้อมดึงข้อมูล: {page.url}")

            time_slots = [
                "08:00 - 09:00", "09:00 - 10:00", "10:00 - 11:00", "11:00 - 12:00",
                "12:00 - 13:00", "13:00 - 14:00", "14:00 - 15:00", "15:00 - 16:00",
                "16:00 - 17:00", "17:00 - 18:00", "18:00 - 19:00"
            ]

            rows = page.locator("table tr").all()
            print(f"จำนวนแถวในตารางที่สแกนเจอ: {len(rows)}")

            for row in rows:
                cells = row.locator("td").all()
                if len(cells) <= 1:
                    continue

                room_name = cells[0].inner_text().strip()
                if not any(char.isdigit() for char in room_name) or ("ชั้น" in room_name and len(room_name) < 5):
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

            print(f"ประมวลผลห้องประชุมได้: {len(scraped_data['rooms'])} ห้อง")

        except Exception as e:
            print(f"เกิดข้อผิดพลาด: {e}")
            try:
                page.screenshot(path="table_page.png")
            except Exception:
                pass
        finally:
            browser.close()

    # 1. บันทึกลง data.js
    try:
        with open("data.js", "w", encoding="utf-8") as f:
            f.write(f"window.CUNEX_DATA = {json.dumps(scraped_data, ensure_ascii=False, indent=2)};")
        print("บันทึก data.js เรียบร้อย")
    except Exception as e:
        print(f"บันทึก data.js ไม่สำเร็จ: {e}")

    # 2. ส่งเข้า Google Apps Script
    if GAS_WEBHOOK_URL:
        try:
            print("กำลังส่งข้อมูลเข้า Google Sheets...")
            res = requests.post(GAS_WEBHOOK_URL, json=scraped_data, timeout=20)
            print(f"สถานะ GAS: {res.status_code}")
        except Exception as e:
            print(f"ส่งข้อมูล GAS ล้มเหลว: {e}")

if __name__ == "__main__":
    run_scraper()
