import os
import re
import datetime
import requests
from playwright.sync_api import sync_playwright

USER = os.environ.get("CUNEX_USER")
PASS = os.environ.get("CUNEX_PASS")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
]

def get_thai_date_str():
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    thai_now = utc_now + datetime.timedelta(hours=7)
    day = thai_now.day
    month = THAI_MONTHS[thai_now.month]
    year = thai_now.year + 543
    return f"{day} {month} {year}"

def parse_slot_status(cell_style):
    style_lower = (cell_style or "").lower()
    if any(c in style_lower for c in ["#9e9e9e", "rgb(158, 158, 158)", "grey", "gray"]):
        return "closed"
    elif any(c in style_lower for c in ["#f06292", "rgb(240, 98, 146)", "pink"]):
        return "busy"
    elif any(c in style_lower for c in ["#4caf50", "rgb(76, 175, 80)", "green"]):
        return "free"
    elif any(c in style_lower for c in ["#ffeb3b", "rgb(255, 235, 59)", "yellow"]):
        return "pending"
    return "free"

def run():
    print("=== เริ่มการทำงานดึงข้อมูล CUNEX ผ่าน GitHub Actions ===")
    thai_date = get_thai_date_str()
    print(f"วันที่ค้นหา: {thai_date}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="th-TH",
            timezone_id="Asia/Bangkok"
        )
        page = context.new_page()

        try:
            # 1. เข้าหน้า Login
            print("1. เปิดหน้า Login...")
            page.goto("https://cunexbackoffice.azurewebsites.net/", timeout=60000, wait_until="networkidle")

            # 2. เข้าสู่ระบบ
            print("2. เข้าสู่ระบบ...")
            page.locator('input[type="text"]').first.fill(USER)
            page.locator('input[type="password"]').first.fill(PASS)
            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)

            # ถ่ายภาพหน้าจอหลังล็อกอินเพื่อตรวจเช็ค
            page.screenshot(path="after_login.png")
            print(f"URL ปัจจุบัน: {page.url}")

            # 3. นำทางไปหน้าค้นหาห้อง
            print("3. นำทางไปหน้าค้นหาการจอง...")
            menu_btn = page.locator('a:has-text("ค้นหาห้อง"), a[href*="SearchRoom"]')
            if menu_btn.count() > 0:
                print("พบคลิกจากเมนู...")
                menu_btn.first.click()
            else:
                print("เปิด URL ตรง...")
                page.goto("https://cunexbackoffice.azurewebsites.net/Booking/SearchRoom", timeout=60000)
            
            page.wait_for_timeout(5000)
            page.screenshot(path="search_page.png")
            print(f"URL หน้าค้นหา: {page.url}")

            # 4. เลือกตึก อาคาร 60 ปี
            print("4. กำลังเลือกตึก: อาคาร 60 ปี...")
            page.wait_for_selector('select', timeout=20000)
            select_box = page.locator('select').first
            select_box.select_option(label="อาคาร 60 ปี (สำหรับนิสิตคณะสัตวแพทยศาสตร์)")
            page.wait_for_timeout(1000)

            # 5. กรอกวันที่ปัจจุบัน
            print(f"5. กำลังกรอกวันที่: {thai_date}...")
            date_input = page.locator('input[type="text"]').first
            date_input.fill("")
            date_input.fill(thai_date)

            # 6. กดปุ่มค้นหา
            print("6. กำลังกดปุ่มค้นหา...")
            page.locator('button:has-text("ค้นหา"), input[value="ค้นหา"]').first.click()

            # 7. รอผลลัพธ์
            print("7. กำลังรอการประมวลผลตารางห้อง...")
            page.wait_for_selector('table', timeout=30000)
            page.wait_for_timeout(2000)

            # 8. อ่านข้อมูลตาราง
            print("8. กำลังอ่านข้อมูลตารางห้อง...")
            rows = page.locator('table tr').all()
            rooms_data = []

            for row in rows:
                text = row.inner_text()
                if "9 ห้อง" in text:
                    cols = row.locator('td').all()
                    if len(cols) >= 12:
                        room_name = cols[0].inner_text().strip()
                        slots = [parse_slot_status(c.get_attribute("style")) for c in cols[1:12]]
                        rooms_data.append({"name": room_name, "slots": slots})

            # แทรกห้อง 906 (ปรับปรุง)
            if not any("906" in r["name"] for r in rooms_data):
                rooms_data.insert(1, {"name": "ห้อง 906 (ปรับปรุง)", "slots": ["closed"] * 11})

            print(f"ผลลัพธ์: ดึงข้อมูลสำเร็จพบ {len(rooms_data)} ห้อง")

            # 9. ส่งข้อมูลเข้า Google Sheets
            if WEBHOOK_URL:
                res = requests.post(WEBHOOK_URL, json={"date": thai_date, "rooms": rooms_data}, timeout=30)
                print(f"ส่งข้อมูลเข้า Google Sheets สำเร็จเรียบร้อย! (Response: {res.status_code})")

        except Exception as e:
            print(f"เกิดข้อผิดพลาด: {e}")
            page.screenshot(path="error.png")
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    run()
