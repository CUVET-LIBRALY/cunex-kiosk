import os
import json
import time
import requests
from datetime import datetime
import pytz
from playwright.sync_api import sync_playwright

# อ่านค่าคอนฟิกจาก Environment Variables (GitHub Secrets)
CUNEX_USER = os.environ.get("CUNEX_USER")
CUNEX_PASS = os.environ.get("CUNEX_PASS")
BUILDING_ID = os.environ.get("BUILDING_ID", "3")
GAS_WEBHOOK_URL = os.environ.get("GAS_WEBHOOK_URL")

TARGET_URL = f"https://cunex.chula.ac.th/admin/booking/table?building_id={BUILDING_ID}"

def parse_cell_status(cell):
    """
    ฟังก์ชันตรวจสอบสถานะจาก Cell (td) โดยตรง
    อ่านค่าสีที่แท้จริงจาก Browser Rendered Style ทั้ง td และ element ภายใน
    """
    try:
        # อ่านค่าสีพื้นหลังที่แท้จริงที่แสดงผลบนหน้าจอ
        colors = cell.evaluate("""
            el => {
                const getEffectiveBg = (target) => {
                    let cur = target;
                    while (cur && cur !== document.body) {
                        const style = window.getComputedStyle(cur);
                        const bg = style.backgroundColor;
                        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
                            return bg;
                        }
                        const attrBg = cur.getAttribute('bgcolor');
                        if (attrBg) return attrBg;
                        cur = cur.parentElement;
                    }
                    return '';
                };

                const tdBg = window.getComputedStyle(el).backgroundColor;
                const pBg = el.querySelector('p') ? window.getComputedStyle(el.querySelector('p')).backgroundColor : '';
                const effBg = getEffectiveBg(el);
                const classStr = el.className + ' ' + (el.querySelector('p') ? el.querySelector('p').className : '');
                const styleStr = (el.getAttribute('style') || '') + ' ' + (el.getAttribute('bgcolor') || '');

                return {
                    tdBg: tdBg || '',
                    pBg: pBg || '',
                    effBg: effBg || '',
                    classStr: classStr.toLowerCase(),
                    styleStr: styleStr.toLowerCase()
                };
            }
        """)

        combined_text = f"{colors['tdBg']} {colors['pBg']} {colors['effBg']} {colors['classStr']} {colors['styleStr']}".lower()

        # 1. ตรวจสอบเงื่อนไขสีเทา (ปิดทำการ)
        gray_keywords = ["gray", "grey", "#808080", "#6c757d", "#555", "#666", "#777", "#888", "#999", "#aaa", "#4a4a4a", "#343a40", "disabled", "closed", "lock"]
        if any(k in combined_text for k in gray_keywords):
            return "closed"

        # ตรวจสอบค่า RGB ทุกตัวที่ดึงมาได้
        for bg in [colors['tdBg'], colors['pBg'], colors['effBg']]:
            if "rgb" in bg:
                nums = [int(n.strip()) for n in bg.replace("rgba(", "").replace("rgb(", "").replace(")", "").split(",") if n.strip().isdigit()]
                if len(nums) >= 3:
                    r, g, b = nums[0], nums[1], nums[2]
                    # สีเทา: R, G, B ค่าใกล้เคียงกันมาก (ความต่างไม่เกิน 25) และไม่ใช่สีขาวสว่าง
                    if abs(r - g) <= 25 and abs(g - b) <= 25 and abs(r - b) <= 25 and 30 <= r <= 220:
                        return "closed"
                    
                    # สีแดง / ชมพู (จองแล้ว)
                    if r > g + 40 and r > b:
                        return "busy"

                    # สีเหลือง (สนใจ/รออนุมัติ)
                    if r > 160 and g > 160 and b < 100:
                        return "pending"

                    # สีเขียว (ว่าง)
                    if g > r + 30 and g > b + 30:
                        return "free"

        # ตรวจสอบจากชื่อสีพื้นฐาน
        if any(c in combined_text for c in ["red", "#ff0000", "#dc3545"]):
            return "busy"
        if any(c in combined_text for c in ["yellow", "#ffff00", "#ffc107"]):
            return "pending"
        if any(c in combined_text for c in ["green", "#008000", "#28a745", "#22c55e"]):
            return "free"

    except Exception as e:
        print(f"Error checking cell: {e}")

    return "free"


def run_scraper():
    tz = pytz.timezone("Asia/Bangkok")
    now_th = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
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
                print("พบหน้าเข้าสู่ระบบ กำลังล็อกอิน...")
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

            print("กำลังค้นหาตาราง...")
            page.wait_for_selector("table", timeout=20000)
            time.sleep(2)

            # กำหนดช่วงเวลามาตรฐาน
            time_slots = [
                "08:00 - 09:00", "09:00 - 10:00", "10:00 - 11:00", "11:00 - 12:00",
                "12:00 - 13:00", "13:00 - 14:00", "14:00 - 15:00", "15:00 - 16:00",
                "16:00 - 17:00", "17:00 - 18:00", "18:00 - 19:00"
            ]

            # อ่านแถวห้องประชุม (มองหา tr ที่มี id MainContentPlaceHolder)
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
                # เริ่มอ่าน cell ที่ 1 เป็นต้นไป (cell 0 คือชื่อห้อง)
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

            print(f"ดึงข้อมูลเรียบร้อยทั้งหมด {len(scraped_data['rooms'])} ห้อง")

        except Exception as e:
            print(f"เกิดข้อผิดพลาดขณะ Scrape: {e}")
        finally:
            browser.close()

    # บันทึก data.js สำหรับ Fast Cache
    try:
        with open("data.js", "w", encoding="utf-8") as f:
            f.write(f"window.CUNEX_DATA = {json.dumps(scraped_data, ensure_ascii=False, indent=2)};")
        print("บันทึกข้อมูลลง data.js สำเร็จ")
    except Exception as e:
        print(f"บันทึก data.js ล้มเหลว: {e}")

    # ส่งเข้า Google Apps Script
    if GAS_WEBHOOK_URL:
        try:
            print("กำลังส่งข้อมูลเข้า Google Sheets...")
            res = requests.post(GAS_WEBHOOK_URL, json=scraped_data, timeout=20)
            print(f"สถานะส่งข้อมูล GAS: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"ส่งข้อมูล GAS ล้มเหลว: {e}")

if __name__ == "__main__":
    run_scraper()
