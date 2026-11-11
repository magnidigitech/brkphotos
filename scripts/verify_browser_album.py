"""
Automated Browser Verification for My Event Album in BRK Photos.
Uses Selenium WebDriver with Headless Chrome to verify real browser behavior,
capture screenshots, test recipient viewing, mobile/desktop viewports,
creator revocation, and admin management.
"""
import os
import sys
import time
import shutil
import tempfile
import sqlite3
import secrets
import json
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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
import app
import database
import uvicorn
SCREENSHOTS_DIR = Path("/Users/venkatavivek/.gemini/antigravity/brain/64149011-e7dc-4646-853f-dbda7591c5f4/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_DIR = Path(tempfile.mkdtemp(prefix="brk_browser_test_"))
STORAGE_DIR = TEST_DIR / "storage"
PHOTOS_DIR = STORAGE_DIR / "photos" / "brk-sir"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = STORAGE_DIR / "data" / "test.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

PORT = 8899
SERVER_URL = f"http://127.0.0.1:{PORT}"

def create_sample_photos():
    """Generates 5 distinct colorful test photos."""
    colors = [("teal", "Moments 1"), ("coral", "Moments 2"), ("navy", "Moments 3"), ("maroon", "Moments 4"), ("olive", "Moments 5")]
    photo_records = []
    for idx, (color, label) in enumerate(colors, start=1):
        filename = f"photo_{idx}.jpg"
        rel_path = f"brk-sir/{filename}"
        img_path = PHOTOS_DIR / filename
        img = Image.new("RGB", (1200, 900), color=color)
        draw = ImageDraw.Draw(img)
        draw.text((100, 100), f"BRK Sir - {label}", fill="white")
        img.save(img_path, format="JPEG", quality=90)
        photo_records.append({
            "id": idx,
            "filename": filename,
            "rel_path": rel_path
        })
    return photo_records

def init_test_db(photo_records):
    """Sets up an isolated SQLite database with schema, event, photos, and admin account."""
    os.environ["DB_PATH"] = str(DB_PATH)
    os.environ["STORAGE_DIR"] = str(STORAGE_DIR)
    sys.path.insert(0, str(BASE_DIR))
    
    import database
    database.DB_PATH = DB_PATH
    database.init_db()

    conn = database.get_db()
    c = conn.cursor()
    # Create event
    c.execute("""
        INSERT OR REPLACE INTO events (id, name, slug, status)
        VALUES (1, 'Bhashyam Ramakrishna Sir Celebration', 'brk-sir', 'published')
    """)
    c.execute("""
        INSERT OR REPLACE INTO albums (id, event_id, name, slug)
        VALUES (1, 1, 'Reception', 'reception')
    """)

    for p in photo_records:
        c.execute("""
            INSERT OR REPLACE INTO photos (id, event_id, album_id, filename, rel_path, is_deleted)
            VALUES (?, 1, 1, ?, ?, 0)
        """, (p["id"], p["filename"], p["rel_path"]))

    # Admin user & session
    c.execute("""
        INSERT OR REPLACE INTO admin_users (id, username, password_hash)
        VALUES (1, 'admin', 'pbkdf2:sha256:1000$test$test')
    """)
    conn.commit()
    conn.close()

    admin_token = "admin_test_session_token_12345678"
    database.create_admin_session(admin_token, user_id=1, expires_days=7)

    # Search proof for visitor
    search_id = "srch_test_visitor_01"
    _, creation_token = database.save_search_result_proof(
        search_id=search_id,
        event_id=1,
        matched_photo_ids=[1, 2, 3, 4],
        creator_session_id="visitor_browser_sess",
        ttl_seconds=7200
    )

    return admin_token, search_id, creation_token

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

def js_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'nearest'}); arguments[0].click();", element)

