import os
import re
import datetime
import requests
from playwright.sync_api import sync_playwright

# อ่านค่า Secret จาก GitHub Environment Variables
USER = os.environ.get("CUNEX_USER")
PASS = os.environ.get("CUNEX_PASS")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# พจนานุกรมแปลงชื่อเดือนภาษาไทย
THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
]

def get_thai_date_str():
    """สร้างสตริงวันที่ภาษาไทย เช่น '10 กันยายน 2569'"""
    # ปรับ Timezone เป็นประเทศไทย (UTC+7)
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    thai_now = utc_now + datetime.timedelta(hours=7)
    day = thai_now.day
    month = THAI_MONTHS[thai_now.month]
    year = thai_now.year + 543
    return f"{day} {month} {year}"

def parse_slot_status(cell_style):
    """แปลงสีพื้นหลังของแต่ละช่วงเวลาเป็นสถานะ"""
    style_lower = (cell_style or "").lower()
    if "#9e9e9e" in style_lower or "rgb(158, 158, 158)" in style_lower or "grey" in style_lower or "gray" in style_lower:
        return "closed"
    elif "#f06292" in style_lower or "rgb(240, 98, 146)" in style_lower or "pink" in style_lower:
        return "busy"
    elif "#4caf50" in style_lower or "rgb(76, 175, 80)" in style_lower or "green" in style_lower:
        return "free"
    elif "#ffeb3b" in style_lower or "rgb(255, 235, 59)" in style_lower or "yellow" in style_lower:
        return "pending"
    return "free"

def run():
    print("=== เริ่มการทำงานดึงข้อมูล CUNEX ผ่าน GitHub Actions ===")
    thai_date = get_thai_date_str()
    print(f"วันที่ค้นหา: {thai_date}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # 1. เข้าหน้า Login
        print("1. เปิดหน้า Login...")
        page.goto("https://cunexbackoffice.azurewebsites.net/", timeout=60000)

        # 2. เข้าสู่ระบบ
        print("2. เข้าสู่ระบบ...")
        page.locator('input[type="text"], input[name*="user"], input[id*="user"]').first.fill(USER)
        page.locator('input[type="password"]').first.fill(PASS)
        page.locator('button[type="submit"], input[type="submit"]').first.click()
        page.wait_for_load_state("networkidle")

        # 3. นำทางไปหน้าค้นหาห้อง
        print("3. นำทางไปหน้าค้นหาการจอง...")
        page.goto("https://cunexbackoffice.azurewebsites.net/Booking/SearchRoom", timeout=60000)
        page.wait_for_load_state("networkidle")

        # 4. เลือกตึก อาคาร 60 ปี
        print("4. กำลังเลือกตึก: อาคาร 60 ปี...")
        building_select = page.locator('select').first
        building_select.select_option(label="อาคาร 60 ปี (สำหรับนิสิตคณะสัตวแพทยศาสตร์)")
        page.wait_for_timeout(1000)

        # 5. กรอกวันที่ปัจจุบัน
        print(f"5. กำลังกรอกวันที่: {thai_date}...")
        date_input = page.locator('input[type="text"]').nth(0)
        date_input.fill("")
        date_input.fill(thai_date)

        # 6. กดปุ่มค้นหา
        print("6. กำลังกดปุ่มค้นหา...")
        search_btn = page.locator('button:has-text("ค้นหา"), input[value="ค้นหา"]').first
        search_btn.click()

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
                    slots = []
                    for c in cols[1:12]:
                        style = c.get_attribute("style") or ""
                        slots.append(parse_slot_status(style))
                    rooms_data.append({
                        "name": room_name,
                        "slots": slots
                    })

        # แทรกห้อง 906 (ปิดปรับปรุงถาวร)
        room_906_exists = any("906" in r["name"] for r in rooms_data)
        if not room_906_exists:
            rooms_data.insert(1, {
                "name": "ห้อง 906 (ปรับปรุง)",
                "slots": ["closed"] * 11
            })

        print(f"ผลลัพธ์: ดึงข้อมูลสำเร็จพบ {len(rooms_data)} ห้อง")

        # 9. ส่งข้อมูลเข้า Google Sheets
        if WEBHOOK_URL:
            payload = {
                "date": thai_date,
                "rooms": rooms_data
            }
            res = requests.post(WEBHOOK_URL, json=payload, timeout=30)
            print(f"ส่งข้อมูลเข้า Google Sheets สำเร็จเรียบร้อย! (Response: {res.status_code})")
        else:
            print("คำเตือน: ไม่พบ WEBHOOK_URL ใน Secrets")

        browser.close()

if __name__ == "__main__":
    run()
