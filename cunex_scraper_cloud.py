import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

CUNEX_USER = os.environ.get("CUNEX_USER")
CUNEX_PASS = os.environ.get("CUNEX_PASS")
BUILDING_ID = os.environ.get("BUILDING_ID")
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

        # ตรวจสอบสถานะปิดบริการ (สีเทา)
        gray_keywords = ["gray", "grey", "#808080", "#6c757d", "#555", "#666", "#777", "disabled", "closed", "ปิด"]
        if any(k in combined for k in gray_keywords):
            return "closed"

        for bg in [p_bg, td_bg]:
            if "rgb" in bg:
                nums = [int(n.strip()) for n in bg.replace("rgba(", "").replace("rgb(", "").replace(")", "").split(",") if n.strip().isdigit()]
                if len(nums) >= 3:
                    r, g, b = nums[0], nums[1], nums[2]
                    # สีเทา
                    if abs(r - g) <= 25 and abs(g - b) <= 25 and abs(r - b) <= 25 and 30 <= r <= 220:
                        return "closed"
                    # สีแดง / ชมพู (จองแล้ว / ใช้งาน)
                    if r > g + 40 and r > b:
                        return "busy"
                    # สีเหลือง (สนใจ / รอนุมัติ)
                    if r > 160 and g > 160 and b < 100:
                        return "pending"
                    # สีเขียว (ว่าง)
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
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            print(f"กำลังเปิดเข้าระบบ CU NEX: {LOGIN_URL}")
            page.goto(LOGIN_URL, timeout=45000, wait_until="networkidle")
            time.sleep(2)

            # ขั้นตอนการเข้าสู่ระบบ
            if page.locator("input[type='password']").count() > 0:
                print("พบหน้าเข้าสู่ระบบ กำลังกรอกรหัส...")
                user_input = page.locator("input[type='text'], input[name*='User'], input[name*='user'], input[id*='User']").first
                pass_input = page.locator("input[type='password']").first
                
                user_input.fill(CUNEX_USER or "")
                pass_input.fill(CUNEX_PASS or "")
                
                login_btn = page.locator("input[type='submit'], button[type='submit'], input[value*='เข้าสู่ระบบ'], input[value*='Login']").first
                login_btn.click()
                print("คลิกปุ่มเข้าสู่ระบบแล้ว กำลังรอการยืนยันตัวตน...")
                page.wait_for_load_state("networkidle")
                time.sleep(4)

            # ตรงไปที่หน้าค้นหาห้อง
            print(f"กำลังเปิดหน้าค้นหาห้อง: {TARGET_URL}")
            page.goto(TARGET_URL, timeout=45000, wait_until="networkidle")
            time.sleep(4)
            print(f"อยู่ที่หน้า: {page.url}")

            # ตรวจสอบและเลือก Dropdown ทั้งหมดที่มีในหน้า
            print("กำลังวิเคราะห์ Dropdown ในหน้า...")
            page.wait_for_selector("select", timeout=15000)
            
            # รอดูตัวเลือกใน Dropdown นานขึ้น เผื่อโหลด AJAX
            time.sleep(3)
            
            # ให้ JavaScript ตรวจหาตัวเลือกทั้งหมดและเลือกตึก
            eval_result = page.evaluate("""() => {
                const selects = Array.from(document.querySelectorAll('select'));
                const summary = [];
                let selectedBuilding = false;
                let selectedText = '';

                selects.forEach((sel, selIdx) => {
                    const opts = Array.from(sel.options).map(o => ({ value: o.value, text: (o.text || o.innerText || '').trim() }));
                    summary.push({ selectId: sel.id || sel.name || `select_${selIdx}`, options: opts });

                    // ลองค้นหาตัวเลือกตึก
                    opts.forEach(opt => {
                        const t = opt.text;
                        if (t.includes('60 ปี') || t.includes('สัตวแพทย์') || t.includes('60th') || t.includes('๖๐ ปี')) {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            selectedBuilding = true;
                            selectedText = t;
                        }
                    });
                });

                return { summary, selectedBuilding, selectedText };
            }""")

            print(f"พบ Dropdown ทั้งหมด {len(eval_result.get('summary', []))} ตัว:")
            for item in eval_result.get("summary", []):
                opt_texts = [o["text"] for o in item["options"]]
                print(f" - [{item['selectId']}]: มี {len(opt_texts)} ตัวเลือก ตัวอย่าง: {opt_texts[:5]}")

            if eval_result.get("selectedBuilding"):
                print(f"เลือกตึกสำเร็จ: {eval_result.get('selectedText')}")
                page.wait_for_load_state("networkidle")
                time.sleep(3)
            else:
                print("ยังไม่พบคำว่า 60 ปี ในตัวเลือก กำลังลองใช้ค่า BUILDING_ID หรือตัวเลือกที่มี...")
                if BUILDING_ID:
                    page.evaluate(f"""(bid) => {{
                        const selects = Array.from(document.querySelectorAll('select'));
                        for (let sel of selects) {{
                            for (let opt of Array.from(sel.options)) {{
                                if (opt.value === bid || opt.text.includes(bid)) {{
                                    sel.value = opt.value;
                                    sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                }}
                            }}
                        }}
                    }}""", BUILDING_ID)
                    time.sleep(2)

            # ค้นหาและคลิกปุ่มค้นหา
            print("กำลังคลิกปุ่มค้นหา...")
            search_success = page.evaluate("""() => {
                // ค้นหาปุ่มค้นหาตาม Attribute
                const candidates = Array.from(document.querySelectorAll('input[type="submit"], input[type="button"], button'));
                for (let btn of candidates) {
                    const val = (btn.value || btn.innerText || btn.id || btn.name || '').toLowerCase();
                    if (val.includes('ค้นหา') || val.includes('search') || val.includes('btnsearch')) {
                        btn.click();
                        return { clicked: true, info: btn.outerHTML.substring(0, 100) };
                    }
                }
                // ถ้าไม่พบ ให้ลองคลิกปุ่ม submit ตัวแรก
                if (candidates.length > 0) {
                    candidates[0].click();
                    return { clicked: true, info: 'first_candidate' };
                }
                return { clicked: false };
            }""")

            if search_success.get("clicked"):
                print(f"คลิกปุ่มค้นหาสำเร็จ ({search_success.get('info')}) รอโหลดข้อมูลตาราง 6 วินาที...")
                page.wait_for_load_state("networkidle")
                time.sleep(6)
            else:
                print("ไม่พบปุ่มค้นหา กำลังตรวจแถวข้อมูล...")
                time.sleep(3)

            # อ่านช่วงเวลาทั้ง 11 สล็อต
            time_slots = [
                "08:00 - 09:00", "09:00 - 10:00", "10:00 - 11:00", "11:00 - 12:00",
                "12:00 - 13:00", "13:00 - 14:00", "14:00 - 15:00", "15:00 - 16:00",
                "16:00 - 17:00", "17:00 - 18:00", "18:00 - 19:00"
            ]

            # รอแถวตาราง
            rows = page.locator("tr").all()
            print(f"พบแถวตารางทั้งหมดในหน้า: {len(rows)} แถว")

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
            print(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {e}")
        finally:
            browser.close()

    # 1. เขียน data.js
    try:
        with open("data.js", "w", encoding="utf-8") as f:
            f.write(f"window.CUNEX_DATA = {json.dumps(scraped_data, ensure_ascii=False, indent=2)};")
        print("บันทึก data.js เรียบร้อย")
    except Exception as e:
        print(f"บันทึก data.js ไม่สำเร็จ: {e}")

    # 2. ส่งเข้า Google Sheets
    if GAS_WEBHOOK_URL and len(scraped_data["rooms"]) > 0:
        try:
            print("กำลังส่งข้อมูลเข้า Google Sheets...")
            res = requests.post(GAS_WEBHOOK_URL, json=scraped_data, timeout=25)
            print(f"สถานะ GAS: {res.status_code}")
        except Exception as e:
            print(f"ส่งข้อมูล GAS ล้มเหลว: {e}")
    else:
        print(f"ข้ามการส่งข้อมูลเข้า Google Sheets (จำนวนห้องที่พบ: {len(scraped_data['rooms'])})")

if __name__ == "__main__":
    run_scraper()
