"""
Regression test for Create Album button visibility & interaction bug.
Verifies that clicking 'Create Album' in the bottom toolbar opens the modal visibly,
above the results gallery, with opacity=1, pointer-events=auto, and responds to real clicks.
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

TEST_DIR = Path(tempfile.mkdtemp(prefix="brk_regression_test_"))
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
    return records, search_id, creation_token

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
    print("=== STARTING MODAL REGRESSION SUITE ===")
    records, search_id, creation_token = setup_test_data()

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
        # TEST 1: DESKTOP - Natural UI Click Opens Modal Visibly
        print("\n--- Test 1: Desktop - Natural UI Click opens modal visibly ---")
        driver = get_driver((1280, 800))
        driver.get(SERVER_URL)
        time.sleep(1.5)

        # Inject matched search results and valid search proof into client state
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

        # Real UI click on Create Album button
        create_btn = driver.find_element(By.ID, "bulkCreateAlbumBtn")
        create_btn.click()
        time.sleep(0.5)

        # Check modal computed visibility & stacking
        modal_info = driver.execute_script("""
            const el = document.getElementById('createAlbumModalBackdrop');
            const comp = window.getComputedStyle(el);
            const cx = window.innerWidth / 2;
            const cy = window.innerHeight / 2;
            const topEl = document.elementFromPoint(cx, cy);
            return {
                display: comp.display,
                opacity: parseFloat(comp.opacity),
                pointerEvents: comp.pointerEvents,
                zIndex: parseInt(comp.zIndex, 10),
                topElementTag: topEl ? topEl.tagName : null,
                topElementClass: topEl ? topEl.className : null,
                isInsideModal: el.contains(topEl) || el === topEl
            };
        """)
        print("Modal inspection:", modal_info)
        assert modal_info["display"] == "flex", f"Expected display: flex, got {modal_info['display']}"
        assert modal_info["opacity"] == 1.0, f"Expected opacity: 1.0, got {modal_info['opacity']}"
        assert modal_info["pointerEvents"] == "auto", f"Expected pointerEvents: auto, got {modal_info['pointerEvents']}"
        assert modal_info["zIndex"] >= 90000, f"Expected zIndex >= 90000, got {modal_info['zIndex']}"
        assert modal_info["isInsideModal"], f"Center of screen should be inside modal, but was {modal_info['topElementTag']}.{modal_info['topElementClass']}"

        # Capture Screenshot of visible modal
        shot1 = SCREENSHOTS_DIR / "creation_modal_opened.png"
        driver.save_screenshot(str(shot1))
        print("Captured Screenshot:", shot1)

        # Verify modal accepts typing into input
        title_input = driver.find_element(By.ID, "inputAlbumTitle")
        title_input.clear()
        title_input.send_keys("My Moments with BRK Sir")
        assert title_input.get_attribute("value") == "My Moments with BRK Sir"

        # Click Confirm Create Album button
        confirm_btn = driver.find_element(By.ID, "btnConfirmCreateAlbum")
        confirm_btn.click()
        time.sleep(1.2)

        # Verify success modal is displayed visibly
        succ_info = driver.execute_script("""
            const el = document.getElementById('albumSuccessModalBackdrop');
            const comp = window.getComputedStyle(el);
            return {
                display: comp.display,
                opacity: parseFloat(comp.opacity),
                pointerEvents: comp.pointerEvents
            };
        """)
        assert succ_info["display"] == "flex" and succ_info["opacity"] == 1.0
        share_url = driver.find_element(By.ID, "successShareLinkInput").get_attribute("value")
        print("Generated share URL:", share_url)

        # Close success modal
        driver.find_element(By.ID, "btnCloseSuccessModal").click()
        time.sleep(0.4)

        # TEST 2: RECIPIENT ZERO-AUTH VIEWER
        print("\n--- Test 2: Recipient Zero-Auth Viewer ---")
        recip_driver = get_driver((1280, 800))
        recip_driver.get(share_url)
        time.sleep(1.5)
        cards = recip_driver.find_elements(By.CSS_SELECTOR, "#sharedPhotoGrid .photo-card")
        print(f"Recipient viewer loaded {len(cards)} photos.")
        assert len(cards) == 2, f"Expected 2 photos in snapshot, found {len(cards)}"
        recip_driver.quit()

        # TEST 3: RESTORED RESULTS WITH MISSING / EXPIRED PROOF
        print("\n--- Test 3: Restored results with missing proof ---")
        driver.execute_script("""
            localStorage.removeItem('brk_current_search_id');
            localStorage.removeItem('brk_current_creation_token');
            localStorage.removeItem('brk_saved_face_thumb');
            localStorage.removeItem('brk_saved_embedding');
            currentSearchId = null;
            currentCreationToken = null;
        """)
        # Click Create Album button with 2 photos selected but no proof
        create_btn.click()
        time.sleep(0.5)

        # Verify modal did NOT open and toast alert is displayed above toolbar
        toast_info = driver.execute_script("""
            const modal = document.getElementById('createAlbumModalBackdrop');
            const toast = document.getElementById('galleryToast');
            const toastComp = window.getComputedStyle(toast);
            const bar = document.getElementById('bulkSelectionBar');
            const toastRect = toast.getBoundingClientRect();
            const barRect = bar.getBoundingClientRect();
            return {
                modalDisplay: window.getComputedStyle(modal).display,
                toastText: toast.textContent,
                toastShown: toast.classList.contains('show'),
                toastBottom: toastRect.bottom,
                barTop: barRect.top,
                isToastAboveBar: toastRect.bottom <= barRect.top + 5
            };
        """)
        print("Missing proof toast inspection:", toast_info)
        assert toast_info["modalDisplay"] == "none", "Modal must not open without proof"
        assert "Search proof expired" in toast_info["toastText"] or "Please find" in toast_info["toastText"]
        assert toast_info["toastShown"], "Actionable toast must be visible"

        # Verify selected photos were preserved!
        selected_count = driver.execute_script("return selectedPhotos.size;")
        print("Selected photos count after expired proof warning:", selected_count)
        assert selected_count == 2, f"Selection must be preserved, got {selected_count}"

        driver.quit()

        # TEST 4: MOBILE VIEWPORT (iPhone 14 / 390x844)
        print("\n--- Test 4: Mobile Viewport ---")
        mob_driver = get_driver((390, 844), mobile=True)
        mob_driver.get(SERVER_URL)
        time.sleep(1.2)
        mob_driver.execute_script(f"""
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

        mob_btn = mob_driver.find_element(By.ID, "bulkCreateAlbumBtn")
        mob_btn.click()
        time.sleep(0.5)

        mob_modal_info = mob_driver.execute_script("""
            const el = document.getElementById('createAlbumModalBackdrop');
            const comp = window.getComputedStyle(el);
            return {
                display: comp.display,
                opacity: parseFloat(comp.opacity),
                pointerEvents: comp.pointerEvents
            };
        """)
        print("Mobile modal info:", mob_modal_info)
        assert mob_modal_info["display"] == "flex" and mob_modal_info["opacity"] == 1.0

        # Close via cancel button
        mob_driver.find_element(By.ID, "btnCancelCreateAlbum").click()
        time.sleep(0.4)
        mob_closed = mob_driver.execute_script("return document.getElementById('createAlbumModalBackdrop').style.display;")
        assert mob_closed == "none", "Modal must close on cancel"

        # Open My Albums drawer on mobile
        mob_driver.find_element(By.ID, "openMyAlbumsBtn").click()
        time.sleep(0.5)
        my_albums_info = mob_driver.execute_script("""
            const el = document.getElementById('myAlbumsModalBackdrop');
            const comp = window.getComputedStyle(el);
            return {
                display: comp.display,
                opacity: parseFloat(comp.opacity)
            };
        """)
        assert my_albums_info["display"] == "flex" and my_albums_info["opacity"] == 1.0
        print("Mobile My Albums drawer opened visibly.")

        mob_driver.quit()

        print("\n=======================================================")
        print("  ALL REGRESSION CHECKS PASSED SUCCESSFULLY!")
        print("=======================================================")

    finally:
        server.should_exit = True
        server_thread.join(timeout=2)
        shutil.rmtree(TEST_DIR, ignore_errors=True)

if __name__ == "__main__":
    run_tests()

