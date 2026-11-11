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
        viewSavedPhotos();
    """)
    time.sleep(1.0)

def run_all_tests():
    print("=== STARTING COMPREHENSIVE MOBILE GALLERY & BOTTOM NAV VERIFICATION ===")
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
        # CHECK 1: 320px Ultra-Narrow Mobile Gallery
        # =====================================================================
        print("\n--- Check 1: 320px Viewport (iPhone SE / Ultra-Narrow) ---")
        d320 = get_driver(320, 568, is_mobile=True)
        d320.get(SERVER_URL)
        time.sleep(1.0)
        inject_mock_data(d320, records, proofs["mobile_320"]["search_id"], proofs["mobile_320"]["token"])

        info320 = d320.execute_script("""
            const header = document.querySelector('.gallery-sticky-header');
            const nav = document.getElementById('mobileBottomNav');
            const navItems = Array.from(document.querySelectorAll('.bottom-nav-item')).map(it => ({
                label: it.querySelector('.bottom-nav-label')?.textContent.trim(),
                active: it.classList.contains('active'),
                w: it.getBoundingClientRect().width,
                h: it.getBoundingClientRect().height
            }));
            const desktopActions = document.querySelector('.gallery-actions-wrap');
            const openMyAlbumsDisp = window.getComputedStyle(document.getElementById('openMyAlbumsBtn')).display;
            const toggleSelectDisp = window.getComputedStyle(document.getElementById('toggleSelectBtn')).display;
            const closePhotosDisp = window.getComputedStyle(document.getElementById('closePhotosBtn')).display;
            const closeBtn = document.getElementById('galleryDismissBtn');
            const closeBtnRect = closeBtn.getBoundingClientRect();
            const discoveryDisp = window.getComputedStyle(document.getElementById('discoveryToolbar')).display;

            return {
                header_position: window.getComputedStyle(header).position,
                header_h: header.getBoundingClientRect().height,
                nav_displayed: window.getComputedStyle(nav).display,
                nav_items: navItems,
                open_albums_hidden: openMyAlbumsDisp === 'none',
                toggle_select_hidden: toggleSelectDisp === 'none',
                close_photos_hidden: closePhotosDisp === 'none',
                close_btn_w: closeBtnRect.width,
                close_btn_h: closeBtnRect.height,
                discovery_hidden: discoveryDisp === 'none'
            };
        """)
        print("320px Info:", info320)
        assert info320["nav_displayed"] == "flex", "Bottom nav must be visible on 320px mobile"
        assert len(info320["nav_items"]) == 4, "Must have 4 bottom nav destinations"
        assert info320["nav_items"][0]["label"] == "Photos" and info320["nav_items"][0]["active"], "Photos should be active by default"
        assert info320["open_albums_hidden"] and info320["toggle_select_hidden"] and info320["close_photos_hidden"], "Desktop header buttons must be hidden on mobile"
        assert info320["discovery_hidden"], "Discovery toolbar must not crowd header on mobile"
        assert info320["close_btn_w"] >= 36 and info320["close_btn_h"] >= 36, "Close button must be comfortably tappable"

        shot_320 = SCREENSHOTS_DIR / "mobile_320_normal_gallery.png"
        d320.save_screenshot(str(shot_320))
        print("Saved Screenshot: mobile_320_normal_gallery.png")
        d320.quit()

        # =====================================================================
        # CHECK 2: 390px Standard Mobile Viewport (iPhone 14)
        # =====================================================================
        print("\n--- Check 2: 390px Viewport (iPhone 14) ---")
        d390 = get_driver(390, 844, is_mobile=True)
        d390.get(SERVER_URL)
        time.sleep(1.0)
        inject_mock_data(d390, records, proofs["mobile_390"]["search_id"], proofs["mobile_390"]["token"])

        shot_390_normal = SCREENSHOTS_DIR / "mobile_390_normal_gallery.png"
        d390.save_screenshot(str(shot_390_normal))
        print("Saved Screenshot: mobile_390_normal_gallery.png")

        # -----------------------------------------------------------------
        # CHECK 3: More Sheet -> Select Photos -> Contextual Selection Mode
        # -----------------------------------------------------------------
        print("\n--- Check 3: More Sheet and Contextual Selection Mode ---")
        d390.find_element(By.ID, "navBtnMore").click()
        time.sleep(0.4)

        more_sheet_info = d390.execute_script("""
            const sheet = document.getElementById('moreSheetBackdrop');
            const cont = document.getElementById('moreSheetContainer');
            return {
                hidden: sheet.hidden,
                active: sheet.classList.contains('active'),
                select_photos_btn: !!document.getElementById('sheetSelectPhotosBtn'),
                new_search_btn: !!document.getElementById('sheetNewSearchBtn')
            };
        """)
        print("More Sheet Info:", more_sheet_info)
        assert not more_sheet_info["hidden"] and more_sheet_info["active"], "More sheet must open"

        shot_390_sheet = SCREENSHOTS_DIR / "mobile_390_more_sheet.png"
        d390.save_screenshot(str(shot_390_sheet))
        print("Saved Screenshot: mobile_390_more_sheet.png")

        # Click "Select Photos" inside More Sheet
        d390.find_element(By.ID, "sheetSelectPhotosBtn").click()
        time.sleep(0.5)

        # Verify bottom nav replaced by Selection Panel
        selection_mode_info = d390.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const selBar = document.getElementById('bulkSelectionBar');
            const createBtn = document.getElementById('bulkCreateAlbumBtn');
            const zipBtn = document.getElementById('bulkDownloadZipBtn');
            const countEl = document.getElementById('bulkSelectedCount');
            const doneBtn = document.getElementById('bulkCancelBtn');

            const navDisp = window.getComputedStyle(nav).display;
            const selBarActive = selBar.classList.contains('active');
            const createRect = createBtn.getBoundingClientRect();
            const zipRect = zipBtn.getBoundingClientRect();

            return {
                nav_hidden: navDisp === 'none',
                selection_bar_active: selBarActive,
                count_text: countEl.textContent.trim(),
                done_text: doneBtn.textContent.trim(),
                create_disabled: createBtn.disabled || window.getComputedStyle(createBtn).pointerEvents === 'none',
                zip_disabled: zipBtn.disabled || window.getComputedStyle(zipBtn).pointerEvents === 'none',
                create_w: createRect.width,
                zip_w: zipRect.width,
                equal_width: Math.abs(createRect.width - zipRect.width) <= 4,
                create_h: createRect.height,
                zip_h: zipRect.height
            };
        """)
        print("Selection Mode Info (0 selected):", selection_mode_info)
        assert selection_mode_info["nav_hidden"], "Bottom navigation must be hidden in selection mode"
        assert selection_mode_info["selection_bar_active"], "Selection panel must be active in selection mode"
        assert selection_mode_info["done_text"] == "Done", "Exit button must be labeled 'Done'"
        assert selection_mode_info["create_disabled"] and selection_mode_info["zip_disabled"], "Actions must be disabled when 0 selected"
        assert selection_mode_info["equal_width"], "Create Album and Download ZIP must be equal width (2-column layout)"
        assert selection_mode_info["create_h"] >= 40 and selection_mode_info["zip_h"] >= 40, "Action buttons must be touch-friendly (>=40px)"

        # Select 2 photos
        d390.execute_script(f"""
            togglePhotoSelect('{records[0]["rel_path"]}', null);
            togglePhotoSelect('{records[1]["rel_path"]}', null);
        """)
        time.sleep(0.3)

        count_after = d390.execute_script("return document.getElementById('bulkSelectedCount').textContent.trim();")
        print("Count after 2 selects:", count_after)
        assert "2 selected" in count_after, f"Count should be '2 selected', got {count_after}"

        shot_390_sel = SCREENSHOTS_DIR / "mobile_390_selection_mode.png"
        d390.save_screenshot(str(shot_390_sel))
        print("Saved Screenshot: mobile_390_selection_mode.png")

        # Open Create Album modal from selection bar
        d390.find_element(By.ID, "bulkCreateAlbumBtn").click()
        time.sleep(0.5)

        modal_open = d390.execute_script("return document.getElementById('createAlbumModalBackdrop').style.display;")
        assert "flex" in modal_open, "Create album modal must open"

        # Cancel modal and verify selection is PRESERVED (closing modal does not exit selection mode)
        d390.find_element(By.ID, "btnCancelCreateAlbum").click()
        time.sleep(0.4)

        preservation_check = d390.execute_script("""
            const count = document.getElementById('bulkSelectedCount').textContent.trim();
            const navDisp = window.getComputedStyle(document.getElementById('mobileBottomNav')).display;
            const selActive = document.getElementById('bulkSelectionBar').classList.contains('active');
            return { count, nav_hidden: navDisp === 'none', sel_active: selActive };
        """)
        print("Preservation after cancel modal:", preservation_check)
        assert "2 selected" in preservation_check["count"], "Selection must be preserved after canceling modal"
        assert preservation_check["nav_hidden"] and preservation_check["sel_active"], "Selection mode must still be active"

        # Now click "Done" to exit selection mode and restore bottom nav
        d390.find_element(By.ID, "bulkCancelBtn").click()
        time.sleep(0.4)

        restored_check = d390.execute_script("""
            const navDisp = window.getComputedStyle(document.getElementById('mobileBottomNav')).display;
            const selActive = document.getElementById('bulkSelectionBar').classList.contains('active');
            const count = document.getElementById('bulkSelectedCount').textContent.trim();
            return { nav_restored: navDisp === 'flex', sel_inactive: !selActive, count };
        """)
        print("After Done clicked:", restored_check)
        assert restored_check["nav_restored"], "Bottom navigation must be restored after Done"
        assert restored_check["sel_inactive"], "Selection panel must be deactivated"

        # -----------------------------------------------------------------
        # CHECK 4: Filter Sheet from Bottom Nav
        # -----------------------------------------------------------------
        print("\n--- Check 4: Filter Sheet from Bottom Nav ---")
        d390.find_element(By.ID, "navBtnFilter").click()
        time.sleep(0.5)

        filter_info = d390.execute_script("""
            const sheet = document.getElementById('filterSheetBackdrop');
            return {
                hidden: sheet.hidden,
                active: sheet.classList.contains('active')
            };
        """)
        assert not filter_info["hidden"] and filter_info["active"], "Filter sheet must open from bottom nav"

        shot_filter = SCREENSHOTS_DIR / "mobile_390_filter_sheet.png"
        d390.save_screenshot(str(shot_filter))
        print("Saved Screenshot: mobile_390_filter_sheet.png")

        # Close filter sheet
        d390.find_element(By.ID, "closeFilterSheetBtn").click()
        time.sleep(0.4)

        d390.quit()

        # =====================================================================
        # CHECK 5: 430px Viewport (iPhone 14 Pro Max) — My Albums Light Modal
        # =====================================================================
        print("\n--- Check 5: 430px Viewport & Image 1 Light My Albums Verification ---")
        d430 = get_driver(430, 932, is_mobile=True)
        d430.get(SERVER_URL)
        time.sleep(1.0)
        inject_mock_data(d430, records, proofs["mobile_430"]["search_id"], proofs["mobile_430"]["token"])

        # Open My Albums via Bottom Nav
        d430.find_element(By.ID, "navBtnAlbums").click()
        time.sleep(0.5)

        # Inspect Light Modal styling matching Image 1
        modal_styles = d430.execute_script("""
            const modalCont = document.querySelector('#myAlbumsModalBackdrop .album-modal-container');
            const titleEl = modalCont.querySelector('h3');
            const subtitleEl = modalCont.querySelector('p');
            const closeBtn = document.getElementById('btnCloseMyAlbumsModal');

            const firstCard = document.querySelector('#myAlbumsListContainer > div');
            const cardTitle = firstCard.querySelector('h4');
            const cardBadge = firstCard.querySelector('span');
            const compactPill = firstCard.querySelector('.compact-link-pill');
            const copyBtn = firstCard.querySelector('.btn-copy-action');
            const waBtn = firstCard.querySelector('.btn-whatsapp-icon');
            const openBtn = firstCard.querySelector('a[title="Open album in new tab"]');
            const revokeBtn = firstCard.querySelector('.btn-revoke-album');

            const modalComp = window.getComputedStyle(modalCont);
            const cardComp = window.getComputedStyle(firstCard);
            const pillComp = window.getComputedStyle(compactPill);
            const openComp = window.getComputedStyle(openBtn);
            const revokeComp = window.getComputedStyle(revokeBtn);
            const badgeComp = window.getComputedStyle(cardBadge);

            return {
                modal_bg: modalComp.backgroundColor,
                title_color: window.getComputedStyle(titleEl).color,
                card_bg: cardComp.backgroundColor,
                card_title_color: window.getComputedStyle(cardTitle).color,
                badge_bg: badgeComp.backgroundColor,
                badge_color: badgeComp.color,
                pill_bg: pillComp.backgroundColor,
                open_color: openComp.color,
                open_border: openComp.borderColor,
                revoke_color: revokeComp.color,
                revoke_border: revokeComp.borderColor,
                has_wa_svg: !!waBtn.querySelector('svg'),
                wa_bg: window.getComputedStyle(waBtn).backgroundColor
            };
        """)
        print("Image 1 Light Modal Styles:", modal_styles)

        # 1. White modal surface (#ffffff)
        assert "rgb(255, 255, 255)" in modal_styles["modal_bg"], f"Modal background must be white, got {modal_styles['modal_bg']}"
        # 2. Dark navy titles (#0f172a = rgb(15, 23, 42))
        assert "rgb(15, 23, 42)" in modal_styles["title_color"], f"Title must be #0f172a, got {modal_styles['title_color']}"
        assert "rgb(15, 23, 42)" in modal_styles["card_title_color"], f"Card title must be #0f172a, got {modal_styles['card_title_color']}"
        # 3. Light card background (#f8fafc = rgb(248, 250, 252))
        assert "rgb(248, 250, 252)" in modal_styles["card_bg"], f"Card must be light #f8fafc, got {modal_styles['card_bg']}"
        # 4. Active status badge: soft green
        assert "rgb(220, 252, 231)" in modal_styles["badge_bg"], "Badge must be soft green #dcfce7"
        assert "rgb(21, 128, 61)" in modal_styles["badge_color"], "Badge text must be green #15803d"
        # 5. Open action: dark green text and border (#0f5c4d = rgb(15, 92, 77))
        assert "rgb(15, 92, 77)" in modal_styles["open_color"], f"Open button text must be #0f5c4d, got {modal_styles['open_color']}"
        # 6. Revoke action: red text and border (#dc2626 = rgb(220, 38, 38))
        assert "rgb(220, 38, 38)" in modal_styles["revoke_color"], f"Revoke button text must be #dc2626, got {modal_styles['revoke_color']}"
        # 7. WhatsApp official SVG
        assert modal_styles["has_wa_svg"], "WhatsApp button must have official SVG"

        shot_430_my_albums = SCREENSHOTS_DIR / "mobile_430_my_albums_light.png"
        d430.save_screenshot(str(shot_430_my_albums))
        print("Saved Screenshot: mobile_430_my_albums_light.png")

        d430.find_element(By.ID, "btnCloseMyAlbumsModal").click()
        d430.quit()

        # =====================================================================
        # CHECK 6: Desktop Viewport (1280x800) — Unchanged Functional Toolbar
        # =====================================================================
        print("\n--- Check 6: Desktop Viewport (1280x800) ---")
        ddesk = get_driver(1280, 800, is_mobile=False)
        ddesk.get(SERVER_URL)
        time.sleep(1.0)
        inject_mock_data(ddesk, records, proofs["desktop"]["search_id"], proofs["desktop"]["token"])

        desk_info = ddesk.execute_script("""
            const nav = document.getElementById('mobileBottomNav');
            const openMyAlbums = document.getElementById('openMyAlbumsBtn');
            const toggleSelect = document.getElementById('toggleSelectBtn');
            const closePhotos = document.getElementById('closePhotosBtn');
            const discovery = document.getElementById('discoveryToolbar');

            return {
                nav_hidden: window.getComputedStyle(nav).display === 'none',
                open_my_albums_visible: window.getComputedStyle(openMyAlbums).display !== 'none',
                toggle_select_visible: window.getComputedStyle(toggleSelect).display !== 'none',
                close_photos_visible: window.getComputedStyle(closePhotos).display !== 'none',
                discovery_visible: window.getComputedStyle(discovery).display !== 'none'
            };
        """)
        print("Desktop Toolbar Info:", desk_info)
        assert desk_info["nav_hidden"], "Mobile bottom nav must be hidden on desktop"
        assert desk_info["open_my_albums_visible"], "My Albums button must be visible in desktop header"
        assert desk_info["toggle_select_visible"], "Select button must be visible in desktop header"
        assert desk_info["close_photos_visible"], "New Search button must be visible in desktop header"

        shot_desk = SCREENSHOTS_DIR / "desktop_functional_toolbar.png"
        ddesk.save_screenshot(str(shot_desk))
        print("Saved Screenshot: desktop_functional_toolbar.png")
        ddesk.quit()

        print("\n=======================================================")
        print("  ALL 6 MOBILE & DESKTOP REGRESSION CHECKS PASSED!     ")
        print("=======================================================")

    finally:
        pass

if __name__ == "__main__":
    run_all_tests()
    os._exit(0)
