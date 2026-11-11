import os
import sys
import time
import json
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

def run():
    options = Options()
    options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    # Enable browser console logging
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    driver = webdriver.Chrome(options=options)
    try:
        url = "https://photos.bhashyamramakrishna.in/"
        print(f"Loading {url}...")
        driver.get(url)
        time.sleep(2)

        # Inspect initial state
        print("Page title:", driver.title)

        # Simulate saved profile with matched search results like the user screenshot:
        # Title: Photos with BRK Sir, 180 photographs found.
        # Two photos selected.
        setup_script = """
        const samplePhotos = [
            { id: 101, rel_path: "events/2026-11-10/photo1.jpg", filename: "photo1.jpg" },
            { id: 102, rel_path: "events/2026-11-10/photo2.jpg", filename: "photo2.jpg" },
            { id: 103, rel_path: "events/2026-11-10/photo3.jpg", filename: "photo3.jpg" }
        ];
        localStorage.setItem('brk_saved_photos', JSON.stringify(samplePhotos));
        localStorage.setItem('brk_saved_count', '180');
        localStorage.setItem('brk_saved_face_thumb', 'data:image/jpeg;base64,/9j/4AAQSkZJRg==');
        // Test case 1: with search_id and creation_token
        localStorage.setItem('brk_current_search_id', 'srch_test123');
        localStorage.setItem('brk_current_creation_token', 'cr_test123');

        // Open saved photos view
        viewSavedPhotos();

        // Turn on selection mode and select 2 photos
        toggleSelectionMode(true);
        togglePhotoSelect("events/2026-11-10/photo1.jpg", null);
        togglePhotoSelect("events/2026-11-10/photo2.jpg", null);
        """
        driver.execute_script(setup_script)
        time.sleep(1)

        # Check bulk selection bar
        bar_active = driver.execute_script("return document.getElementById('bulkSelectionBar').classList.contains('active');")
        count_text = driver.execute_script("return document.getElementById('bulkSelectedCount').textContent;")
        print(f"Bulk bar active: {bar_active}, count text: {count_text}")

        # Check modal before click
        modal_style_before = driver.execute_script("""
            const el = document.getElementById('createAlbumModalBackdrop');
            const comp = window.getComputedStyle(el);
            return {
                display: comp.display,
                opacity: comp.opacity,
                pointerEvents: comp.pointerEvents,
                zIndex: comp.zIndex,
                visibility: comp.visibility
            };
        """)
        print("Modal computed style before click:", modal_style_before)

        # Now click Create Album button
        print("Clicking bulkCreateAlbumBtn...")
        album_btn = driver.find_element(By.ID, "bulkCreateAlbumBtn")
        album_btn.click()
        time.sleep(1)

        # Check modal after click
        modal_style_after = driver.execute_script("""
            const el = document.getElementById('createAlbumModalBackdrop');
            const comp = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            const child = el.firstElementChild;
            const childComp = child ? window.getComputedStyle(child) : null;
            return {
                backdrop_display: comp.display,
                backdrop_opacity: comp.opacity,
                backdrop_pointerEvents: comp.pointerEvents,
                backdrop_zIndex: comp.zIndex,
                backdrop_visibility: comp.visibility,
                rect: { top: rect.top, left: rect.left, width: rect.width, height: rect.height },
                child_display: childComp ? childComp.display : null,
                child_opacity: childComp ? childComp.opacity : null,
                classes: el.className
            };
        """)
        print("Modal computed style after click:", json.dumps(modal_style_after, indent=2))

        # Check what element is at center of screen
        top_el_at_center = driver.execute_script("""
            const cx = window.innerWidth / 2;
            const cy = window.innerHeight / 2;
            const el = document.elementFromPoint(cx, cy);
            return {
                tagName: el ? el.tagName : null,
                id: el ? el.id : null,
                className: el ? el.className : null
            };
        """)
        print("Element at center of screen:", top_el_at_center)

        # Check browser console logs
        logs = driver.get_log("browser")
        print(f"Browser console logs ({len(logs)} entries):")
        for log in logs:
            print("  ", log["level"], log["message"])

        # Take screenshot of reproduced state
        shot_path = "/Users/venkatavivek/.gemini/antigravity/brain/64149011-e7dc-4646-853f-dbda7591c5f4/screenshots/reproduced_click_state.png"
        driver.save_screenshot(shot_path)
        print("Saved screenshot to:", shot_path)

    finally:
        driver.quit()

if __name__ == "__main__":
    run()

