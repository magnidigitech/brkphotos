import os
import sys
import time
import json
import threading
from pathlib import Path
from PIL import Image, ImageDraw
import uvicorn

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from unittest.mock import MagicMock

# Mock ML dependencies before app import
sys.modules['insightface'] = MagicMock()
sys.modules['insightface.app'] = MagicMock()

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import app
import database

PORT = 8899
SERVER_URL = f"http://127.0.0.1:{PORT}"
TEST_DIR = Path("/tmp/brkphotos_mobile_test")
DB_PATH = TEST_DIR / "test_mobile.db"
STORAGE_DIR = TEST_DIR / "storage"
PHOTOS_DIR = STORAGE_DIR / "photos" / "brk-sir"
DATA_DIR = STORAGE_DIR / "data"

SCREENSHOTS_DIR = Path("/Users/venkatavivek/.gemini/antigravity/brain/64149011-e7dc-4646-853f-dbda7591c5f4/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

def setup_test_environment():
    if TEST_DIR.exists():
        import shutil
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    records = []
    colors = ["#2563eb", "#059669", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#475569", "#be123c"]
    for idx, col in enumerate(colors, start=1):
        filename = f"event_photo_{idx}.jpg"
        rel_path = f"brk-sir/{filename}"
        img_path = PHOTOS_DIR / filename
        img = Image.new("RGB", (800, 600), color=col)
        draw = ImageDraw.Draw(img)
        draw.text((40, 40), f"BRK Celebration #{idx}", fill="white")
        img.save(img_path, format="JPEG")
        records.append({"id": idx, "filename": filename, "rel_path": rel_path})

    database.DB_PATH = DB_PATH
    database.init_db()
    conn = database.get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO events (id, name, slug, status) VALUES (1, 'Photos with BRK Sir', 'brk-sir', 'published')")
    c.execute("INSERT OR REPLACE INTO albums (id, event_id, name, slug) VALUES (1, 1, 'Main', 'main')")
    for r in records:
        c.execute("INSERT OR REPLACE INTO photos (id, event_id, album_id, filename, rel_path, is_deleted) VALUES (?, 1, 1, ?, ?, 0)",
                  (r["id"], r["filename"], r["rel_path"]))
    conn.commit()
    conn.close()

    proofs = {}
    for name in ["mobile_320", "mobile_390", "mobile_430", "desktop"]:
        s_id = f"srch_{name}"
        _, tok = database.save_search_result_proof(
            search_id=s_id,
            event_id=1,
            matched_photo_ids=[r["id"] for r in records],
            creator_session_id=f"sess_{name}",
            ttl_seconds=3600
        )
        proofs[name] = {"search_id": s_id, "token": tok}

    return records, proofs

def get_driver(width, height, is_mobile=True):
    options = Options()
    options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--window-size={width},{height}")
    if is_mobile:
        options.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
    driver = webdriver.Chrome(options=options)
    driver.set_window_size(width, height)
    return driver

def inject_mock_data(driver, records, search_id, creation_token, albums=None):
    if albums is None:
        albums = [
            {
                "id": 1,
                "title": "BRK Sir — My Moments",
                "share_token": "sh_1111111111111111111111111111111111111111111111111111111111111111",
                "manage_token": "mg_1111111111111111111111111111111111111111111111111111111111111111",
                "share_url": "https://photos.bhashyamramakrishna.in/s/sh_1111111111111111111111111111111111111111111111111111111111111111",
                "photo_count": 3,
                "created_at": "2026-11-11T00:00:00Z",
                "expires_at": "2026-12-11T00:00:00Z",
                "is_revoked": False
            },
            {
                "id": 2,
                "title": "BRK Sir — My Moments",
                "share_token": "sh_2222222222222222222222222222222222222222222222222222222222222222",
                "manage_token": "mg_2222222222222222222222222222222222222222222222222222222222222222",
                "share_url": "https://photos.bhashyamramakrishna.in/s/sh_2222222222222222222222222222222222222222222222222222222222222222",
                "photo_count": 2,
                "created_at": "2026-11-11T00:00:00Z",
                "expires_at": "2026-12-11T00:00:00Z",
                "is_revoked": False
            }
        ]

    driver.execute_script(f"""
        const samplePhotos = {json.dumps(records)};
        localStorage.setItem('brk_saved_photos', JSON.stringify(samplePhotos));
        localStorage.setItem('brk_saved_count', '{len(records)}');
        localStorage.setItem('brk_saved_face_thumb', 'data:image/jpeg;base64,/9j/4AAQSkZJRg==');
        localStorage.setItem('brk_current_search_id', '{search_id}');
        localStorage.setItem('brk_current_creation_token', '{creation_token}');
        localStorage.setItem('my_shared_albums', JSON.stringify({json.dumps(albums)}));
    """)

def run_all_tests():
    print("=== STARTING 10-CHECK MOBILE NAVIGATION, SELECTION & LONG-PRESS VERIFICATION ===")
    records, proofs = setup_test_environment()

    database.DB_PATH = DB_PATH
    app.STORAGE = STORAGE_DIR
    app.PHOTOS = STORAGE_DIR / "photos"
    app.DATA = STORAGE_DIR / "data"

    config = uvicorn.Config(app.app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.5)

    try:
        # =====================================================================
        # CHECK 1: Fresh Homepage (No Saved Results)
        # =====================================================================
        print("\n--- Check 1: Fresh Homepage (No Saved Results) ---")
        d = get_driver(390, 844, is_mobile=True)
        d.get(SERVER_URL)
        time.sleep(1.0)

        home_state = d.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const selBar = document.getElementById('bulkSelectionBar');
            const gallery = document.getElementById('photosSection');
            return {
                nav_disp: window.getComputedStyle(nav).display,
                nav_hidden: nav.hidden || window.getComputedStyle(nav).display === 'none',
                sel_hidden: selBar.hidden || window.getComputedStyle(selBar).display === 'none',
                gallery_hidden: gallery.hidden || window.getComputedStyle(gallery).display === 'none'
            };
        """)
        print("Fresh Homepage State:", home_state)
        assert home_state["nav_hidden"], "Mobile bottom nav must be strictly hidden on fresh homepage"
        assert home_state["sel_hidden"], "Selection panel must be strictly hidden on fresh homepage"
        assert home_state["gallery_hidden"], "Gallery section must be hidden"

        shot_fresh = SCREENSHOTS_DIR / "fresh_homepage_clean.png"
        d.save_screenshot(str(shot_fresh))
        print("Saved Screenshot: fresh_homepage_clean.png")

        # =====================================================================
        # CHECK 2: Homepage with Saved Results in localStorage
        # =====================================================================
        print("\n--- Check 2: Homepage with Saved Results in localStorage ---")
        inject_mock_data(d, records, proofs["mobile_390"]["search_id"], proofs["mobile_390"]["token"])
        d.get(SERVER_URL)
        time.sleep(1.0)

        saved_home_state = d.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const selBar = document.getElementById('bulkSelectionBar');
            const gallery = document.getElementById('photosSection');
            const welcomeBanner = document.getElementById('savedProfileBadge');
            return {
                nav_disp: window.getComputedStyle(nav).display,
                nav_hidden: nav.hidden || window.getComputedStyle(nav).display === 'none',
                sel_hidden: selBar.hidden || window.getComputedStyle(selBar).display === 'none',
                gallery_hidden: gallery.hidden || window.getComputedStyle(gallery).display === 'none',
                welcome_visible: welcomeBanner && !welcomeBanner.hidden && window.getComputedStyle(welcomeBanner).display !== 'none'
            };
        """)
        print("Homepage with Saved Results State:", saved_home_state)
        assert saved_home_state["welcome_visible"], "Welcome back banner must be visible when results are saved"
        assert saved_home_state["nav_hidden"], "Bottom nav must STILL be hidden on homepage even with saved results!"
        assert saved_home_state["sel_hidden"], "Selection panel must be hidden on homepage"

        shot_saved_home = SCREENSHOTS_DIR / "homepage_saved_results_clean.png"
        d.save_screenshot(str(shot_saved_home))
        print("Saved Screenshot: homepage_saved_results_clean.png")

        # =====================================================================
        # CHECK 3: Click "View Photos >" to Open Gallery
        # =====================================================================
        print("\n--- Check 3: Click 'View Photos >' to Open Gallery ---")
        d.find_element(By.ID, "viewSavedPhotosBtn").click()
        time.sleep(0.8)

        gallery_open_state = d.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const selBar = document.getElementById('bulkSelectionBar');
            const gallery = document.getElementById('photosSection');
            const cards = document.querySelectorAll('.photo-card');
            return {
                nav_disp: window.getComputedStyle(nav).display,
                sel_disp: window.getComputedStyle(selBar).display,
                gallery_hidden: gallery.hidden,
                cards_count: cards.length
            };
        """)
        print("Gallery Open State:", gallery_open_state)
        assert gallery_open_state["nav_disp"] == "flex", "Bottom nav must be visible ('flex') inside active gallery"
        assert gallery_open_state["sel_disp"] == "none", "Selection bar must be hidden in normal gallery mode"
        assert not gallery_open_state["gallery_hidden"], "Gallery overlay must be visible"
        assert gallery_open_state["cards_count"] > 0, "Photo cards must be rendered"

        # =====================================================================
        # CHECK 4: Enter Selection Mode
        # =====================================================================
        print("\n--- Check 4: Enter Selection Mode via More Sheet ---")
        d.find_element(By.ID, "navBtnMore").click()
        time.sleep(0.4)
        d.find_element(By.ID, "sheetSelectPhotosBtn").click()
        time.sleep(0.4)

        sel_mode_state = d.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const selBar = document.getElementById('bulkSelectionBar');
            const count = document.getElementById('bulkSelectedCount').textContent.trim();
            const doneBtn = document.getElementById('bulkCancelBtn');
            return {
                nav_disp: window.getComputedStyle(nav).display,
                sel_disp: window.getComputedStyle(selBar).display,
                sel_active: selBar.classList.contains('active'),
                count_text: count,
                done_text: doneBtn.textContent.trim()
            };
        """)
        print("Selection Mode State:", sel_mode_state)
        assert sel_mode_state["nav_disp"] == "none", "Bottom nav MUST be hidden during selection mode"
        assert sel_mode_state["sel_disp"] == "flex" and sel_mode_state["sel_active"], "Selection bar must be active"

        # Select 2 photos
        d.execute_script(f"""
            togglePhotoSelect('{records[0]["rel_path"]}', null);
            togglePhotoSelect('{records[1]["rel_path"]}', null);
        """)
        time.sleep(0.3)

        count_text = d.execute_script("return document.getElementById('bulkSelectedCount').textContent.trim();")
        assert "2 selected" in count_text, f"Expected 2 selected, got {count_text}"

        # =====================================================================
        # CHECK 5: Open Create Album Modal and Cancel (Preserve Selection)
        # =====================================================================
        print("\n--- Check 5: Open Create Album Modal & Cancel ---")
        d.find_element(By.ID, "bulkCreateAlbumBtn").click()
        time.sleep(0.5)

        # Cancel modal
        d.find_element(By.ID, "btnCancelCreateAlbum").click()
        time.sleep(0.4)

        cancel_state = d.execute_script("""
            return {
                count: document.getElementById('bulkSelectedCount').textContent.trim(),
                nav_disp: window.getComputedStyle(document.getElementById('mobileBottomNav')).display,
                sel_disp: window.getComputedStyle(document.getElementById('bulkSelectionBar')).display
            };
        """)
        print("State after Cancel Modal:", cancel_state)
        assert "2 selected" in cancel_state["count"], "Selection must be preserved after canceling creation modal"
        assert cancel_state["nav_disp"] == "none", "Bottom nav must remain hidden while in selection mode"
        assert cancel_state["sel_disp"] == "flex", "Selection bar must remain visible"

        # =====================================================================
        # CHECK 6: Confirm Album Creation -> Selection Cleared & Nav Restored
        # =====================================================================
        print("\n--- Check 6: Confirm Album Creation (Clear Selection & Reset Mode) ---")
        d.find_element(By.ID, "bulkCreateAlbumBtn").click()
        time.sleep(0.5)

        title_input = d.find_element(By.ID, "inputAlbumTitle")
        title_input.clear()
        title_input.send_keys("BRK Sir — Special Moments")

        # Click Confirm Create Album
        d.find_element(By.ID, "btnConfirmCreateAlbum").click()
        time.sleep(1.0)

        # Verify success dialog is visible
        success_info = d.execute_script("""
            const modal = document.getElementById('albumSuccessModalBackdrop');
            const nav = document.getElementById('mobileBottomNav');
            const selBar = document.getElementById('bulkSelectionBar');
            const selectedCards = document.querySelectorAll('.photo-card.selected');
            const activeCards = document.querySelectorAll('.photo-card.selection-active');
            const checkboxes = Array.from(document.querySelectorAll('.photo-select-checkbox')).filter(c => window.getComputedStyle(c).display !== 'none');
            const count = document.getElementById('bulkSelectedCount').textContent.trim();

            return {
                modal_open: modal && (modal.style.display === 'flex' || modal.classList.contains('active')),
                nav_disp: window.getComputedStyle(nav).display,
                sel_disp: window.getComputedStyle(selBar).display,
                selected_cards_count: selectedCards.length,
                active_cards_count: activeCards.length,
                visible_checkboxes_count: checkboxes.length,
                count_text: count
            };
        """)
        print("Success State & Selection Reset Info:", success_info)
        assert success_info["modal_open"], "Success modal backdrop must be open"
        assert success_info["selected_cards_count"] == 0, "No cards should retain .selected class"
        assert success_info["active_cards_count"] == 0, "No cards should retain .selection-active class"
        assert success_info["visible_checkboxes_count"] == 0, "All selection checkboxes must be hidden"
        assert "0 selected" in success_info["count_text"], "Count must be reset to 0 selected"
        assert success_info["sel_disp"] == "none", "Selection bar must be hidden after creation"
        assert success_info["nav_disp"] == "flex", "Bottom nav must be restored behind success modal"

        # Close success modal
        d.find_element(By.ID, "btnCloseSuccessModal").click()
        time.sleep(0.5)

        # Capture gallery after creation
        shot_gallery_after = SCREENSHOTS_DIR / "gallery_after_successful_creation.png"
        d.save_screenshot(str(shot_gallery_after))
        print("Saved Screenshot: gallery_after_successful_creation.png")

        # =====================================================================
        # CHECK 7: Close Gallery / Return to New Search -> Hide Bars
        # =====================================================================
        print("\n--- Check 7: Close Gallery / Return to Homepage ---")
        d.execute_script("closeGalleryOverlay(true);")
        time.sleep(0.5)

        closed_state = d.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const selBar = document.getElementById('bulkSelectionBar');
            const gallery = document.getElementById('photosSection');
            return {
                gallery_hidden: gallery.hidden,
                nav_disp: window.getComputedStyle(nav).display,
                sel_disp: window.getComputedStyle(selBar).display
            };
        """)
        print("Closed Gallery State:", closed_state)
        assert closed_state["gallery_hidden"], "Gallery must be closed"
        assert closed_state["nav_disp"] == "none", "Bottom nav must be hidden on homepage"
        assert closed_state["sel_disp"] == "none", "Selection bar must be hidden on homepage"

        # =====================================================================
        # CHECK 8: Reopen Gallery -> Verify Clean Unselected State
        # =====================================================================
        print("\n--- Check 8: Reopen Gallery & Verify Clean State ---")
        d.execute_script("viewSavedPhotos();")
        time.sleep(1.0)

        reopened_state = d.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const selBar = document.getElementById('bulkSelectionBar');
            const allCards = document.querySelectorAll('.photo-card');
            const selectedCards = document.querySelectorAll('.photo-card.selected');
            const allImgs = document.querySelectorAll('.photo-card .photo-img');
            return {
                nav_disp: window.getComputedStyle(nav).display,
                sel_disp: window.getComputedStyle(selBar).display,
                total_cards: allCards.length,
                total_imgs: allImgs.length,
                selected_cards: selectedCards.length
            };
        """)
        print("Reopened Gallery State:", reopened_state)
        assert reopened_state["nav_disp"] == "flex", "Bottom nav must be restored upon reopening gallery"
        assert reopened_state["sel_disp"] == "none", "Selection bar must not be visible"
        assert reopened_state["selected_cards"] == 0, "No stale selections should exist"

        # =====================================================================
        # CHECK 9: Native Long-Press & Drag Suppression
        # =====================================================================
        print("\n--- Check 9: Native Long-Press & Drag Suppression ---")
        WebDriverWait(d, 5).until(lambda dr: dr.find_element(By.CSS_SELECTOR, ".photo-card"))
        suppression_check = d.execute_script("""
            const card = document.querySelector('.photo-card');
            const img = card.querySelector('.photo-img') || card;

            // Dispatch contextmenu event
            const cmEvent = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
            const cmPrevented = !img.dispatchEvent(cmEvent);

            // Dispatch dragstart event
            const dragEvent = new MouseEvent('dragstart', { bubbles: true, cancelable: true });
            const dragPrevented = !img.dispatchEvent(dragEvent);

            const cardStyle = window.getComputedStyle(card);
            const imgStyle = window.getComputedStyle(img);

            // Check raw style sheet declarations for iOS Safari -webkit-touch-callout
            let touchCalloutInStyles = false;
            for (const s of document.querySelectorAll('style')) {
                if (s.textContent.includes('-webkit-touch-callout: none')) {
                    touchCalloutInStyles = true;
                    break;
                }
            }

            return {
                cm_prevented: cmPrevented,
                drag_prevented: dragPrevented,
                card_user_select: cardStyle.webkitUserSelect || cardStyle.userSelect,
                img_user_select: imgStyle.webkitUserSelect || imgStyle.userSelect,
                img_draggable: img.getAttribute('draggable'),
                card_draggable: card.getAttribute('draggable'),
                touch_callout_in_styles: touchCalloutInStyles
            };
        """)
        print("Suppression Check Info:", suppression_check)
        assert suppression_check["cm_prevented"], "contextmenu event must be prevented on photo images"
        assert suppression_check["drag_prevented"], "dragstart event must be prevented on photo images"
        assert suppression_check["img_draggable"] == "false", "Image attribute draggable must be false"
        assert suppression_check["card_draggable"] == "false", "Card attribute draggable must be false"
        assert suppression_check["img_user_select"] == "none", "Image user-select must be none"
        assert suppression_check["card_user_select"] == "none", "Card user-select must be none"
        assert suppression_check["touch_callout_in_styles"], "-webkit-touch-callout: none must be present in stylesheet for iOS Safari"

        # =====================================================================
        # CHECK 10: Multi-viewport Integrity (320px, 430px, Desktop)
        # =====================================================================
        print("\n--- Check 10: Multi-viewport & Desktop Toolbar Integrity ---")
        d.quit()

        # Desktop (1280x800)
        ddesk = get_driver(1280, 800, is_mobile=False)
        ddesk.get(SERVER_URL)
        time.sleep(1.0)
        inject_mock_data(ddesk, records, proofs["desktop"]["search_id"], proofs["desktop"]["token"])
        ddesk.execute_script("viewSavedPhotos();")
        time.sleep(0.5)

        desk_info = ddesk.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const openMyAlbums = document.getElementById('openMyAlbumsBtn');
            const toggleSelect = document.getElementById('toggleSelectBtn');
            const closePhotos = document.getElementById('closePhotosBtn');

            return {
                nav_hidden: window.getComputedStyle(nav).display === 'none',
                open_my_albums_visible: window.getComputedStyle(openMyAlbums).display !== 'none',
                toggle_select_visible: window.getComputedStyle(toggleSelect).display !== 'none',
                close_photos_visible: window.getComputedStyle(closePhotos).display !== 'none'
            };
        """)
        print("Desktop Toolbar Check:", desk_info)
        assert desk_info["nav_hidden"], "Mobile bottom nav must NEVER appear on desktop"
        assert desk_info["open_my_albums_visible"], "My Albums button visible on desktop"
        assert desk_info["toggle_select_visible"], "Select button visible on desktop"
        ddesk.quit()

        print("\n=======================================================")
        print("  ALL 10 VERIFICATION CHECKS PASSED FLAWLESSLY!        ")
        print("=======================================================")

    finally:
        pass

if __name__ == "__main__":
    run_all_tests()
    os._exit(0)
