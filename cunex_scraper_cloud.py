import os
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# ==================== CONFIGURATION ====================
LOGIN_URL = "https://cunexbackoffice.azurewebsites.net/login.aspx"
SEARCH_URL = "https://cunexbackoffice.azurewebsites.net/SearchReservation.aspx"

USERNAME = os.environ.get("CUNEX_USER")
PASSWORD = os.environ.get("CUNEX_PASS")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

BUILDING_TEXT = "อาคาร 60 ปี"
# ========================================================

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
]

TIME_SLOTS = [
    "08:00 - 09:00", "09:00 - 10:00", "10:00 - 11:00", "11:00 - 12:00",
    "12:00 - 13:00", "13:00 - 14:00", "14:00 - 15:00", "15:00 - 16:00",
    "16:00 - 17:00", "17:00 - 18:00", "18:00 - 19:00"
]

def get_thai_date_str():
    # คำนวณเวลาไทย (UTC+7)
    utc_now = datetime.now(timezone.utc)
    thai_now = utc_now + timedelta(hours=7)
    thai_year = thai_now.year + 543
    thai_month = THAI_MONTHS[thai_now.month]
    return f"{thai_now.day} {thai_month} {thai_year}"

def parse_color_status(rgb_color):
    if not rgb_color:
        return "free"
    match = re.search(r"rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", rgb_color)
    if not match:
        return "free"
    r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))

    if abs(r - g) < 15 and abs(g - b) < 15 and 80 < r < 200:
        return "closed"
    if r > 180 and g > 180 and b < 100:
        return "pending"
    if (r > 180 and g < 100 and b < 100) or (r > 180 and b > 100 and g < 150):
        return "busy"
    if g > 120 and g > r and g > b:
        return "free"
    return "free"

def run_task():
    date_str = get_thai_date_str()
    print(f"=== กำลังเริ่มเชื่อมต่อ CUNEX Backoffice วันที่ {date_str} ===")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1366, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            print("1. เปิดหน้า Login...")
            page.goto(LOGIN_URL, timeout=45000)
            page.wait_for_load_state("domcontentloaded")

            # ตรวจสอบการ Login
            txt_user = page.locator("input[id*='txtUsername'], input[name*='Username'], input[type='text']").first
            if txt_user.is_visible(timeout=5000):
                print("2. เข้าสู่ระบบ...")
                txt_user.click()
                txt_user.fill(USERNAME)
                txt_pass = page.locator("input[id*='txtPassword'], input[name*='Password'], input[type='password']").first
                txt_pass.click()
                txt_pass.fill(PASSWORD)
                
                btn_login = page.locator("input[id*='btnLogin'], input[value*='เข้าใช้งาน'], input[type='submit']").first
                if btn_login.is_visible(timeout=3000):
                    btn_login.click()
                else:
                    txt_pass.press("Enter")

                page.wait_for_load_state("networkidle", timeout=30000)
                time.sleep(2)

            print("3. เปิดหน้าค้นหาห้อง...")
            page.goto(SEARCH_URL, timeout=45000)
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(2)

            print("4. กำลังเลือกตึก: อาคาร 60 ปี...")
            building_select = page.locator("select").first
            options = building_select.locator("option").all()
            target_val = None
            for opt in options:
                txt = opt.text_content() or ""
                if BUILDING_TEXT in txt:
                    target_val = opt.get_attribute("value")
                    break
            
            if target_val:
                building_select.select_option(value=target_val)
            else:
                building_select.select_option(index=1)

            # รอหน้าเว็บประมวลผล Postback หลังเลือกตึก
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)

            print(f"5. กำลังกรอกวันที่: {date_str}...")
            page.evaluate(f"""() => {{
                let inputs = Array.from(document.querySelectorAll('input[type="text"]'));
                if (inputs.length >= 3) {{
                    inputs[2].value = '{date_str}';
                }} else {{
                    for (let inp of inputs) {{
                        if (inp.id.toLowerCase().includes('date') || inp.name.toLowerCase().includes('date')) {{
                            inp.value = '{date_str}';
                        }}
                    }}
                }}
            }}""")
            time.sleep(1)

            print("6. กำลังกดปุ่มค้นหา...")
            search_btn = page.locator("input[value*='ค้นหา'], button:has-text('ค้นหา'), a:has-text('ค้นหา')").first
            if search_btn.is_visible(timeout=4000):
                search_btn.click()
            else:
                page.evaluate("""() => {
                    let btn = Array.from(document.querySelectorAll('input, button, a')).find(el => (el.value && el.value.includes('ค้นหา')) || (el.innerText && el.innerText.includes('ค้นหา')));
                    if (btn) btn.click();
                }""")

            print("7. กำลังรอการประมวลผลตารางห้อง...")
            page.wait_for_load_state("networkidle", timeout=25000)
            time.sleep(3)

            print("8. กำลังอ่านข้อมูลตารางห้อง...")
            rows = page.locator("tr").all()
            scraped_rooms = []

            for row in rows:
                cells = row.locator("td").all()
                if len(cells) < 12:
                    continue

                full_text = (cells[0].text_content() or "").strip()
                if not any(k in full_text for k in ["905", "906", "907", "908", "927", "928", "929", "930", "931", "932"]):
                    continue

                slot_dict = {}
                for idx, slot in enumerate(TIME_SLOTS):
                    cell_idx = 1 + idx
                    if cell_idx < len(cells):
                        bg_color = cells[cell_idx].evaluate("el => window.getComputedStyle(el).backgroundColor")
                        slot_dict[slot] = parse_color_status(bg_color)
                    else:
                        slot_dict[slot] = "free"

                scraped_rooms.append({
                    "room_name": full_text,
                    "slots": slot_dict
                })

            print(f"ผลลัพธ์: ดึงข้อมูลสำเร็จพบ {len(scraped_rooms)} ห้อง")

            if len(scraped_rooms) > 0:
                # ส่งข้อมูลเข้า Google Sheets Webhook
                utc_now = datetime.now(timezone.utc)
                thai_now = utc_now + timedelta(hours=7)
                payload = {
                    "updated_at": thai_now.strftime("%Y-%m-%d %H:%M:%S"),
                    "rooms": scraped_rooms
                }
                res = requests.post(WEBHOOK_URL, json=payload, timeout=25)
                print(f"ส่งข้อมูลเข้า Google Sheets สำเร็จเรียบร้อย! (Response: {res.status_code})")
            else:
                page.screenshot(path="after_search_not_found.png")
                print("ยังไม่พบข้อมูลห้องในตาราง")

        except Exception as e:
            print(f"เกิดข้อผิดพลาดขณะทำงาน: {e}")
            page.screenshot(path="error.png")
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    run_task()
