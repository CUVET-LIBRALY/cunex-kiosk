import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

CUNEX_USER = os.environ.get("CUNEX_USER")
CUNEX_PASS = os.environ.get("CUNEX_PASS")
GAS_WEBHOOK_URL = os.environ.get("GAS_WEBHOOK_URL")

LOGIN_URL = "https://cunexbackoffice.azurewebsites.net/Login.aspx"
TARGET_URL = "https://cunexbackoffice.azurewebsites.net/SearchReservation.aspx"

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

        # ปิดบริการ (สีเทา)
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

        if "red" in combined or "#ff0000" in combined or "pink" in combined:
            return "busy"
        if "yellow" in combined or "#ffff00" in combined:
            return "pending"
        if "green" in combined or "#008000" in combined:
            return "free"

    except Exception:
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
        # ปรับ User-Agent และพารามิเตอร์เบราว์เซอร์ให้เหมือนผู้ใช้ทั่วไป
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="th-TH",
            timezone_id="Asia/Bangkok"
        )
        page = context.new_page()

        try:
            print(f"กำลังเปิดเข้าระบบ CU NEX: {LOGIN_URL}")
            page.goto(LOGIN_URL, timeout=45000, wait_until="domcontentloaded")
            time.sleep(2)

            # ตรวจสอบฟอร์มเข้าสู่ระบบ
            if page.locator("input[type='password']").count() > 0:
                print("พบหน้าเข้าสู่ระบบ กำลังกรอกรหัส...")
                page.locator("input[type='text'], input[name*='User'], input[id*='User']").first.fill(CUNEX_USER or "")
                page.locator("input[type='password']").first.fill(CUNEX_PASS or "")
                page.locator("input[type='submit'], button[type='submit'], input[value*='เข้าสู่ระบบ']").first.click()
                print("คลิกปุ่มเข้าสู่ระบบแล้ว กำลังรอ Session...")
                time.sleep(4)

            # เข้าสู่หน้าค้นหาตารางการจอง
            print(f"กำลังเปิดหน้าค้นหาห้อง: {TARGET_URL}")
            page.goto(TARGET_URL, timeout=45000, wait_until="domcontentloaded")
            time.sleep(3)

            # 1. เลือกตึก อาคาร 60 ปี
            print("กำลังเลือกตึก อาคาร 60 ปี...")
            page.wait_for_selector("#MainContentPlaceHolder_ddlBuilding", timeout=15000)
            page.select_option("#MainContentPlaceHolder_ddlBuilding", value="3")
            time.sleep(2)

            # 2. กระตุ้น ASP.NET PostBack ตรงไปยัง Event ของปุ่มค้นหา
            print("กำลังส่งสัญญาณ ASP.NET PostBack เพื่อค้นหา...")
            postback_result = page.evaluate("""() => {
                // ตรวจสอบ Event Target ของปุ่ม searchLinkButton
                const linkBtn = document.getElementById('MainContentPlaceHolder_searchLinkButton');
                if (linkBtn) {
                    const href = linkBtn.getAttribute('href');
                    // กรณี href มีคำสั่ง javascript:__doPostBack
                    if (href && href.includes('__doPostBack')) {
                        eval(href.replace('javascript:', ''));
                        return 'Executed href __doPostBack';
                    }
                    linkBtn.click();
                    return 'Executed linkBtn.click()';
                }
                
                // สั่งผ่านฟังก์ชันสากลของ ASP.NET
                if (typeof __doPostBack === 'function') {
                    __doPostBack('ctl00$MainContentPlaceHolder$searchLinkButton', '');
                    return 'Called __doPostBack manually';
                }
                
                return 'No ASP.NET PostBack handler found';
            }""")
            print(f"สถานะการส่งคำสั่งค้นหา: {postback_result}")

            # 3. กำหนดเวลารอตารางเรนเดอร์อย่างแน่นอน (ASP.NET DataBind ใช้เวลา 5-7 วินาที)
            print("กำลังรอการเรนเดอร์ตารางผลลัพธ์...")
            time.sleep(7)

            time_slots = [
                "08:00 - 09:00", "09:00 - 10:00", "10:00 - 11:00", "11:00 - 12:00",
                "12:00 - 13:00", "13:00 - 14:00", "14:00 - 15:00", "15:00 - 16:00",
                "16:00 - 17:00", "17:00 - 18:00", "18:00 - 19:00"
            ]

            # ตรวจสอบแถวตารางทั้งหมด
            rows = page.locator("tr").all()
            print(f"พบแถว tr ทั้งหมด: {len(rows)} แถว")

            for row in rows:
                cells = row.locator("td").all()
                if len(cells) < 10:
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

            print(f"ดึงข้อมูลสำเร็จทั้งหมด {len(scraped_data['rooms'])} ห้อง")

        except Exception as e:
            print(f"เกิดข้อผิดพลาดในการทำงาน: {e}")
        finally:
            browser.close()

    # บันทึก data.js
    try:
        with open("data.js", "w", encoding="utf-8") as f:
            f.write(f"window.CUNEX_DATA = {json.dumps(scraped_data, ensure_ascii=False, indent=2)};")
        print("บันทึก data.js เรียบร้อย")
    except Exception as e:
        print(f"บันทึก data.js ไม่สำเร็จ: {e}")

    # ส่งเข้า Google Sheets Webhook
    if GAS_WEBHOOK_URL and len(scraped_data["rooms"]) > 0:
        try:
            print("กำลังส่งข้อมูลเข้า Google Sheets...")
            res = requests.post(GAS_WEBHOOK_URL, json=scraped_data, timeout=25)
            print(f"สถานะ GAS Webhook: {res.status_code}")
        except Exception as e:
            print(f"ส่งข้อมูล GAS ล้มเหลว: {e}")
    else:
        print(f"ข้ามการส่งข้อมูลเข้า Google Sheets (จำนวนห้องที่พบ: {len(scraped_data['rooms'])})")

if __name__ == "__main__":
    run_scraper()