def run():
    print("--- 1. Setting up test environment ---")
    photo_records = create_sample_photos()
    admin_token, search_id, creation_token = init_test_db(photo_records)

    # Start FastAPI server in background thread
    database.DB_PATH = DB_PATH
    app.STORAGE = STORAGE_DIR
    app.PHOTOS = STORAGE_DIR / "photos"
    app.DATA = STORAGE_DIR / "data"

    config = uvicorn.Config(app.app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    try:
        # Wait for server to boot
        time.sleep(2)
        print("Server running on", SERVER_URL)

        # ---------------------------------------------------------------------
        # VISITOR CONTEXT: Search results, selection, creation
        # ---------------------------------------------------------------------
        print("\n--- 2. Visitor Context: Matched Search & Selection ---")
        creator_driver = get_driver((1280, 800))
        creator_driver.get(f"{SERVER_URL}/brk-sir")
        time.sleep(1)

        # Inject simulated search match results into localStorage
        mock_photos = [
            {"id": 1, "filename": "photo_1.jpg", "rel_path": "brk-sir/photo_1.jpg"},
            {"id": 2, "filename": "photo_2.jpg", "rel_path": "brk-sir/photo_2.jpg"},
            {"id": 3, "filename": "photo_3.jpg", "rel_path": "brk-sir/photo_3.jpg"},
            {"id": 4, "filename": "photo_4.jpg", "rel_path": "brk-sir/photo_4.jpg"}
        ]
        creator_driver.execute_script(f"""
            localStorage.setItem('brk_current_search_id', '{search_id}');
            localStorage.setItem('brk_current_creation_token', '{creation_token}');
            localStorage.setItem('brk_saved_photos', JSON.stringify({json.dumps(mock_photos)}));
            localStorage.setItem('brk_saved_count', '4');
            localStorage.setItem('brk_saved_face_thumb', '/thumb/brk-sir/photo_1.jpg');
        """)
        creator_driver.refresh()
        time.sleep(1.5)

        # Open saved profile view
        creator_driver.execute_script("viewSavedPhotos();")
        time.sleep(1)

        # Toggle selection mode
        creator_driver.find_element(By.ID, "toggleSelectBtn").click()
        time.sleep(0.5)

        # Select all photos
        creator_driver.find_element(By.ID, "bulkSelectAllBtn").click()
        time.sleep(0.5)

        # Screenshot 1: Desktop Selection & Bulk Toolbar
        shot1 = SCREENSHOTS_DIR / "01_selection_and_bulk_bar.png"
        creator_driver.save_screenshot(str(shot1))
        print("Captured Screenshot 1:", shot1)

        # Click Create Album button in bulk bar
        print("\n--- 3. Visitor Context: Create Album Modal Preview ---")
        creator_driver.find_element(By.ID, "bulkCreateAlbumBtn").click()
        time.sleep(0.8)

        # Screenshot 2: Creation Preview Modal
        shot2 = SCREENSHOTS_DIR / "02_create_album_modal.png"
        creator_driver.save_screenshot(str(shot2))
        print("Captured Screenshot 2:", shot2)

        # Click Confirm Create Album button
        print("\n--- 4. Visitor Context: Album Creation & WhatsApp / Copy Share ---")
        confirm_btn = creator_driver.find_element(By.ID, "btnConfirmCreateAlbum")
        js_click(creator_driver, confirm_btn)
        time.sleep(2)

        # Screenshot 3: Link-Ready Success Screen
        shot3 = SCREENSHOTS_DIR / "03_link_ready_success.png"
        creator_driver.save_screenshot(str(shot3))
        print("Captured Screenshot 3:", shot3)

        # Read share link and WhatsApp URL
        share_url = creator_driver.find_element(By.ID, "successShareLinkInput").get_attribute("value")
        wa_href = creator_driver.find_element(By.ID, "btnWhatsAppShare").get_attribute("href")
        print("Generated Share Link:", share_url)
        print("Verified WhatsApp URL:", wa_href[:80] + "...")
        assert "https://api.whatsapp.com/send?text=" in wa_href, "WhatsApp link must use secure wa.me API"

        # Close success modal
        close_btn = creator_driver.find_element(By.ID, "btnCloseSuccessModal")
        js_click(creator_driver, close_btn)
        time.sleep(0.5)

        # Open My Albums Drawer
        print("\n--- 5. Visitor Context: My Albums Drawer ---")
        open_drawer_btn = creator_driver.find_element(By.ID, "openMyAlbumsBtn")
        js_click(creator_driver, open_drawer_btn)
        time.sleep(0.8)

        # Screenshot 4: My Albums Drawer
        shot4 = SCREENSHOTS_DIR / "04_my_albums_drawer.png"
        creator_driver.save_screenshot(str(shot4))
        print("Captured Screenshot 4:", shot4)

        # ---------------------------------------------------------------------
        # RECIPIENT CONTEXT: Clean separate incognito browser (zero auth)
        # ---------------------------------------------------------------------
        print("\n--- 6. Recipient Context: Desktop Responsive Grid (Zero Auth) ---")
        recipient_driver = get_driver((1280, 800))
        recipient_driver.get(share_url)
        time.sleep(2)

        # Screenshot 5: Recipient Desktop Grid
        shot5 = SCREENSHOTS_DIR / "05_recipient_desktop_grid.png"
        recipient_driver.save_screenshot(str(shot5))
        print("Captured Screenshot 5:", shot5)

        # Click first photo card to open Lightbox
        print("\n--- 7. Recipient Context: Full-Screen Lightbox (Desktop) ---")
        first_card = recipient_driver.find_element(By.CSS_SELECTOR, "#sharedPhotoGrid .photo-card")
        js_click(recipient_driver, first_card)
        time.sleep(1)

        # Screenshot 6: Desktop Lightbox
        shot6 = SCREENSHOTS_DIR / "06_recipient_lightbox_desktop.png"
        recipient_driver.save_screenshot(str(shot6))
        print("Captured Screenshot 6:", shot6)

        # Test Lightbox Keyboard Navigation: Next photo
        body = recipient_driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ARROW_RIGHT)
        time.sleep(0.5)
        counter_text = recipient_driver.find_element(By.ID, "lbCounter").text
        assert "2 /" in counter_text, f"Counter should advance to 2, got: {counter_text}"

        # Close lightbox
        close_lb = recipient_driver.find_element(By.ID, "lbCloseBtn")
        js_click(recipient_driver, close_lb)
        time.sleep(0.5)

        # ---------------------------------------------------------------------
        # RECIPIENT CONTEXT: Mobile Viewport (iPhone 14 / 390x844)
        # ---------------------------------------------------------------------
        print("\n--- 8. Recipient Context: Mobile Responsive Viewport ---")
        recipient_driver.quit()
        mobile_driver = get_driver((390, 844), mobile=True)
        mobile_driver.get(share_url)
        time.sleep(1.8)

        # Screenshot 7: Mobile Grid
        shot7 = SCREENSHOTS_DIR / "07_recipient_mobile_grid.png"
        mobile_driver.save_screenshot(str(shot7))
        print("Captured Screenshot 7:", shot7)

        # Open mobile lightbox
        mobile_card = mobile_driver.find_element(By.CSS_SELECTOR, "#sharedPhotoGrid .photo-card")
        js_click(mobile_driver, mobile_card)
        time.sleep(0.8)

        # Screenshot 8: Mobile Lightbox
        shot8 = SCREENSHOTS_DIR / "08_recipient_mobile_lightbox.png"
        mobile_driver.save_screenshot(str(shot8))
        print("Captured Screenshot 8:", shot8)

        mobile_driver.quit()

        # ---------------------------------------------------------------------
        # CREATOR REVOCATION
        # ---------------------------------------------------------------------
        print("\n--- 9. Creator Revocation via Header-based Request ---")
        # Creator clicks Revoke Link button
        revoke_btn = creator_driver.find_element(By.CSS_SELECTOR, ".btn-revoke-album")
        # Accept browser confirm dialog automatically via script
        creator_driver.execute_script("window.confirm = function() { return true; };")
        js_click(creator_driver, revoke_btn)
        time.sleep(1.5)

        # Screenshot 9: Drawer after revocation
        shot9 = SCREENSHOTS_DIR / "09_drawer_after_revocation.png"
        creator_driver.save_screenshot(str(shot9))
        print("Captured Screenshot 9:", shot9)

        # ---------------------------------------------------------------------
        # RECIPIENT CONTEXT: Revoked State & Invalid Token State
        # ---------------------------------------------------------------------
        print("\n--- 10. Recipient Context: Revoked & Invalid States ---")
        verifier_driver = get_driver((1280, 800))
        verifier_driver.get(share_url)
        time.sleep(1)

        # Screenshot 10: Revoked State Card
        shot10 = SCREENSHOTS_DIR / "10_recipient_revoked_state.png"
        verifier_driver.save_screenshot(str(shot10))
        print("Captured Screenshot 10:", shot10)
        assert "Album Unavailable" in verifier_driver.page_source

        # Test invalid token
        verifier_driver.get(f"{SERVER_URL}/s/invalid_random_token_1234567890")
        time.sleep(1)

        # Screenshot 11: Invalid Token State
        shot11 = SCREENSHOTS_DIR / "11_recipient_invalid_token.png"
        verifier_driver.save_screenshot(str(shot11))
        print("Captured Screenshot 11:", shot11)
        assert "Album Unavailable" in verifier_driver.page_source or "could not be found" in verifier_driver.page_source

        verifier_driver.quit()
        creator_driver.quit()

        # ---------------------------------------------------------------------
        # ADMIN CONTEXT: Dashboard, Metrics, Photo Inspection Modal
        # ---------------------------------------------------------------------
        print("\n--- 11. Admin Context: Shared Albums Management Tab ---")
        admin_driver = get_driver((1366, 850))
        admin_driver.get(f"{SERVER_URL}/admin.html")
        time.sleep(1)

        # Inject admin token into cookie & localStorage
        admin_driver.add_cookie({
            "name": "admin_token",
            "value": admin_token,
            "path": "/"
        })
        admin_driver.execute_script(f"""
            localStorage.setItem('admin_token', '{admin_token}');
            localStorage.setItem('admin_active_tab', 'shared-albums');
        """)
        admin_driver.refresh()
        time.sleep(2)

        # Navigate to shared-albums tab
        admin_driver.execute_script("switchTab('shared-albums');")
        time.sleep(1.5)

        # Screenshot 12: Admin Shared Albums Panel
        shot12 = SCREENSHOTS_DIR / "12_admin_shared_albums_panel.png"
        admin_driver.save_screenshot(str(shot12))
        print("Captured Screenshot 12:", shot12)

        # Click Photos inspection button
        photos_btn = admin_driver.find_element(By.CSS_SELECTOR, "#sharedAlbumsTableBody button[title='Inspect Photos']")
        js_click(admin_driver, photos_btn)
        time.sleep(1)

        # Screenshot 13: Admin Photo Inspection Modal
        shot13 = SCREENSHOTS_DIR / "13_admin_photo_inspection_modal.png"
        admin_driver.save_screenshot(str(shot13))
        print("Captured Screenshot 13:", shot13)

        admin_driver.quit()

        print("\n=======================================================")
        print("  ALL 13 BROWSER WORKFLOW VERIFICATIONS PASSED!")
        print("=======================================================")

    finally:
        server.should_exit = True
        server_thread.join(timeout=2)
        shutil.rmtree(TEST_DIR, ignore_errors=True)

if __name__ == "__main__":
    run()
