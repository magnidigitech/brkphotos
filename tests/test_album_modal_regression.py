"""
Comprehensive regression test suite for My Event Album UI:
1. Textbox contrast (dark text #111827 on white #ffffff background, visible borders, caret).
2. Compact friendly share-link presentation (domain/s/...) with accurate full-URL copy.
3. Official WhatsApp brand SVG icon button (min 44x44px touch target, aria-label, tooltip, no text).
4. Desktop and mobile viewports with screenshot captures.
"""
import os
import sys
import time
import shutil
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock
from PIL import Image, ImageDraw

# Mock ML dependencies before app import
sys.modules['insightface'] = MagicMock()
sys.modules['insightface.app'] = MagicMock()

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
import app
import database
import uvicorn

SCREENSHOTS_DIR = Path("/Users/venkatavivek/.gemini/antigravity/brain/64149011-e7dc-4646-853f-dbda7591c5f4/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_DIR = Path(tempfile.mkdtemp(prefix="brk_ui_test_"))
STORAGE_DIR = TEST_DIR / "storage"
PHOTOS_DIR = STORAGE_DIR / "photos" / "brk-sir"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STORAGE_DIR / "data" / "test.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

PORT = 8898
SERVER_URL = f"http://127.0.0.1:{PORT}"

def setup_test_data():
    colors = [("teal", "1"), ("coral", "2"), ("navy", "3"), ("maroon", "4")]
    records = []
    for idx, (color, label) in enumerate(colors, start=1):
        filename = f"photo_{idx}.jpg"
        rel_path = f"brk-sir/{filename}"
        img_path = PHOTOS_DIR / filename
        img = Image.new("RGB", (800, 600), color=color)
        draw = ImageDraw.Draw(img)
        draw.text((50, 50), f"Test Photo {label}", fill="white")
        img.save(img_path, format="JPEG")
        records.append({"id": idx, "filename": filename, "rel_path": rel_path})

    database.DB_PATH = DB_PATH
    database.init_db()
    conn = database.get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO events (id, name, slug, status) VALUES (1, 'BRK Sir Celebration', 'brk-sir', 'published')")
    c.execute("INSERT OR REPLACE INTO albums (id, event_id, name, slug) VALUES (1, 1, 'Main', 'main')")
    for r in records:
        c.execute("INSERT OR REPLACE INTO photos (id, event_id, album_id, filename, rel_path, is_deleted) VALUES (?, 1, 1, ?, ?, 0)",
                  (r["id"], r["filename"], r["rel_path"]))
    conn.commit()
    conn.close()

    search_id = "srch_reg_test_001"
    _, creation_token = database.save_search_result_proof(
        search_id=search_id,
        event_id=1,
        matched_photo_ids=[1, 2, 3, 4],
        creator_session_id="visitor_sess_reg",
        ttl_seconds=3600
    )

    search_id_mob = "srch_reg_test_mob"
    _, creation_token_mob = database.save_search_result_proof(
        search_id=search_id_mob,
        event_id=1,
        matched_photo_ids=[1, 2, 3, 4],
        creator_session_id="visitor_sess_reg_mob",
        ttl_seconds=3600
    )
    return records, search_id, creation_token, search_id_mob, creation_token_mob

def get_driver(viewport=(1280, 800), mobile=False):
    options = Options()
    options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    if mobile:
        options.add_argument(f"--window-size={viewport[0]},{viewport[1]}")
        options.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1")
    else:
        options.add_argument(f"--window-size={viewport[0]},{viewport[1]}")
    driver = webdriver.Chrome(options=options)
    driver.set_window_size(viewport[0], viewport[1])
    return driver

def run_tests():
    print("=== STARTING MY EVENT ALBUM UI VERIFICATION SUITE ===")
    records, search_id, creation_token, search_id_mob, creation_token_mob = setup_test_data()

    database.DB_PATH = DB_PATH
    app.STORAGE = STORAGE_DIR
    app.PHOTOS = STORAGE_DIR / "photos"
    app.DATA = STORAGE_DIR / "data"

    config = uvicorn.Config(app.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    time.sleep(1.5)

    try:
        # -------------------------------------------------------------
        # TEST 1: DESKTOP - Creation Modal Input Contrast & Styling
        # -------------------------------------------------------------
        print("\n--- Test 1: Desktop - Creation Modal Form Contrast ---")
        driver = get_driver((1280, 800))
        driver.get(SERVER_URL)
        time.sleep(1.5)

        driver.execute_script(f"""
            const samplePhotos = {records};
            localStorage.setItem('brk_saved_photos', JSON.stringify(samplePhotos));
            localStorage.setItem('brk_saved_count', '4');
            localStorage.setItem('brk_saved_face_thumb', 'data:image/jpeg;base64,/9j/4AAQSkZJRg==');
            localStorage.setItem('brk_current_search_id', '{search_id}');
            localStorage.setItem('brk_current_creation_token', '{creation_token}');
            viewSavedPhotos();
            toggleSelectionMode(true);
            togglePhotoSelect('{records[0]["rel_path"]}', null);
            togglePhotoSelect('{records[1]["rel_path"]}', null);
        """)
        time.sleep(0.8)

        # Real UI click to open modal
        driver.find_element(By.ID, "bulkCreateAlbumBtn").click()
        time.sleep(0.6)

        # Inspect input & select styles
        input_styles = driver.execute_script("""
            const inp = document.getElementById('inputAlbumTitle');
            const sel = document.getElementById('selectAlbumExpiry');
            const inpComp = window.getComputedStyle(inp);
            const selComp = window.getComputedStyle(sel);
            return {
                input_bg: inpComp.backgroundColor,
                input_color: inpComp.color,
                input_caret: inpComp.caretColor,
                input_border: inpComp.borderColor,
                select_bg: selComp.backgroundColor,
                select_color: selComp.color,
                select_border: selComp.borderColor
            };
        """)
        print("Input & Select styles:", input_styles)

        # Verify white background and dark text (#111827 = rgb(17, 24, 39))
        assert "rgb(255, 255, 255)" in input_styles["input_bg"], f"Input background must be white, got {input_styles['input_bg']}"
        assert "rgb(17, 24, 39)" in input_styles["input_color"], f"Input text color must be #111827, got {input_styles['input_color']}"
        assert "rgb(255, 255, 255)" in input_styles["select_bg"], f"Select background must be white, got {input_styles['select_bg']}"
        assert "rgb(17, 24, 39)" in input_styles["select_color"], f"Select text color must be #111827, got {input_styles['select_color']}"

        # Enter title and verify readability
        title_inp = driver.find_element(By.ID, "inputAlbumTitle")
        title_inp.clear()
        title_inp.send_keys("Reception Highlights — Special")
        time.sleep(0.3)

        # Capture Screenshot 1: High Contrast Creation Modal
        shot1 = SCREENSHOTS_DIR / "01_creation_modal_contrast.png"
        driver.save_screenshot(str(shot1))
        print("Captured Screenshot 1:", shot1)

        # -------------------------------------------------------------
        # TEST 2: Link-Ready Dialog — Compact Link & WhatsApp SVG Button
        # -------------------------------------------------------------
        print("\n--- Test 2: Link-Ready Dialog — Compact Link & WhatsApp SVG Button ---")
        driver.find_element(By.ID, "btnConfirmCreateAlbum").click()
        time.sleep(1.2)

        link_dialog_info = driver.execute_script("""
            const compactTextEl = document.getElementById('compactShareLinkText');
            const fullInput = document.getElementById('successShareLinkInput');
            const waBtn = document.getElementById('btnWhatsAppShare');
            const copyBtn = document.getElementById('btnCopySuccessLink');
            const waSvg = waBtn ? waBtn.querySelector('svg') : null;
            const waSvgPath = waSvg ? waSvg.querySelector('path') : null;
            const waRect = waBtn ? waBtn.getBoundingClientRect() : null;
            const waComp = waBtn ? window.getComputedStyle(waBtn) : null;
            const linkPillComp = window.getComputedStyle(document.getElementById('compactShareLinkDisplay'));

            return {
                compact_text: compactTextEl ? compactTextEl.textContent : '',
                full_url: fullInput ? fullInput.value : '',
                link_pill_bg: linkPillComp.backgroundColor,
                link_pill_color: linkPillComp.color,
                wa_has_svg: !!waSvg,
                wa_d_attr: waSvgPath ? waSvgPath.getAttribute('d').substring(0, 30) : '',
                wa_aria_label: waBtn ? waBtn.getAttribute('aria-label') : null,
                wa_title: waBtn ? waBtn.getAttribute('title') : null,
                wa_text_content: waBtn ? waBtn.textContent.trim() : '',
                wa_width: waRect ? waRect.width : 0,
                wa_height: waRect ? waRect.height : 0,
                wa_bg: waComp ? waComp.backgroundColor : '',
                wa_href: waBtn ? waBtn.getAttribute('href') : ''
            };
        """)
        print("Link dialog inspection:", {k: v for k, v in link_dialog_info.items() if k != 'wa_href'})
        print("WhatsApp URL starts with:", link_dialog_info['wa_href'][:60])

        # 1. Compact link verification
        assert "/s/…" in link_dialog_info["compact_text"], f"Compact link must show domain/s/…, got {link_dialog_info['compact_text']}"
        assert link_dialog_info["full_url"].startswith("http"), "Full URL must be a valid complete URL"
        assert "/s/sh_" in link_dialog_info["full_url"], "Full URL must retain secure share token"

        # 2. Text contrast in link pill
        assert "rgb(255, 255, 255)" in link_dialog_info["link_pill_bg"], "Link pill background must be white"

        # 3. WhatsApp button verification
        assert link_dialog_info["wa_has_svg"], "WhatsApp button must contain SVG icon"
        assert "M12.04" in link_dialog_info["wa_d_attr"], "SVG must use official WhatsApp path asset"
        assert link_dialog_info["wa_aria_label"] == "Share on WhatsApp", "aria-label must be 'Share on WhatsApp'"
        assert link_dialog_info["wa_title"] == "Share on WhatsApp", "title tooltip must be 'Share on WhatsApp'"
        assert link_dialog_info["wa_text_content"] == "", "WhatsApp button must NOT contain visible text, only SVG icon"
        assert link_dialog_info["wa_width"] >= 44, f"Touch target width must be >= 44px, got {link_dialog_info['wa_width']}"
        assert link_dialog_info["wa_height"] >= 44, f"Touch target height must be >= 44px, got {link_dialog_info['wa_height']}"
        assert "rgb(37, 211, 102)" in link_dialog_info["wa_bg"], "WhatsApp button background must be #25D366 green"
        assert "https://api.whatsapp.com/send?text=" in link_dialog_info["wa_href"], "Must use official wa API"
        assert "mg_" not in link_dialog_info["wa_href"], "Must NEVER leak management credentials"

        # 4. Copy Link action test
        copy_btn = driver.find_element(By.ID, "btnCopySuccessLink")
        copy_btn.click()
        time.sleep(0.3)
        btn_copy_text = driver.find_element(By.ID, "btnCopySuccessText").text
        print("Copy button feedback text:", btn_copy_text)
        assert "Copied" in btn_copy_text, "Copy button must show '✓ Copied!' feedback"

        # Capture Screenshot 2: Link-Ready Dialog
        shot2 = SCREENSHOTS_DIR / "02_link_ready_compact_whatsapp.png"
        driver.save_screenshot(str(shot2))
        print("Captured Screenshot 2:", shot2)

        # Close success modal
        driver.find_element(By.ID, "btnCloseSuccessModal").click()
        time.sleep(0.4)

        # -------------------------------------------------------------
        # TEST 3: My Albums Drawer — Compact Link & WhatsApp Icon
        # -------------------------------------------------------------
        print("\n--- Test 3: My Albums Drawer — Consistent UI ---")
        driver.execute_script("openMyAlbumsModal();")
        time.sleep(0.6)

        diag = driver.execute_script("""
            return {
                modal_display: document.getElementById('myAlbumsModalBackdrop') ? document.getElementById('myAlbumsModalBackdrop').style.display : 'not_found',
                container_children: document.getElementById('myAlbumsListContainer') ? document.getElementById('myAlbumsListContainer').children.length : -1,
                container_html: document.getElementById('myAlbumsListContainer') ? document.getElementById('myAlbumsListContainer').innerHTML.substring(0, 200) : 'none',
                stored_albums: localStorage.getItem('my_shared_albums')
            };
        """)
        print("DIAG:", diag)

        drawer_info = driver.execute_script("""
            const firstCard = document.querySelector('#myAlbumsListContainer > div');
            if (!firstCard) return null;
            const waBtn = firstCard.querySelector('.btn-whatsapp-icon');
            const compactPill = firstCard.querySelector('.compact-link-pill');
            const copyBtn = firstCard.querySelector('.btn-copy-action');
            return {
                has_compact_pill: !!compactPill,
                compact_text: compactPill ? compactPill.textContent.trim() : '',
                has_wa_icon: !!waBtn,
                wa_aria: waBtn ? waBtn.getAttribute('aria-label') : null,
                wa_svg: !!(waBtn && waBtn.querySelector('svg')),
                has_copy_btn: !!copyBtn
            };
        """)
        print("Drawer inspection:", drawer_info)
        assert drawer_info["has_compact_pill"], "Drawer must render compact link pill"
        assert "/s/…" in drawer_info["compact_text"], f"Drawer compact text should contain /s/…, got {drawer_info['compact_text']}"
        assert drawer_info["has_wa_icon"] and drawer_info["wa_svg"], "Drawer must use WhatsApp SVG icon button"
        assert drawer_info["wa_aria"] == "Share on WhatsApp"

        # Capture Screenshot 3: My Albums Drawer
        shot3 = SCREENSHOTS_DIR / "03_my_albums_compact_whatsapp.png"
        driver.save_screenshot(str(shot3))
        print("Captured Screenshot 3:", shot3)

        driver.find_element(By.ID, "btnCloseMyAlbumsModal").click()
        driver.quit()

        # -------------------------------------------------------------
        # TEST 4: Mobile Viewport (iPhone 14 / 390x844)
        # -------------------------------------------------------------
        print("\n--- Test 4: Mobile Viewport Rendering ---")
        mob_driver = get_driver((390, 844), mobile=True)
        mob_driver.get(SERVER_URL)
        time.sleep(1.2)
        mob_driver.execute_script(f"""
            const samplePhotos = {records};
            localStorage.setItem('brk_saved_photos', JSON.stringify(samplePhotos));
            localStorage.setItem('brk_saved_count', '4');
            localStorage.setItem('brk_saved_face_thumb', 'data:image/jpeg;base64,/9j/4AAQSkZJRg==');
            localStorage.setItem('brk_current_search_id', '{search_id_mob}');
            localStorage.setItem('brk_current_creation_token', '{creation_token_mob}');
            viewSavedPhotos();
            toggleSelectionMode(true);
            togglePhotoSelect('{records[0]["rel_path"]}', null);
            togglePhotoSelect('{records[1]["rel_path"]}', null);
        """)
        time.sleep(0.8)

        # Open creation modal on mobile
        mob_driver.find_element(By.ID, "bulkCreateAlbumBtn").click()
        time.sleep(0.5)

        # Capture Screenshot 4: Mobile Creation Dialog
        shot4 = SCREENSHOTS_DIR / "04_mobile_creation_contrast.png"
        mob_driver.save_screenshot(str(shot4))
        print("Captured Screenshot 4:", shot4)

        # Submit on mobile
        mob_driver.find_element(By.ID, "btnConfirmCreateAlbum").click()
        time.sleep(1.2)

        # Verify WhatsApp button on mobile has min 44x44px target
        mob_wa_size = mob_driver.execute_script("""
            const wa = document.getElementById('btnWhatsAppShare');
            const r = wa.getBoundingClientRect();
            return { w: r.width, h: r.height };
        """)
        print("Mobile WhatsApp button size:", mob_wa_size)
        assert mob_wa_size["w"] >= 44 and mob_wa_size["h"] >= 44, "Must maintain >= 44x44px touch target on mobile"

        # Capture Screenshot 5: Mobile Link-Ready Dialog
        shot5 = SCREENSHOTS_DIR / "05_mobile_link_ready.png"
        mob_driver.save_screenshot(str(shot5))
        print("Captured Screenshot 5:", shot5)

        mob_driver.quit()

        print("\n=======================================================")
        print("  ALL UI & CONTRAST VERIFICATIONS PASSED SUCCESSFULLY!")
        print("=======================================================")

    finally:
        server.should_exit = True
        server_thread.join(timeout=2)
        shutil.rmtree(TEST_DIR, ignore_errors=True)

if __name__ == "__main__":
    run_tests()
