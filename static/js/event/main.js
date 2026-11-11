    const $ = id => document.getElementById(id);
    const slug = window.location.pathname.replace(/^\/|\/$/g, '') || 'brk-sir';
    let eventData = null;
    let currentPhotosList = [];
    let currentPhotoIndex = 0;

    // --- Privacy-Preserving Analytics Telemetry ---
    function getVisitorId() {
      try {
        let v = localStorage.getItem('brk_visitor_id');
        if (!v) {
          v = 'v_' + Math.random().toString(36).substring(2, 10) + Date.now().toString(36);
          localStorage.setItem('brk_visitor_id', v);
        }
        return v;
      } catch (e) {
        return 'v_anon';
      }
    }

    function getSessionId() {
      try {
        let s = sessionStorage.getItem('brk_session_id');
        if (!s) {
          s = 's_' + Math.random().toString(36).substring(2, 10) + Date.now().toString(36);
          sessionStorage.setItem('brk_session_id', s);
        }
        return s;
      } catch (e) {
        return 's_anon';
      }
    }

    function getDeviceType() {
      const w = window.innerWidth;
      const ua = navigator.userAgent || '';
      if (/Mobi|Android|iPhone/i.test(ua) || w <= 640) return 'mobile';
      if (/iPad|Tablet/i.test(ua) || (w > 640 && w <= 1024)) return 'tablet';
      return 'desktop';
    }

    function emitAnalyticsEvent(eventType, extra = {}) {
      try {
        const payload = {
          event_type: eventType,
          session_id: getSessionId(),
          visitor_id: getVisitorId(),
          device_type: getDeviceType(),
          event_slug: slug || 'brk-sir',
          ...extra
        };
        fetch('/api/analytics/event', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          keepalive: true
        }).catch(() => {});
      } catch (e) {}
    }

    // Portal visit tracking (guarded against multiple rapid reloads in same session)
    (function trackVisit() {
      try {
        const lastVisit = sessionStorage.getItem('brk_last_visit_ts');
        const now = Date.now();
        if (!lastVisit || (now - parseInt(lastVisit, 10)) > 120000) {
          sessionStorage.setItem('brk_last_visit_ts', now.toString());
          emitAnalyticsEvent('portal_visit');
        }
      } catch (e) {}
    })();

    // Genuine Photo View Debouncing (only count when user views photo for >= 1 second)
    let photoViewDebounceTimer = null;
    const viewedPhotosInSession = new Set();

    function trackGenuinePhotoView(item) {
      if (!item) return;
      if (photoViewDebounceTimer) clearTimeout(photoViewDebounceTimer);
      const photoPath = typeof item === 'string' ? item : (item.rel_path || item.filename || '');
      if (!photoPath) return;

      photoViewDebounceTimer = setTimeout(() => {
        if (!viewedPhotosInSession.has(photoPath)) {
          viewedPhotosInSession.add(photoPath);
          const parsed = (typeof parsePhotoDate === 'function') ? parsePhotoDate(item) : {};
          emitAnalyticsEvent('photo_opened', {
            photo_path: photoPath,
            album_name: parsed.title || ''
          });
        }
      }, 1000); // 1.0 second dwell time
    }

    async function loadEvent() {
      try {
        let res = await fetch('/api/public/gallery');
        if (!res.ok) res = await fetch(`/api/public/events/${slug}`);
        if (res.ok) {
          eventData = await res.json();
          if (eventData && eventData.event && eventData.event.name) {
            document.title = `${eventData.event.name} | Bhashyam Ramakrishna Garu, MP Rajya Sabha`;
          }
        }
      } catch (err) {
        document.title = 'Photo with BRK Sir | Bhashyam Ramakrishna Garu, MP Rajya Sabha';
      }
    }

    let gridRenderQueue = [];
    let gridQueueCursor = 0;
    let gridScrollObserver = null;
    let isRenderingBatch = false;
    const GRID_BATCH_SIZE = 40;

    // Gallery Discovery, Album Filters & Sorting State
    let rawPhotosList = [];
    let totalMatchingPhotosCount = 0;
    let sortOrder = 'newest'; // 'newest' | 'oldest'

    // Applied Filters State
    let selectedCalendarDate = null; // 'YYYY-MM-DD'
    let selectedOtherAlbum = null; // rawAlbum string e.g. 'PART 01'
    let serverDateSummary = null; // Server aggregated date summary across all matches
    const datePhotoCounts = new Map(); // 'YYYY-MM-DD' -> count
    const otherAlbumCounts = new Map(); // rawAlbum -> count
    let newestCalendarDate = null; // 'YYYY-MM-DD'

    // History and Scroll State
    let isClosingLightboxViaCode = false;
    let lastGalleryScrollTop = 0;

    // Draft State for Filter Bottom Sheet (allows cancel / tap-away without altering active filters)
    let draftCalendarDate = null;
    let draftOtherAlbum = null;
    let draftSortOrder = 'newest';
    let sheetViewYear = 2026;
    let sheetViewMonth = 10;
    let hasUserNavigatedSheetMonth = false;

    // Selection Mode State
    let isSelectionMode = false;
    const selectedPhotos = new Set();
    let toastTimeout = null;

    // Central UI-State Update Function for Mobile Navigation & Selection Bars
    function updateMobileNavigationState() {
      const galleryOverlay = $('photosSection');
      const isGalleryOpen = !!(galleryOverlay && !galleryOverlay.hidden && window.getComputedStyle(galleryOverlay).display !== 'none');
      const isMobile = window.innerWidth <= 767;
      const nav = $('mobileBottomNav');
      const bar = $('bulkSelectionBar');

      if (!isGalleryOpen) {
        // Gallery is closed (homepage, selfie capture, tips, etc.)
        if (nav) {
          nav.hidden = true;
          nav.style.display = 'none';
        }
        if (bar) {
          bar.hidden = true;
          bar.style.display = 'none';
          bar.classList.remove('active');
        }
        return;
      }

      // Gallery is open
      if (isMobile) {
        if (isSelectionMode) {
          // Contextual Selection Panel replaces Bottom Nav on Mobile
          if (nav) {
            nav.hidden = true;
            nav.style.display = 'none';
          }
          if (bar) {
            bar.hidden = false;
            bar.style.display = 'flex';
            bar.classList.add('active');
          }
        } else {
          // Normal Mobile Gallery Browsing Mode
          if (nav) {
            nav.hidden = false;
            nav.style.display = 'flex';
          }
          if (bar) {
            bar.classList.remove('active');
            bar.style.display = 'none';
            bar.hidden = true;
          }
        }
      } else {
        // Desktop gallery mode: mobile bottom nav is ALWAYS hidden
        if (nav) {
          nav.hidden = true;
          nav.style.display = 'none';
        }
        if (isSelectionMode) {
          if (bar) {
            bar.hidden = false;
            bar.style.display = 'flex';
            bar.classList.add('active');
          }
        } else {
          if (bar) {
            bar.classList.remove('active');
            bar.style.display = 'none';
            bar.hidden = true;
          }
        }
      }
    }

    function showToast(msg) {
      const toast = $('galleryToast');
      if (!toast) return;
      const isMob = window.innerWidth < 768;
      const isGalleryOpen = !$('photosSection')?.hidden;
      const bar = $('bulkSelectionBar');
      if (isGalleryOpen && isMob && bar && bar.classList.contains('active')) {
        toast.style.bottom = 'calc(max(1rem, env(safe-area-inset-bottom)) + 120px)';
      } else if (isGalleryOpen && isMob) {
        toast.style.bottom = 'calc(max(0.75rem, env(safe-area-inset-bottom)) + 65px)';
      } else {
        toast.style.bottom = 'max(24px, env(safe-area-inset-bottom))';
      }
      toast.textContent = msg;
      toast.classList.add('show');
      if (toastTimeout) clearTimeout(toastTimeout);
      toastTimeout = setTimeout(() => {
        toast.classList.remove('show');
      }, 3200);
    }

    function toggleSelectionMode(force) {
      isSelectionMode = typeof force === 'boolean' ? force : !isSelectionMode;
      const toggleBtn = $('toggleSelectBtn');
      const toggleText = $('toggleSelectText');

      if (toggleText) {
        toggleText.textContent = isSelectionMode ? 'Done' : 'Select';
      }
      if (toggleBtn) {
        toggleBtn.classList.toggle('active-mode', isSelectionMode);
        toggleBtn.style.background = '';
        toggleBtn.style.color = '';
      }

      document.querySelectorAll('.photo-card').forEach(c => {
        c.classList.toggle('selection-active', isSelectionMode);
        const chk = c.querySelector('.photo-select-checkbox');
        if (chk) chk.style.display = isSelectionMode ? 'flex' : 'none';
      });

      updateBulkBar();
      updateMobileNavigationState();
    }

    function togglePhotoSelect(path, cardEl) {
      if (selectedPhotos.has(path)) {
        selectedPhotos.delete(path);
        if (cardEl) cardEl.classList.remove('selected');
      } else {
        selectedPhotos.add(path);
        if (cardEl) cardEl.classList.add('selected');
      }
      updateBulkBar();
    }

    function updateBulkBar() {
      const countEl = $('bulkSelectedCount');
      const zipBtn = $('bulkDownloadZipBtn');
      const albumBtn = $('bulkCreateAlbumBtn');
      const selectAllBtn = $('bulkSelectAllBtn');
      const count = selectedPhotos.size;

      if (countEl) {
        countEl.textContent = `${count} selected`;
      }
      if (zipBtn) {
        zipBtn.disabled = count === 0;
        zipBtn.style.opacity = count === 0 ? '0.5' : '1';
        zipBtn.style.pointerEvents = count === 0 ? 'none' : 'auto';
      }
      if (albumBtn) {
        albumBtn.disabled = count === 0;
        albumBtn.style.opacity = count === 0 ? '0.5' : '1';
        albumBtn.style.pointerEvents = count === 0 ? 'none' : 'auto';
      }
      if (selectAllBtn) {
        const allPaths = currentPhotosList.map(item => typeof item === 'string' ? item : item.rel_path);
        const isAllSelected = allPaths.length > 0 && allPaths.every(p => selectedPhotos.has(p));
        selectAllBtn.textContent = isAllSelected ? 'Deselect All' : 'Select All';
      }
    }

    function selectAllPhotos() {
      const allPaths = currentPhotosList.map(item => typeof item === 'string' ? item : item.rel_path);
      const isAllSelected = allPaths.length > 0 && allPaths.every(p => selectedPhotos.has(p));

      if (isAllSelected) {
        allPaths.forEach(p => selectedPhotos.delete(p));
        document.querySelectorAll('.photo-card').forEach(c => c.classList.remove('selected'));
      } else {
        allPaths.forEach(p => selectedPhotos.add(p));
        document.querySelectorAll('.photo-card').forEach(c => c.classList.add('selected'));
      }
      updateBulkBar();
    }

    function clearSelection() {
      selectedPhotos.clear();
      document.querySelectorAll('.photo-card').forEach(c => {
        c.classList.remove('selected', 'selection-active');
        const chk = c.querySelector('.photo-select-checkbox');
        if (chk) chk.style.display = 'none';
      });
      toggleSelectionMode(false);
    }

    async function downloadSelectedZip() {
      if (selectedPhotos.size === 0) {
        showToast('Please select at least 1 photo.');
        return;
      }

      const zipText = $('bulkDownloadZipText');
      const originalText = zipText ? zipText.textContent : 'Download ZIP';
      if (zipText) zipText.textContent = 'Preparing ZIP...';

      try {
        const payload = {
          photos: Array.from(selectedPhotos),
          quality: 'original',
          session_id: getSessionId(),
          visitor_id: getVisitorId()
        };
        const res = await fetch('/api/download/bulk-zip', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || 'Could not create ZIP archive.');
        }

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = `BRK_Photos_${selectedPhotos.size}_Original_Photos.zip`;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
          window.URL.revokeObjectURL(url);
          a.remove();
        }, 1500);

        showToast(`Downloaded ${selectedPhotos.size} photos as ZIP!`);
      } catch (err) {
        showToast(err.message || 'Error downloading ZIP archive.');
      } finally {
        if (zipText) zipText.textContent = originalText;
      }
    }

    const MONTH_NAMES = [
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    const FULL_MONTH_NAMES = [
      'January', 'February', 'March', 'April', 'May', 'June',
      'July', 'August', 'September', 'October', 'November', 'December'
    ];

    function normalizeBaseFilename(fname) {
      if (!fname || typeof fname !== 'string') return '';
      const base = fname.split('/').pop().toLowerCase().trim();
      const lastDot = base.lastIndexOf('.');
      const stem = lastDot > 0 ? base.substring(0, lastDot) : base;
      return stem
        .replace(/[\s_]*\((?:[0-9]+|copy(?:\s+[0-9]+)?)\)$/i, '')
        .replace(/[\s_-]+copy(?:[\s_-]*[0-9]+|\([0-9]+\))?$/i, '')
        .trim();
    }

    function parseDateString(text) {
      if (!text || typeof text !== 'string') return null;
      const s = text.trim();
      if (!s) return null;

      // 1. DD-MM-YYYY or DD-MM-YY (e.g. "28-10-2026", "28/10/26", "28.10.2026")
      const mDmy = s.match(/\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2}|\d{2})\b/);
      if (mDmy) {
        const d = parseInt(mDmy[1], 10);
        const m = parseInt(mDmy[2], 10);
        let y = parseInt(mDmy[3], 10);
        if (y < 100) y += 2000;
        if (m >= 1 && m <= 12 && d >= 1 && d <= 31) {
          const dt = new Date(y, m - 1, d);
          if (!isNaN(dt.getTime()) && dt.getDate() === d) {
            return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
          }
        }
      }

      // 2. YYYY-MM-DD or YYYY/MM/DD
      const mYmd = s.match(/\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b/);
      if (mYmd) {
        const y = parseInt(mYmd[1], 10);
        const m = parseInt(mYmd[2], 10);
        const d = parseInt(mYmd[3], 10);
        if (m >= 1 && m <= 12 && d >= 1 && d <= 31) {
          const dt = new Date(y, m - 1, d);
          if (!isNaN(dt.getTime()) && dt.getDate() === d) {
            return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
          }
        }
      }

      // 3. DD Mon YYYY (e.g. "28 Oct 2026", "28 October 2026")
      const mText1 = s.match(/\b(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2}|\d{2})\b/);
      if (mText1) {
        const d = parseInt(mText1[1], 10);
        const monStr = mText1[2].toLowerCase().substring(0, 3);
        let y = parseInt(mText1[3], 10);
        if (y < 100) y += 2000;
        const mIdx = MONTH_NAMES.findIndex(mon => mon.toLowerCase() === monStr);
        if (mIdx !== -1 && d >= 1 && d <= 31) {
          const dt = new Date(y, mIdx, d);
          if (!isNaN(dt.getTime()) && dt.getDate() === d) {
            return `${y}-${String(mIdx + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
          }
        }
      }

      // 4. Mon DD, YYYY (e.g. "Oct 28, 2026")
      const mText2 = s.match(/\b([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d{2}|\d{2})\b/);
      if (mText2) {
        const monStr = mText2[1].toLowerCase().substring(0, 3);
        const d = parseInt(mText2[2], 10);
        let y = parseInt(mText2[3], 10);
        if (y < 100) y += 2000;
        const mIdx = MONTH_NAMES.findIndex(mon => mon.toLowerCase() === monStr);
        if (mIdx !== -1 && d >= 1 && d <= 31) {
          const dt = new Date(y, mIdx, d);
          if (!isNaN(dt.getTime()) && dt.getDate() === d) {
            return `${y}-${String(mIdx + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
          }
        }
      }

      // 5. YYYYMMDD in filenames (e.g. IMG_20261028_123456)
      const mCam = s.match(/\b(20\d{2})(\d{2})(\d{2})[_-]\d{4,6}\b/);
      if (mCam) {
        const y = parseInt(mCam[1], 10);
        const m = parseInt(mCam[2], 10);
        const d = parseInt(mCam[3], 10);
        if (m >= 1 && m <= 12 && d >= 1 && d <= 31) {
          const dt = new Date(y, m - 1, d);
          if (!isNaN(dt.getTime()) && dt.getDate() === d) {
            return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
          }
        }
      }

      return null;
    }

    function parsePhotoDate(item) {
      let album = '';
      let event = '';
      let dateStr = '';
      let relPath = '';
      let filename = '';

      if (typeof item === 'object' && item !== null) {
        album = (item.album_name || item.album_slug || '').trim();
        event = (item.event_name || '').trim();
        dateStr = (item.captured_at || '').trim();
        relPath = (item.rel_path || '').trim();
        filename = (item.filename || '').trim();
      } else if (typeof item === 'string') {
        relPath = item.trim();
        const parts = relPath.split('/');
        filename = parts[parts.length - 1] || '';
        if (parts.length > 2) {
          album = parts[parts.length - 2].trim();
          event = parts[parts.length - 3].trim();
        } else if (parts.length > 1) {
          album = parts[parts.length - 2].trim();
        }
      }

      // 1. Check EXIF capture date
      let dKey = parseDateString(dateStr);

      // 2. Extract date from album name or slug
      if (!dKey && album) {
        dKey = parseDateString(album);
      }

      // 3. Extract date from rel_path folder
      if (!dKey && relPath) {
        dKey = parseDateString(relPath);
      }

      // 4. Extract date from filename
      if (!dKey && filename) {
        dKey = parseDateString(filename);
      }

      if (dKey) {
        const [y, m, d] = dKey.split('-').map(v => parseInt(v, 10));
        const dt = new Date(y, m - 1, d);
        const dayPad = String(d).padStart(2, '0');
        const monthPad = String(m).padStart(2, '0');
        return {
          isDate: true,
          key: dKey,
          dateKey: dKey,
          year: y,
          month: m - 1,
          day: d,
          sortTime: dt.getTime(),
          title: `${dayPad} ${MONTH_NAMES[m - 1]} ${y}`,
          rawAlbum: album || dKey
        };
      }

      // Fallback to non-date album title or event name
      const fallback = album || event || 'Celebration Photos';
      return {
        isDate: false,
        key: fallback,
        dateKey: null,
        year: null,
        month: null,
        day: null,
        sortTime: 0,
        title: fallback,
        rawAlbum: fallback
      };
    }

    function updateCalendarAndFilters() {
      const toolbar = $('discoveryToolbar');
      if (!toolbar) return;

      if (!rawPhotosList || rawPhotosList.length === 0) {
        toolbar.hidden = true;
        return;
      }
      toolbar.hidden = false;

      datePhotoCounts.clear();
      otherAlbumCounts.clear();

      if (serverDateSummary && serverDateSummary.date_counts) {
        // Hydrate from complete server-side aggregation across all matches
        for (const [dk, count] of Object.entries(serverDateSummary.date_counts)) {
          datePhotoCounts.set(dk, Number(count) || 0);
        }
        if (serverDateSummary.other_albums) {
          for (const [alb, count] of Object.entries(serverDateSummary.other_albums)) {
            otherAlbumCounts.set(alb, Number(count) || 0);
          }
        }
        newestCalendarDate = serverDateSummary.newest_date || null;
      } else {
        // Fallback to client-side iteration over current loaded items
        let latestDateKey = null;
        let latestSortTime = -1;

        for (const item of rawPhotosList) {
          const parsed = parsePhotoDate(item);
          if (parsed.isDate && parsed.dateKey) {
            const count = (datePhotoCounts.get(parsed.dateKey) || 0) + 1;
            datePhotoCounts.set(parsed.dateKey, count);
            if (parsed.sortTime > latestSortTime) {
              latestSortTime = parsed.sortTime;
              latestDateKey = parsed.dateKey;
            }
          } else {
            const count = (otherAlbumCounts.get(parsed.rawAlbum) || 0) + 1;
            otherAlbumCounts.set(parsed.rawAlbum, count);
          }
        }
        newestCalendarDate = latestDateKey;
      }

      if (!hasUserNavigatedSheetMonth && newestCalendarDate) {
        const parts = newestCalendarDate.split('-');
        sheetViewYear = parseInt(parts[0], 10);
        sheetViewMonth = parseInt(parts[1], 10) - 1;
      }

      updateMainFilterControls();

      // If bottom sheet is currently open, refresh its content
      const backdrop = $('filterSheetBackdrop');
      if (backdrop && !backdrop.hidden && backdrop.classList.contains('active')) {
        renderSheetContent();
      }
    }

    function updateMainFilterControls() {
      const allCount = (totalMatchingPhotosCount > 0) ? totalMatchingPhotosCount : rawPhotosList.length;
      const allChip = $('allPhotosChip');
      const allChipCount = $('allPhotosChipCount');
      if (allChipCount) allChipCount.textContent = `(${allCount})`;
      if (allChip) allChip.classList.toggle('active', !selectedCalendarDate && !selectedOtherAlbum);

      const filterBtn = $('openFilterSheetBtn');
      const badge = $('filterActiveBadge');
      const pill = $('activeFilterPill');
      const pillText = $('activeFilterPillText');

      const isFiltered = !!(selectedCalendarDate || selectedOtherAlbum);
      if (filterBtn) filterBtn.classList.toggle('has-filter', isFiltered);
      if (badge) badge.hidden = !isFiltered;

      const navFilterBadge = $('navFilterBadge');
      if (navFilterBadge) navFilterBadge.style.display = isFiltered ? 'inline-block' : 'none';

      const mobPill = $('mobileActiveFilterPill');
      const mobPillText = $('mobileActiveFilterPillText');
      if (mobPill && mobPillText) {
        if (selectedCalendarDate) {
          mobPill.hidden = false;
          const parts = selectedCalendarDate.split('-');
          const m = parseInt(parts[1], 10) - 1;
          const d = parseInt(parts[2], 10);
          const count = datePhotoCounts.get(selectedCalendarDate) || 0;
          mobPillText.textContent = `${String(d).padStart(2, '0')} ${MONTH_NAMES[m]} (${count})`;
        } else if (selectedOtherAlbum) {
          mobPill.hidden = false;
          const count = otherAlbumCounts.get(selectedOtherAlbum) || 0;
          mobPillText.textContent = `${selectedOtherAlbum} (${count})`;
        } else {
          mobPill.hidden = true;
        }
      }

      if (pill && pillText) {
        if (selectedCalendarDate) {
          pill.hidden = false;
          const parts = selectedCalendarDate.split('-');
          const y = parseInt(parts[0], 10);
          const m = parseInt(parts[1], 10) - 1;
          const d = parseInt(parts[2], 10);
          const count = datePhotoCounts.get(selectedCalendarDate) || 0;
          pillText.textContent = `${String(d).padStart(2, '0')} ${MONTH_NAMES[m]} (${count})`;
          pill.title = `Filtered by date: ${String(d).padStart(2, '0')} ${MONTH_NAMES[m]} ${y}`;
        } else if (selectedOtherAlbum) {
          pill.hidden = false;
          const count = otherAlbumCounts.get(selectedOtherAlbum) || 0;
          pillText.textContent = `${selectedOtherAlbum} (${count})`;
          pill.title = `Filtered by album: ${selectedOtherAlbum}`;
        } else {
          pill.hidden = true;
        }
      }

      updateProgressText();
    }

    function openFilterSheet() {
      // Sync draft state with currently applied state
      draftCalendarDate = selectedCalendarDate;
      draftOtherAlbum = selectedOtherAlbum;
      draftSortOrder = sortOrder;

      if (selectedCalendarDate) {
        const parts = selectedCalendarDate.split('-');
        sheetViewYear = parseInt(parts[0], 10);
        sheetViewMonth = parseInt(parts[1], 10) - 1;
      } else if (!hasUserNavigatedSheetMonth && newestCalendarDate) {
        const parts = newestCalendarDate.split('-');
        sheetViewYear = parseInt(parts[0], 10);
        sheetViewMonth = parseInt(parts[1], 10) - 1;
      }

      renderSheetContent();

      const backdrop = $('filterSheetBackdrop');
      const container = $('filterSheetContainer');
      if (!backdrop || !container) return;

      container.style.transform = '';
      backdrop.hidden = false;
      requestAnimationFrame(() => {
        backdrop.classList.add('active');
      });
      document.body.style.overflow = 'hidden';
    }

    function closeFilterSheet() {
      const backdrop = $('filterSheetBackdrop');
      if (!backdrop || backdrop.hidden) return;

      backdrop.classList.remove('active');
      setTimeout(() => {
        backdrop.hidden = true;
      }, 230);

      // Restore scrolling if gallery overlay is hidden
      const galleryOverlay = $('photosSection');
      if (!galleryOverlay || galleryOverlay.hidden) {
        document.body.style.overflow = '';
      }
    }

    function renderSheetContent() {
      const titleEl = $('calMonthYearTitle');
      const gridEl = $('calendarDaysGrid');
      if (!titleEl || !gridEl) return;

      titleEl.textContent = `${FULL_MONTH_NAMES[sheetViewMonth]} ${sheetViewYear}`;
      gridEl.replaceChildren();

      // Weekday offset for 1st day of active sheet month: 0 = Sun .. 6 = Sat
      const firstDayOfWeek = new Date(sheetViewYear, sheetViewMonth, 1).getDay();
      const daysInMonth = new Date(sheetViewYear, sheetViewMonth + 1, 0).getDate();

      for (let i = 0; i < firstDayOfWeek; i++) {
        const emptyCell = document.createElement('div');
        emptyCell.className = 'calendar-day-cell empty';
        gridEl.appendChild(emptyCell);
      }

      const monthPad = String(sheetViewMonth + 1).padStart(2, '0');

      for (let day = 1; day <= daysInMonth; day++) {
        const dayPad = String(day).padStart(2, '0');
        const dateKey = `${sheetViewYear}-${monthPad}-${dayPad}`;
        const count = datePhotoCounts.get(dateKey) || 0;
        const hasPhotos = count > 0;
        const isSelected = draftCalendarDate === dateKey;

        const cell = document.createElement('button');
        cell.type = 'button';
        cell.className = 'calendar-day-cell' + (hasPhotos ? ' has-photos' : ' muted') + (isSelected ? ' selected' : '');
        cell.innerHTML = `<span>${day}</span>${hasPhotos ? '<span class="calendar-day-dot"></span>' : ''}`;

        if (hasPhotos) {
          cell.title = `${dayPad} ${MONTH_NAMES[sheetViewMonth]} ${sheetViewYear} (${count} ${count === 1 ? 'photo' : 'photos'})`;
          cell.addEventListener('click', () => {
            toggleDraftCalendarDate(dateKey);
          });
        }

        gridEl.appendChild(cell);
      }

      // Enforce strictly invariant 42 cells (6 rows x 7 cols) so switching months never jumps or shifts layout
      const totalRendered = firstDayOfWeek + daysInMonth;
      const trailingEmpty = Math.max(0, 42 - totalRendered);
      for (let i = 0; i < trailingEmpty; i++) {
        const emptyCell = document.createElement('div');
        emptyCell.className = 'calendar-day-cell empty';
        gridEl.appendChild(emptyCell);
      }

      // Render Other Albums Section
      const otherSection = $('sheetOtherAlbumsSection');
      const otherBadge = $('sheetOtherAlbumsCountBadge');
      const otherContainer = $('otherAlbumsChipsContainer');

      if (otherSection && otherBadge && otherContainer) {
        if (otherAlbumCounts.size > 0) {
          otherSection.hidden = false;
          otherBadge.textContent = `(${otherAlbumCounts.size})`;
          otherContainer.replaceChildren();

          const sortedAlbums = Array.from(otherAlbumCounts.entries()).sort((a, b) => {
            return b[1] - a[1] || a[0].localeCompare(b[0]);
          });

          for (const [alb, count] of sortedAlbums) {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'filter-chip' + (draftOtherAlbum === alb ? ' active' : '');
            chip.innerHTML = `<span>${alb}</span> <span>(${count})</span>`;
            chip.addEventListener('click', () => {
              toggleDraftOtherAlbum(alb);
            });
            otherContainer.appendChild(chip);
          }
        } else {
          otherSection.hidden = true;
        }
      }

      // Update Draft Active Banner in Sheet
      const banner = $('sheetActiveFilterBanner');
      const label = $('sheetActiveFilterLabel');
      if (banner && label) {
        if (draftCalendarDate) {
          banner.hidden = false;
          const parts = draftCalendarDate.split('-');
          const y = parseInt(parts[0], 10);
          const m = parseInt(parts[1], 10) - 1;
          const d = parseInt(parts[2], 10);
          const count = datePhotoCounts.get(draftCalendarDate) || 0;
          const formatted = `${String(d).padStart(2, '0')} ${MONTH_NAMES[m]} ${y}`;
          label.innerHTML = `Selected: <strong>${formatted}</strong> (${count} ${count === 1 ? 'photo' : 'photos'})`;
        } else if (draftOtherAlbum) {
          banner.hidden = false;
          const count = otherAlbumCounts.get(draftOtherAlbum) || 0;
          label.innerHTML = `Selected: <strong>${draftOtherAlbum}</strong> (${count} ${count === 1 ? 'photo' : 'photos'})`;
        } else {
          banner.hidden = true;
        }
      }

      // Update Sort Segmented Buttons in Sheet
      const newestBtn = $('sheetSortNewestBtn');
      const oldestBtn = $('sheetSortOldestBtn');
      if (newestBtn && oldestBtn) {
        newestBtn.classList.toggle('active', draftSortOrder === 'newest');
        oldestBtn.classList.toggle('active', draftSortOrder === 'oldest');
      }

      // Update "Show X Photos" Button
      const applyBtnText = $('sheetApplyBtnText');
      if (applyBtnText) {
        if (draftCalendarDate) {
          const count = datePhotoCounts.get(draftCalendarDate) || 0;
          applyBtnText.textContent = `Show ${count} ${count === 1 ? 'Photo' : 'Photos'}`;
        } else if (draftOtherAlbum) {
          const count = otherAlbumCounts.get(draftOtherAlbum) || 0;
          applyBtnText.textContent = `Show ${count} ${count === 1 ? 'Photo' : 'Photos'}`;
        } else {
          const total = (totalMatchingPhotosCount > 0) ? totalMatchingPhotosCount : rawPhotosList.length;
          applyBtnText.textContent = `Show All ${total} Photos`;
        }
      }
    }

    function toggleDraftCalendarDate(dateKey) {
      if (draftCalendarDate === dateKey) {
        draftCalendarDate = null;
      } else {
        draftCalendarDate = dateKey;
        draftOtherAlbum = null;
      }
      renderSheetContent();
    }

    function toggleDraftOtherAlbum(albumName) {
      if (draftOtherAlbum === albumName) {
        draftOtherAlbum = null;
      } else {
        draftOtherAlbum = albumName;
        draftCalendarDate = null;
      }
      renderSheetContent();
    }

    function clearSheetDraftFilters() {
      draftCalendarDate = null;
      draftOtherAlbum = null;
      draftSortOrder = 'newest';
      renderSheetContent();
    }

    function applySheetFilters() {
      sortOrder = draftSortOrder;
      selectedCalendarDate = draftCalendarDate;
      selectedOtherAlbum = draftOtherAlbum;
      closeFilterSheet();
      applyPhotoFiltersAndSort();
      updateMainFilterControls();
    }

    function applyPhotoFiltersAndSort() {
      let filtered = [...rawPhotosList];

      if (selectedCalendarDate) {
        filtered = filtered.filter(item => {
          const parsed = parsePhotoDate(item);
          return parsed.isDate && parsed.dateKey === selectedCalendarDate;
        });
      } else if (selectedOtherAlbum) {
        filtered = filtered.filter(item => {
          const parsed = parsePhotoDate(item);
          return !parsed.isDate && parsed.rawAlbum === selectedOtherAlbum;
        });
      }

      filtered.sort((a, b) => {
        const featA = (typeof a === 'object' && a.is_featured) ? 1 : 0;
        const featB = (typeof b === 'object' && b.is_featured) ? 1 : 0;
        if (featA !== featB) return featB - featA;

        const dateA = parsePhotoDate(a);
        const dateB = parsePhotoDate(b);

        if (dateA.sortTime && dateB.sortTime && dateA.sortTime !== dateB.sortTime) {
          return sortOrder === 'newest' ? dateB.sortTime - dateA.sortTime : dateA.sortTime - dateB.sortTime;
        }

        const nameA = (typeof a === 'string' ? a.split('/').pop() : (a.filename || a.rel_path.split('/').pop())).toLowerCase();
        const nameB = (typeof b === 'string' ? b.split('/').pop() : (b.filename || b.rel_path.split('/').pop())).toLowerCase();
        const comp = nameA.localeCompare(nameB, undefined, { numeric: true, sensitivity: 'base' });
        return sortOrder === 'newest' ? comp : -comp;
      });

      renderPhotosGridData(filtered);
    }

    function toggleSortOrder() {
      sortOrder = sortOrder === 'newest' ? 'oldest' : 'newest';
      const text = $('sortOrderText');
      if (text) text.textContent = sortOrder === 'newest' ? 'Newest First' : 'Oldest First';
      applyPhotoFiltersAndSort();
    }

    function updateProgressText() {
      const el = $('photosProgressText');
      const mobileEl = $('mobilePhotosProgressText');
      const total = (totalMatchingPhotosCount > 0) ? totalMatchingPhotosCount : rawPhotosList.length;
      const loaded = Math.min(gridQueueCursor, currentPhotosList.length);
      const text = `Showing ${loaded} of ${total} photos`;
      if (el) el.textContent = text;
      if (mobileEl) mobileEl.textContent = text;
    }

    function createPhotoCard(item, idx) {
      const p = typeof item === 'string' ? item : item.rel_path;
      const enc = p.split('/').map(encodeURIComponent).join('/');
      const filename = p.split('/').pop();
      const isFeat = typeof item === 'object' && item.is_featured;

      const card = document.createElement('div');
      card.className = 'photo-card' + (isSelectionMode ? ' selection-active' : '') + (selectedPhotos.has(p) ? ' selected' : '');
      card.dataset.photoPath = p;
      card.setAttribute('draggable', 'false');
      card.draggable = false;
      card.addEventListener('contextmenu', (e) => e.preventDefault());
      card.addEventListener('dragstart', (e) => e.preventDefault());

      // Checkbox indicator for selection mode
      const chk = document.createElement('div');
      chk.className = 'photo-select-checkbox';
      chk.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
      chk.style.display = isSelectionMode ? 'flex' : 'none';
      card.appendChild(chk);

      if (isFeat) {
        const badge = document.createElement('span');
        badge.className = 'photo-featured-badge';
        badge.textContent = '★ Featured';
        card.appendChild(badge);
      }

      const wrap = document.createElement('div');
      wrap.className = 'photo-img-wrap';

      const img = document.createElement('img');
      img.className = 'photo-img';
      img.alt = filename;
      img.loading = idx < 12 ? 'eager' : 'lazy';
      img.decoding = 'async';
      img.setAttribute('draggable', 'false');
      img.draggable = false;
      img.addEventListener('contextmenu', (e) => e.preventDefault());
      img.addEventListener('dragstart', (e) => e.preventDefault());

      const onLoad = () => {
        wrap.classList.add('loaded');
        img.classList.add('loaded');
      };

      const onError = () => {
        wrap.classList.add('loaded');
        wrap.classList.add('error');
        wrap.style.display = 'flex';
        wrap.style.alignItems = 'center';
        wrap.style.justifyContent = 'center';
        wrap.style.minHeight = '180px';
        wrap.style.background = 'var(--card)';
        wrap.innerHTML = `<div style="color:var(--muted);font-size:0.75rem;text-align:center;padding:1rem;">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin:0 auto 6px;display:block;opacity:0.5;"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>
          <span>Image unavailable</span>
        </div>`;
      };

      img.addEventListener('load', onLoad, { once: true });
      img.addEventListener('error', onError, { once: true });
      img.src = `/thumb/${enc}`;

      if (img.complete && img.naturalWidth > 0) {
        onLoad();
      }

      wrap.appendChild(img);
      card.appendChild(wrap);

      // Long press detection on mobile to trigger selection mode:
      let pressTimer = null;
      card.addEventListener('touchstart', (e) => {
        pressTimer = setTimeout(() => {
          if (!isSelectionMode) {
            toggleSelectionMode(true);
          }
          togglePhotoSelect(p, card);
        }, 550);
      }, { passive: true });
      card.addEventListener('touchend', () => { if (pressTimer) clearTimeout(pressTimer); });
      card.addEventListener('touchmove', () => { if (pressTimer) clearTimeout(pressTimer); });

      card.addEventListener('click', (e) => {
        if (isSelectionMode) {
          e.preventDefault();
          togglePhotoSelect(p, card);
        } else {
          openPhotoLightbox(idx);
        }
      });
      return card;
    }

    let currentSearchFaceThumb = null;
    let currentSearchEmbedding = null;
    let currentSearchId = null;
    let currentCreationToken = null;
    let currentSelectedFaceIndex = null;
    let currentSearchPage = 1;
    let currentSearchHasMore = false;
    let isLoadingNextPage = false;

    async function fetchNextSearchPage() {
      if (isLoadingNextPage || !currentSearchHasMore) return;
      if (!currentSearchFaceThumb && !currentSearchEmbedding && !currentSearchFile && !currentSearchId) return;
      isLoadingNextPage = true;

      try {
        const payload = {
          search_id: currentSearchId,
          session_id: getSessionId(),
          visitor_id: getVisitorId(),
          embedding: currentSearchEmbedding,
          face_thumb: currentSearchFaceThumb,
          page: currentSearchPage + 1,
          page_size: 50
        };
        let res = await fetch('/api/check-profile-matches', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!res.ok) {
          res = await fetch(`/api/events/${slug}/check-profile-matches`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
        }

        let data = null;
        if (res.ok) {
          data = await res.json();
        }

        // Resilient fallback: If check-profile-matches returned error or no photos, and we have the original uploaded file,
        // fallback to /api/search with page parameter
        if ((!data || data.status === 'error' || !data.photos || data.photos.length === 0) && currentSearchFile) {
          try {
            const fd = new FormData();
            fd.append('selfie', currentSearchFile);
            if (currentSelectedFaceIndex !== null && currentSelectedFaceIndex !== undefined) {
              fd.append('face_index', currentSelectedFaceIndex);
            }
            fd.append('page', currentSearchPage + 1);
            fd.append('page_size', 50);
            let sRes = await fetch('/api/search', { method: 'POST', body: fd });
            if (!sRes.ok) {
              sRes = await fetch(`/api/${slug}/search`, { method: 'POST', body: fd });
            }
            if (sRes.ok) {
              const sData = await sRes.json();
              if (sData && sData.photos && sData.photos.length > 0) {
                data = sData;
              }
            }
          } catch (eFallback) {
            console.warn('Fallback search pagination failed:', eFallback);
          }
        }

        if (data && data.photos && data.photos.length > 0) {
          if (data.search_id) {
            currentSearchId = data.search_id;
            try { localStorage.setItem('brk_current_search_id', data.search_id); } catch (e) {}
          }
          if (data.creation_token) {
            currentCreationToken = data.creation_token;
            try { localStorage.setItem('brk_current_creation_token', data.creation_token); } catch (e) {}
          }
          if (data.embedding && !currentSearchEmbedding) {
            currentSearchEmbedding = data.embedding;
            try { localStorage.setItem('brk_saved_embedding', JSON.stringify(data.embedding)); } catch (e) {}
          }
          if (data.date_summary) {
            serverDateSummary = data.date_summary;
            try {
              localStorage.setItem('brk_saved_date_summary', JSON.stringify(data.date_summary));
            } catch (e) {}
          }
          currentSearchPage = data.page || (currentSearchPage + 1);
          currentSearchHasMore = !!data.has_more;
          if (typeof data.total_count === 'number' && data.total_count > 0) {
            totalMatchingPhotosCount = data.total_count;
          }
          appendPhotosToGrid(data.photos);

          // Automatically prefetch remaining pages smoothly in background
          if (currentSearchHasMore) {
            setTimeout(() => {
              if (currentSearchHasMore && !isLoadingNextPage) {
                fetchNextSearchPage();
              }
            }, 200);
          }
        } else {
          currentSearchHasMore = false;
        }
      } catch (err) {
        console.warn('Error loading next batch of photos:', err);
      } finally {
        isLoadingNextPage = false;
      }
    }

    function appendPhotosToGrid(newPhotos) {
      if (!newPhotos || newPhotos.length === 0) return;
      const seenFnames = new Set(rawPhotosList.map(item => {
        return (typeof item === 'string' ? item.split('/').pop() : (item.filename || item.rel_path.split('/').pop())).toLowerCase();
      }));
      const seenStems = new Set(rawPhotosList.map(item => {
        const fn = (typeof item === 'string' ? item.split('/').pop() : (item.filename || item.rel_path.split('/').pop())).toLowerCase();
        return normalizeBaseFilename(fn);
      }).filter(Boolean));
      const seenSha = new Set(rawPhotosList.map(item => (typeof item === 'object' && item && item.sha256) ? item.sha256 : null).filter(Boolean));

      const added = [];
      for (const item of newPhotos) {
        const fn = (typeof item === 'string' ? item.split('/').pop() : (item.filename || item.rel_path.split('/').pop())).toLowerCase();
        const stem = normalizeBaseFilename(fn);
        const sha = (typeof item === 'object' && item && item.sha256) ? item.sha256 : null;

        if (seenFnames.has(fn)) continue;
        if (sha && seenSha.has(sha)) continue;
        if (stem && seenStems.has(stem)) continue;

        seenFnames.add(fn);
        if (stem) seenStems.add(stem);
        if (sha) seenSha.add(sha);
        added.push(item);
        rawPhotosList.push(item);
      }
      if (added.length === 0) return;

      for (let i = 0; i < added.length; i++) {
        const item = added[i];
        const parsed = parsePhotoDate(item);
        const matchesFilter = (!selectedCalendarDate && !selectedOtherAlbum) ||
          (selectedCalendarDate && parsed.isDate && parsed.dateKey === selectedCalendarDate) ||
          (selectedOtherAlbum && !parsed.isDate && parsed.rawAlbum === selectedOtherAlbum);

        if (matchesFilter) {
          const idx = currentPhotosList.length;
          currentPhotosList.push(item);
          gridRenderQueue.push({
            type: 'photo',
            groupKey: parsed.key,
            item,
            idx
          });
        }
      }

      try {
        localStorage.setItem('brk_saved_photos', JSON.stringify(rawPhotosList));
      } catch (e) {}

      totalMatchingPhotosCount = Math.max(totalMatchingPhotosCount, rawPhotosList.length);
      renderNextGridBatch();
      updateCalendarAndFilters();
      if (!$('photoLightboxModal').hidden) {
        renderFilmstripWindow(currentPhotoIndex);
        updateLightboxHeader(currentPhotoIndex);
        updateSlideSlots(currentPhotoIndex);
      }
    }

    function renderNextGridBatch() {
      if (isRenderingBatch) return;
      isRenderingBatch = true;

      const grid = $('photosGrid');
      const sentinel = $('gridScrollSentinel');
      const end = Math.min(gridQueueCursor + GRID_BATCH_SIZE, gridRenderQueue.length);

      for (let i = gridQueueCursor; i < end; i++) {
        const entry = gridRenderQueue[i];
        if (entry.type === 'section_header') {
          let section = grid.querySelector(`.gallery-date-section[data-group-key="${CSS.escape(entry.groupKey)}"]`);
          if (!section) {
            section = document.createElement('section');
            section.className = 'gallery-date-section';
            section.dataset.groupKey = entry.groupKey;
            section.innerHTML = `
              <div class="gallery-date-header">
                <div class="gallery-date-title-wrap">
                  <h3 class="gallery-date-heading">${entry.title}</h3>
                  <span class="gallery-date-count">${entry.count} ${entry.count === 1 ? 'photo' : 'photos'}</span>
                </div>
              </div>
              <div class="gallery-date-grid"></div>
            `;
            if (sentinel && sentinel.parentNode === grid) {
              grid.insertBefore(section, sentinel);
            } else {
              grid.appendChild(section);
            }
          } else {
            const countEl = section.querySelector('.gallery-date-count');
            if (countEl) countEl.textContent = `${entry.count} ${entry.count === 1 ? 'photo' : 'photos'}`;
          }
        } else if (entry.type === 'photo') {
          let section = grid.querySelector(`.gallery-date-section[data-group-key="${CSS.escape(entry.groupKey)}"]`);
          if (!section) {
            const parsed = parsePhotoDate(entry.item);
            section = document.createElement('section');
            section.className = 'gallery-date-section';
            section.dataset.groupKey = entry.groupKey;
            section.innerHTML = `
              <div class="gallery-date-header">
                <div class="gallery-date-title-wrap">
                  <h3 class="gallery-date-heading">${parsed.title}</h3>
                  <span class="gallery-date-count">1 photo</span>
                </div>
              </div>
              <div class="gallery-date-grid"></div>
            `;
            if (sentinel && sentinel.parentNode === grid) {
              grid.insertBefore(section, sentinel);
            } else {
              grid.appendChild(section);
            }
          }
          const dateGrid = section.querySelector('.gallery-date-grid');
          if (dateGrid) {
            const card = createPhotoCard(entry.item, entry.idx);
            dateGrid.appendChild(card);
            const countEl = section.querySelector('.gallery-date-count');
            if (countEl) {
              const c = dateGrid.children.length;
              countEl.textContent = `${c} ${c === 1 ? 'photo' : 'photos'}`;
            }
          }
        }
      }

      gridQueueCursor = end;
      isRenderingBatch = false;
      updateProgressText();

      // Check if we are approaching the end of current render queue and more pages exist on server
      if (gridQueueCursor >= gridRenderQueue.length - 16 && currentSearchHasMore && !isLoadingNextPage) {
        fetchNextSearchPage();
      }

      if (gridQueueCursor >= gridRenderQueue.length && !currentSearchHasMore) {
        if (gridScrollObserver && sentinel) {
          gridScrollObserver.unobserve(sentinel);
        }
        if (sentinel) sentinel.remove();
      }
    }

    // Scroll listener on full-screen gallery container
    const galleryScroll = $('galleryScrollBody');
    if (galleryScroll) {
      galleryScroll.addEventListener('scroll', () => {
        const scrollBottom = galleryScroll.clientHeight + galleryScroll.scrollTop;
        if (scrollBottom >= galleryScroll.scrollHeight - 1200) {
          if (gridQueueCursor < gridRenderQueue.length) {
            renderNextGridBatch();
          } else if (currentSearchHasMore && !isLoadingNextPage) {
            fetchNextSearchPage();
          }
        }
      }, { passive: true });
    }

    function renderPhotos(photosList) {
      const seenFnames = new Set();
      const seenStems = new Set();
      const seenSha = new Set();
      const uniquePhotos = [];
      for (const item of (photosList || [])) {
        const fn = (typeof item === 'string' ? item.split('/').pop() : (item.filename || item.rel_path.split('/').pop())).toLowerCase();
        const stem = normalizeBaseFilename(fn);
        const sha = (typeof item === 'object' && item && item.sha256) ? item.sha256 : null;

        if (seenFnames.has(fn)) continue;
        if (sha && seenSha.has(sha)) continue;
        if (stem && seenStems.has(stem)) continue;

        seenFnames.add(fn);
        if (stem) seenStems.add(stem);
        if (sha) seenSha.add(sha);
        uniquePhotos.push(item);
      }

      rawPhotosList = uniquePhotos;
      selectedCalendarDate = null;
      selectedOtherAlbum = null;
      hasUserNavigatedMonth = false;
      selectedPhotos.clear();
      updateBulkBar();

      if ($('emptyResultsCard')) {
        $('emptyResultsCard').hidden = rawPhotosList.length > 0;
      }

      applyPhotoFiltersAndSort();
      updateCalendarAndFilters();
    }

    function renderPhotosGridData(list) {
      currentPhotosList = list;
      const grid = $('photosGrid');
      grid.replaceChildren();

      if (gridScrollObserver) {
        gridScrollObserver.disconnect();
      }

      if (currentPhotosList.length === 0) {
        grid.innerHTML = '<div style="color:#64748b;text-align:center;padding:3rem 1rem;font-size:0.95rem;">No photos match the selected filter.</div>';
        updateProgressText();
        return;
      }

      // Group photos by date
      const groupMap = new Map();
      for (let idx = 0; idx < currentPhotosList.length; idx++) {
        const item = currentPhotosList[idx];
        const parsed = parsePhotoDate(item);
        if (!groupMap.has(parsed.key)) {
          groupMap.set(parsed.key, {
            key: parsed.key,
            title: parsed.title,
            sortTime: parsed.sortTime,
            items: []
          });
        }
        groupMap.get(parsed.key).items.push({ item, idx });
      }

      const groups = Array.from(groupMap.values());
      // Sort groups chronologically according to sortOrder
      groups.sort((a, b) => {
        if (a.sortTime && b.sortTime && a.sortTime !== b.sortTime) {
          return sortOrder === 'newest' ? b.sortTime - a.sortTime : a.sortTime - b.sortTime;
        }
        if (a.sortTime) return -1;
        if (b.sortTime) return 1;
        return a.key.localeCompare(b.key);
      });

      gridRenderQueue = [];
      gridQueueCursor = 0;

      for (const g of groups) {
        gridRenderQueue.push({
          type: 'section_header',
          groupKey: g.key,
          title: g.title,
          count: g.items.length
        });
        for (const entry of g.items) {
          gridRenderQueue.push({
            type: 'photo',
            groupKey: g.key,
            item: entry.item,
            idx: entry.idx
          });
        }
      }

      let sentinel = $('gridScrollSentinel');
      if (!sentinel) {
        sentinel = document.createElement('div');
        sentinel.id = 'gridScrollSentinel';
        sentinel.style.height = '30px';
        sentinel.style.width = '100%';
        sentinel.style.display = 'block';
      }
      grid.appendChild(sentinel);

      if (gridRenderQueue.length > GRID_BATCH_SIZE || currentSearchHasMore) {
        gridScrollObserver = new IntersectionObserver((entries) => {
          if (entries[0] && entries[0].isIntersecting) {
            if (gridQueueCursor < gridRenderQueue.length) {
              renderNextGridBatch();
            } else if (currentSearchHasMore && !isLoadingNextPage) {
              fetchNextSearchPage();
            }
          }
        }, {
          root: $('galleryScrollBody') || null,
          rootMargin: '1000px 0px',
          threshold: 0
        });
        gridScrollObserver.observe(sentinel);
      }

      renderNextGridBatch();
      updateProgressText();
    }

    // --- Full-Screen Gallery Overlay Management ---
    function openGalleryOverlay() {
      const overlay = $('photosSection');
      if (!overlay) return;
      overlay.hidden = false;
      document.body.style.overflow = 'hidden';

      const viewAction = $('searchViewPhotosAction');
      if (viewAction) viewAction.hidden = false;

      updateMobileNavigationState();

      try {
        history.pushState({ brkGalleryOpen: true }, '');
      } catch (e) {}
    }

    function closeGalleryOverlay(isFromPopState = false) {
      const overlay = $('photosSection');
      if (!overlay || overlay.hidden) return;

      overlay.hidden = true;
      document.body.style.overflow = '';

      if (isSelectionMode || selectedPhotos.size > 0) {
        clearSelection();
      } else {
        updateMobileNavigationState();
      }

      if (!isFromPopState && window.history.state && window.history.state.brkGalleryOpen) {
        try {
          window.history.back();
        } catch (e) {}
      }
    }

    $('galleryDismissBtn')?.addEventListener('click', () => closeGalleryOverlay(false));
    $('searchViewPhotosBtn')?.addEventListener('click', () => openGalleryOverlay());
    $('closePhotosBtn')?.addEventListener('click', () => {
      closeGalleryOverlay(false);
      clearSelection();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    // --- 3-Panel Sliding Lightbox (Native Smooth Carousel) ---
    let sliderTrack = null;
    let imgPrev = null;
    let imgCurr = null;
    let imgNext = null;
    let spinnerPrev = null;
    let spinnerCurr = null;
    let spinnerNext = null;
    let isTransitioning = false;
    let zoomScale = 1;
    let panX = 0;
    let panY = 0;
    let isPanning = false;

    function initLightboxSlider() {
      sliderTrack = $('lightboxSliderTrack');
      imgPrev = $('imgPrev');
      imgCurr = $('imgCurr');
      imgNext = $('imgNext');
      spinnerPrev = $('spinnerPrev');
      spinnerCurr = $('spinnerCurr');
      spinnerNext = $('spinnerNext');
      [imgPrev, imgCurr, imgNext].forEach(img => {
        if (img) {
          img.setAttribute('draggable', 'false');
          img.draggable = false;
          img.addEventListener('contextmenu', (e) => e.preventDefault());
          img.addEventListener('dragstart', (e) => e.preventDefault());
        }
      });
    }

    // Web Audio API Swipe Sound & Mute Preference
    let audioCtx = null;
    let isSoundMuted = localStorage.getItem('magni_gallery_muted') === 'true';

    function updateSoundButton() {
      const onIcon = $('soundIconOn');
      const offIcon = $('soundIconOff');
      const btn = $('lightboxSoundBtn');
      if (onIcon && offIcon) {
        onIcon.style.display = isSoundMuted ? 'none' : 'block';
        offIcon.style.display = isSoundMuted ? 'block' : 'none';
      }
      if (btn) {
        btn.title = isSoundMuted ? 'Unmute Swipe Sound' : 'Mute Swipe Sound';
      }
    }

    function playSwipeSound() {
      if (isSoundMuted) return;
      try {
        if (!audioCtx) {
          const AC = window.AudioContext || window.webkitAudioContext;
          if (AC) audioCtx = new AC();
        }
        if (audioCtx && audioCtx.state === 'suspended') {
          audioCtx.resume();
        }
        if (!audioCtx) return;
        const now = audioCtx.currentTime;
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();

        osc.type = 'sine';
        osc.frequency.setValueAtTime(450, now);
        osc.frequency.exponentialRampToValueAtTime(150, now + 0.045);

        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.045);

        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start(now);
        osc.stop(now + 0.045);
      } catch (e) { }
    }

    $('lightboxSoundBtn')?.addEventListener('click', e => {
      e.stopPropagation();
      isSoundMuted = !isSoundMuted;
      localStorage.setItem('magni_gallery_muted', isSoundMuted);
      updateSoundButton();
    });
    updateSoundButton();

    function toggleZoomBar(forceState) {
      const bar = $('lightboxZoomBar');
      if (!bar) return;
      if (forceState !== undefined) {
        bar.hidden = !forceState;
      } else {
        bar.hidden = !bar.hidden;
      }
    }

    function setZoom(scale) {
      zoomScale = Math.min(Math.max(scale, 1), 3.5);
      if (zoomScale <= 1.02) {
        resetZoom();
      } else {
        updateImgTransform();
      }
    }

    function updateImgTransform() {
      initLightboxSlider();
      if (!imgCurr) return;
      const zoomText = $('zoomLevelText');
      if (zoomText) zoomText.textContent = `${Math.round(zoomScale * 100)}%`;

      if (zoomScale <= 1.02) {
        zoomScale = 1;
        panX = 0;
        panY = 0;
        isPanning = false;
        imgCurr.style.transform = 'none';
        imgCurr.style.cursor = 'grab';
      } else {
        const maxPanX = (window.innerWidth * (zoomScale - 1)) / 1.8;
        const maxPanY = (window.innerHeight * (zoomScale - 1)) / 1.8;
        panX = Math.max(Math.min(panX, maxPanX), -maxPanX);
        panY = Math.max(Math.min(panY, maxPanY), -maxPanY);

        imgCurr.style.transform = `scale(${zoomScale}) translate3d(${panX / zoomScale}px, ${panY / zoomScale}px, 0)`;
        imgCurr.style.cursor = isPanning ? 'grabbing' : 'grab';
      }
    }

    function resetZoom() {
      zoomScale = 1;
      panX = 0;
      panY = 0;
      isPanning = false;
      initLightboxSlider();
      if (imgCurr) {
        imgCurr.style.transform = 'none';
        imgCurr.style.cursor = 'grab';
      }
      const zoomText = $('zoomLevelText');
      if (zoomText) zoomText.textContent = '100%';
    }

    function getPhotoEnc(item) {
      if (!item) return '';
      const p = typeof item === 'string' ? item : item.rel_path;
      return p.split('/').map(encodeURIComponent).join('/');
    }

    function getPhotoFilename(item) {
      if (!item) return '';
      const p = typeof item === 'string' ? item : item.rel_path;
      return p.split('/').pop();
    }

    function getTotalPhotosCount() {
      if (selectedCalendarDate) {
        const c = (typeof datePhotoCounts !== 'undefined' && datePhotoCounts) ? datePhotoCounts.get(selectedCalendarDate) : null;
        return (c && c > 0) ? Math.max(c, currentPhotosList.length) : currentPhotosList.length;
      }
      if (selectedOtherAlbum) {
        const c = (typeof otherAlbumCounts !== 'undefined' && otherAlbumCounts) ? otherAlbumCounts.get(selectedOtherAlbum) : null;
        return (c && c > 0) ? Math.max(c, currentPhotosList.length) : currentPhotosList.length;
      }
      if (typeof totalMatchingPhotosCount === 'number' && totalMatchingPhotosCount > 0) {
        return Math.max(totalMatchingPhotosCount, currentPhotosList.length);
      }
      return currentPhotosList.length;
    }

    function updateLightboxHeader(index) {
      const total = getTotalPhotosCount();
      if (total <= 0) return;
      const normIndex = ((index % total) + total) % total;
      const item = currentPhotosList[normIndex];
      const filename = item ? getPhotoFilename(item) : `Photo ${normIndex + 1}`;
      const enc = item ? getPhotoEnc(item) : '';

      if (item) {
        trackGenuinePhotoView(item);
      }

      $('lightboxCounter').textContent = `${normIndex + 1} / ${total}`;
      $('lightboxFilename').textContent = filename;
      $('lightboxFilename').title = filename;

      const hdBtn = $('lightboxDownloadHdBtn');
      if (hdBtn) {
        if (enc) {
          hdBtn.href = `/download/hd/${enc}`;
          hdBtn.setAttribute('download', `${filename.replace(/\.[^/.]+$/, "")}_HD.jpg`);
          hdBtn.style.pointerEvents = '';
          hdBtn.style.opacity = '1';
        } else {
          hdBtn.removeAttribute('href');
          hdBtn.style.pointerEvents = 'none';
          hdBtn.style.opacity = '0.5';
        }
      }

      const btn4k = $('lightboxDownload4kBtn');
      if (btn4k) {
        if (enc) {
          btn4k.href = `/download/4k/${enc}`;
          btn4k.setAttribute('download', filename);
          btn4k.style.pointerEvents = '';
          btn4k.style.opacity = '1';
        } else {
          btn4k.removeAttribute('href');
          btn4k.style.pointerEvents = 'none';
          btn4k.style.opacity = '0.5';
        }
      }
    }

    // --- 9-Thumbnail Circular Filmstrip Window ---
    const FILMSTRIP_WINDOW_SIZE = 9;

    function renderFilmstripWindow(centerIndex = currentPhotoIndex) {
      const track = $('lightboxFilmstripTrack');
      if (!track) return;

      const total = getTotalPhotosCount();
      if (total === 0) {
        track.replaceChildren();
        return;
      }

      // Single source of truth: normalized 0-based circular index based on total (e.g. 160)
      const normCenter = ((centerIndex % total) + total) % total;

      // When total >= 9: Display exactly 9 thumbnails with selected photo at 5th position (index 4).
      // When total < 9: Display all photos (0..total-1) in order without duplicating.
      const windowSize = Math.min(FILMSTRIP_WINDOW_SIZE, total);
      const half = Math.floor(windowSize / 2); // 4 when windowSize is 9

      const targetIndices = [];
      if (total >= FILMSTRIP_WINDOW_SIZE) {
        for (let offset = -half; offset <= half; offset++) {
          const idx = ((normCenter + offset) % total + total) % total;
          targetIndices.push(idx);
        }
      } else {
        for (let i = 0; i < total; i++) {
          targetIndices.push(i);
        }
      }

      // Keep exact number of DOM nodes in track
      while (track.children.length < targetIndices.length) {
        const thumb = document.createElement('div');
        thumb.className = 'filmstrip-thumb';
        const img = document.createElement('img');
        img.loading = 'eager';
        img.decoding = 'async';
        thumb.appendChild(img);

        thumb.addEventListener('click', (e) => {
          e.stopPropagation();
          const target = parseInt(thumb.dataset.index, 10);
          if (!isNaN(target) && target !== currentPhotoIndex) {
            jumpToPhoto(target);
          }
        });
        track.appendChild(thumb);
      }
      while (track.children.length > targetIndices.length) {
        track.removeChild(track.lastChild);
      }

      let needMorePhotos = false;

      // Update thumbnails in place smoothly without image reloads
      for (let pos = 0; pos < targetIndices.length; pos++) {
        const photoIdx = targetIndices[pos];
        const thumb = track.children[pos];
        const img = thumb.firstElementChild;
        const item = currentPhotosList[photoIdx];

        thumb.dataset.index = photoIdx;
        const isActive = (photoIdx === normCenter);
        thumb.classList.toggle('active', isActive);
        thumb.setAttribute('aria-label', `Photo ${photoIdx + 1}${isActive ? ' (current)' : ''}`);

        if (item) {
          thumb.classList.remove('loading-placeholder');
          const p = typeof item === 'string' ? item : item.rel_path;
          const enc = p.split('/').map(encodeURIComponent).join('/');
          const thumbSrc = `/thumb/${enc}`;

          if (img.dataset.src !== thumbSrc) {
            img.dataset.src = thumbSrc;
            img.src = thumbSrc;
            img.alt = `Photo ${photoIdx + 1}`;
          }
        } else {
          // Photo is not yet loaded into memory - display skeleton placeholder and fetch
          needMorePhotos = true;
          thumb.classList.add('loading-placeholder');
          img.removeAttribute('src');
          img.dataset.src = '';
          img.alt = `Photo ${photoIdx + 1} (loading...)`;
        }
      }

      if (needMorePhotos || normCenter >= currentPhotosList.length - 15) {
        if (currentSearchHasMore && !isLoadingNextPage) {
          fetchNextSearchPage();
        }
      }
    }

    function buildFilmstrip() {
      renderFilmstripWindow(currentPhotoIndex);
    }

    function updateFilmstripActive(index, autoCenter = true) {
      renderFilmstripWindow(index);
    }

    function checkAndFetchMorePhotos(index) {
      const total = getTotalPhotosCount();
      const norm = ((index % total) + total) % total;
      if ((norm >= currentPhotosList.length - 15 || currentPhotosList.length < total) && currentSearchHasMore && !isLoadingNextPage) {
        fetchNextSearchPage();
      }
    }

    function setSlideImage(imgEl, spinnerEl, src) {
      if (!imgEl) return;
      if (!src) {
        imgEl.src = '';
        if (spinnerEl) spinnerEl.classList.remove('visible');
        return;
      }
      if (imgEl.src && imgEl.src.endsWith(src)) {
        if (spinnerEl) spinnerEl.classList.remove('visible');
        return;
      }
      if (spinnerEl) spinnerEl.classList.add('visible');
      imgEl.onload = () => {
        if (spinnerEl) spinnerEl.classList.remove('visible');
      };
      imgEl.onerror = () => {
        if (spinnerEl) spinnerEl.classList.remove('visible');
      };
      imgEl.src = src;
    }

    function updateSlideSlots(targetIdx) {
      initLightboxSlider();
      const total = getTotalPhotosCount();
      if (total === 0) return;

      const normTarget = ((targetIdx % total) + total) % total;
      const currItem = currentPhotosList[normTarget];
      if (currItem) {
        const currEnc = getPhotoEnc(currItem);
        setSlideImage(imgCurr, spinnerCurr, `/preview/${currEnc}`);
      } else {
        setSlideImage(imgCurr, spinnerCurr, '');
        if (spinnerCurr) spinnerCurr.classList.add('visible');
        if (currentSearchHasMore && !isLoadingNextPage) {
          fetchNextSearchPage();
        }
      }

      if (total > 1) {
        const prevIdx = ((normTarget - 1) % total + total) % total;
        const nextIdx = (normTarget + 1) % total;

        const prevItem = currentPhotosList[prevIdx];
        const nextItem = currentPhotosList[nextIdx];

        if (prevItem) {
          const prevEnc = getPhotoEnc(prevItem);
          setSlideImage(imgPrev, spinnerPrev, `/preview/${prevEnc}`);
        } else {
          setSlideImage(imgPrev, spinnerPrev, '');
        }

        if (nextItem) {
          const nextEnc = getPhotoEnc(nextItem);
          setSlideImage(imgNext, spinnerNext, `/preview/${nextEnc}`);
        } else {
          setSlideImage(imgNext, spinnerNext, '');
        }

        // Preload adjacent ±2 photos in background
        const prev2Idx = ((normTarget - 2) % total + total) % total;
        const next2Idx = (normTarget + 2) % total;
        if (currentPhotosList[prev2Idx]) {
          new Image().src = `/preview/${getPhotoEnc(currentPhotosList[prev2Idx])}`;
        }
        if (currentPhotosList[next2Idx]) {
          new Image().src = `/preview/${getPhotoEnc(currentPhotosList[next2Idx])}`;
        }
      } else {
        setSlideImage(imgPrev, spinnerPrev, '');
        setSlideImage(imgNext, spinnerNext, '');
      }
    }

    function jumpToPhoto(targetIdx) {
      const total = getTotalPhotosCount();
      if (total === 0) return;
      const normalizedIdx = ((targetIdx % total) + total) % total;
      initLightboxSlider();
      currentPhotoIndex = normalizedIdx;
      resetZoom();

      if (sliderTrack) {
        sliderTrack.style.transition = 'none';
        sliderTrack.style.transform = 'translate3d(-100%, 0, 0)';
      }

      updateLightboxHeader(normalizedIdx);
      renderFilmstripWindow(normalizedIdx);
      checkAndFetchMorePhotos(normalizedIdx);

      if (imgCurr) {
        imgCurr.style.transition = 'opacity 0.15s ease';
        imgCurr.style.opacity = '0.4';
      }
      updateSlideSlots(normalizedIdx);
      setTimeout(() => {
        if (imgCurr) imgCurr.style.opacity = '1';
      }, 60);

      try {
        history.replaceState({ brkLightboxOpen: true, photoIndex: normalizedIdx }, '');
      } catch (e) {}
    }

    function goToNextPhoto() {
      const total = getTotalPhotosCount();
      if (isTransitioning || total <= 1) return;
      initLightboxSlider();
      isTransitioning = true;
      resetZoom();

      if (sliderTrack) {
        sliderTrack.style.transition = 'transform 0.25s cubic-bezier(0.16, 1, 0.3, 1)';
        sliderTrack.style.transform = 'translate3d(-200%, 0, 0)';
      }
      playSwipeSound();

      setTimeout(() => {
        finishSlideTransition(1);
      }, 250);
    }

    function goToPrevPhoto() {
      const total = getTotalPhotosCount();
      if (isTransitioning || total <= 1) return;
      initLightboxSlider();
      isTransitioning = true;
      resetZoom();

      if (sliderTrack) {
        sliderTrack.style.transition = 'transform 0.25s cubic-bezier(0.16, 1, 0.3, 1)';
        sliderTrack.style.transform = 'translate3d(0%, 0, 0)';
      }
      playSwipeSound();

      setTimeout(() => {
        finishSlideTransition(-1);
      }, 250);
    }

    function finishSlideTransition(direction) {
      initLightboxSlider();
      const total = getTotalPhotosCount();
      if (total <= 0) {
        isTransitioning = false;
        return;
      }

      if (direction === 1) {
        currentPhotoIndex = (currentPhotoIndex + 1) % total;
      } else if (direction === -1) {
        currentPhotoIndex = ((currentPhotoIndex - 1) % total + total) % total;
      }

      updateLightboxHeader(currentPhotoIndex);
      renderFilmstripWindow(currentPhotoIndex);
      checkAndFetchMorePhotos(currentPhotoIndex);

      updateSlideSlots(currentPhotoIndex);

      // Instantly snap track back to center without transition
      if (sliderTrack) {
        sliderTrack.style.transition = 'none';
        sliderTrack.style.transform = 'translate3d(-100%, 0, 0)';
      }

      try {
        history.replaceState({ brkLightboxOpen: true, photoIndex: currentPhotoIndex }, '');
      } catch (e) {}

      isTransitioning = false;
      currentDragX = 0;
      currentDragY = 0;
    }

    function openPhotoLightbox(index) {
      const total = getTotalPhotosCount();
      if (total === 0) return;
      currentPhotoIndex = ((index % total) + total) % total;
      resetZoom();

      // Remember scroll position in gallery so we return to the exact same spot
      const galleryBody = $('galleryScrollBody');
      if (galleryBody) {
        lastGalleryScrollTop = galleryBody.scrollTop;
      }

      initLightboxSlider();
      if (sliderTrack) {
        sliderTrack.style.transition = 'none';
        sliderTrack.style.transform = 'translate3d(-100%, 0, 0)';
      }

      updateLightboxHeader(currentPhotoIndex);
      renderFilmstripWindow(currentPhotoIndex);
      checkAndFetchMorePhotos(currentPhotoIndex);

      updateSlideSlots(currentPhotoIndex);

      const modal = $('photoLightboxModal');
      if (modal.hidden) {
        modal.hidden = false;
        requestAnimationFrame(() => {
          modal.classList.add('active');
        });

        try {
          history.pushState({ brkLightboxOpen: true, photoIndex: currentPhotoIndex }, '');
        } catch (e) {}
      } else {
        try {
          history.replaceState({ brkLightboxOpen: true, photoIndex: currentPhotoIndex }, '');
        } catch (e) {}
      }
    }

    function closeLightbox(isFromPopState = false) {
      const modal = $('photoLightboxModal');
      if (!modal || (modal.hidden && !modal.classList.contains('active'))) return;

      modal.classList.remove('active');
      setTimeout(() => {
        modal.hidden = true;
        initLightboxSlider();
        if (imgCurr) imgCurr.src = '';
        if (imgPrev) imgPrev.src = '';
        if (imgNext) imgNext.src = '';
        resetZoom();
        toggleZoomBar(false);
      }, 260);

      if (!isFromPopState && window.history.state && window.history.state.brkLightboxOpen) {
        isClosingLightboxViaCode = true;
        try {
          window.history.back();
        } catch (e) {}
      }

      // Restore scroll position in gallery
      const galleryBody = $('galleryScrollBody');
      if (galleryBody && lastGalleryScrollTop > 0) {
        galleryBody.scrollTop = lastGalleryScrollTop;
      }

      const allCards = document.querySelectorAll('.photo-card');
      if (allCards && allCards[currentPhotoIndex]) {
        allCards[currentPhotoIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
        allCards[currentPhotoIndex].classList.add('focused-photo-pulse');
        setTimeout(() => {
          allCards[currentPhotoIndex]?.classList.remove('focused-photo-pulse');
        }, 1200);
      }
    }

    window.addEventListener('popstate', (e) => {
      // 1. If we triggered history.back() from closeLightbox(), ignore this popstate event
      if (isClosingLightboxViaCode) {
        isClosingLightboxViaCode = false;
        const galleryBody = $('galleryScrollBody');
        if (galleryBody && lastGalleryScrollTop > 0) {
          galleryBody.scrollTop = lastGalleryScrollTop;
        }
        return;
      }

      // 2. Filter bottom sheet dismiss on back button
      const filterSheet = $('filterSheetBackdrop');
      if (filterSheet && !filterSheet.hidden && filterSheet.classList.contains('active')) {
        closeFilterSheet();
        return;
      }

      // 3. Lightbox viewer dismiss on back button
      const lightbox = $('photoLightboxModal');
      if (lightbox && (!lightbox.hidden || lightbox.classList.contains('active'))) {
        closeLightbox(true);
        const galleryBody = $('galleryScrollBody');
        if (galleryBody && lastGalleryScrollTop > 0) {
          galleryBody.scrollTop = lastGalleryScrollTop;
        }
        return;
      }

      // 4. Results gallery overlay: if history state indicates we are still in gallery, do NOT close
      if (e.state && e.state.brkGalleryOpen) {
        return;
      }

      // 5. Back from results gallery to search home
      const gallery = $('photosSection');
      if (gallery && !gallery.hidden) {
        closeGalleryOverlay(true);
        return;
      }
    });

    $('closeLightboxBtn').addEventListener('click', () => closeLightbox(false));

    // Keyboard Navigation
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        const moreSheet = $('moreSheetBackdrop');
        if (moreSheet && !moreSheet.hidden && moreSheet.classList.contains('active')) {
          closeMoreSheet();
          return;
        }
        const filterSheet = $('filterSheetBackdrop');
        if (filterSheet && !filterSheet.hidden && filterSheet.classList.contains('active')) {
          closeFilterSheet();
          return;
        }
      }
      if (!$('photoLightboxModal').hidden) {
        if (e.key === 'ArrowRight') goToNextPhoto();
        if (e.key === 'ArrowLeft') goToPrevPhoto();
        if (e.key === 'Escape') closeLightbox(false);
      }
    });

    // Lightbox Filmstrip Touch Isolation & Horizontal Swipe Handling
    const filmstripBar = $('lightboxFilmstripBar');
    let fsTouchStartX = 0;
    let fsTouchStartY = 0;
    let fsTouchStartTime = 0;

    if (filmstripBar) {
      filmstripBar.addEventListener('touchstart', e => {
        e.stopPropagation();
        if (e.touches.length === 1) {
          fsTouchStartX = e.touches[0].clientX;
          fsTouchStartY = e.touches[0].clientY;
          fsTouchStartTime = Date.now();
        }
      }, { passive: true });

      filmstripBar.addEventListener('touchmove', e => {
        e.stopPropagation();
      }, { passive: true });

      filmstripBar.addEventListener('touchend', e => {
        e.stopPropagation();
        if (e.changedTouches.length === 1) {
          const diffX = e.changedTouches[0].clientX - fsTouchStartX;
          const diffY = e.changedTouches[0].clientY - fsTouchStartY;
          const elapsed = Date.now() - fsTouchStartTime;
          if (Math.abs(diffX) > 28 && Math.abs(diffX) > Math.abs(diffY) && elapsed < 450) {
            if (diffX < 0) {
              goToNextPhoto();
            } else {
              goToPrevPhoto();
            }
          }
        }
      }, { passive: true });
    }

    // Touch & Swipe Controller on Lightbox Wrap (Native 3-Panel Physics)
    const lightboxWrap = $('lightboxImgWrap');
    let touchStartX = 0;
    let touchStartY = 0;
    let touchStartTime = 0;
    let currentDragX = 0;
    let currentDragY = 0;
    let startPanX = 0;
    let startPanY = 0;
    let isTouchSwiping = false;
    let isPinching = false;
    let initialPinchDist = 0;
    let initialPinchScale = 1;
    let lastLightboxTap = 0;

    lightboxWrap.addEventListener('touchstart', e => {
      if (isTransitioning) return;
      if (e.target.closest('button, a, .lightbox-zoom-bar')) return;
      initLightboxSlider();

      if (e.touches.length === 2) {
        isPinching = true;
        isPanning = false;
        isTouchSwiping = false;
        initialPinchDist = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        initialPinchScale = zoomScale;
        return;
      }

      if (e.touches.length === 1) {
        touchStartTime = Date.now();
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
        currentDragX = 0;
        currentDragY = 0;

        if (zoomScale > 1.05) {
          isPanning = true;
          isTouchSwiping = false;
          startPanX = e.touches[0].clientX - panX;
          startPanY = e.touches[0].clientY - panY;
        } else {
          isPanning = false;
          isTouchSwiping = true;
          if (sliderTrack) {
            sliderTrack.style.transition = 'none';
          }
        }
      }
    }, { passive: false });

    lightboxWrap.addEventListener('touchmove', e => {
      if (isTransitioning) return;

      if (isPinching && e.touches.length === 2) {
        e.preventDefault();
        const dist = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        if (initialPinchDist > 10) {
          setZoom(initialPinchScale * (dist / initialPinchDist));
        }
        return;
      }

      if (e.touches.length === 1) {
        const diffX = e.touches[0].clientX - touchStartX;
        const diffY = e.touches[0].clientY - touchStartY;
        currentDragX = diffX;
        currentDragY = diffY;

        if (isPanning && zoomScale > 1.05) {
          e.preventDefault();
          panX = e.touches[0].clientX - startPanX;
          panY = e.touches[0].clientY - startPanY;
          updateImgTransform();
        } else if (isTouchSwiping && zoomScale <= 1.05) {
          if (Math.abs(diffX) > Math.abs(diffY)) {
            e.preventDefault();
            if (sliderTrack) {
              const resistance = (getTotalPhotosCount() <= 1) ? 0.25 : 1;
              sliderTrack.style.transform = `translate3d(calc(-100% + ${diffX * resistance}px), 0, 0)`;
            }
          }
        }
      }
    }, { passive: false });

    lightboxWrap.addEventListener('touchend', e => {
      if (isPinching && e.touches.length < 2) {
        isPinching = false;
      }

      if (e.touches.length === 0) {
        const elapsed = Date.now() - touchStartTime;
        const dist = Math.hypot(currentDragX, currentDragY);

        // Tap Detection
        if (dist < 10 && elapsed < 350) {
          isPanning = false;
          isTouchSwiping = false;
          const now = Date.now();
          if (now - lastLightboxTap < 300) {
            if (zoomScale > 1.2) {
              resetZoom();
            } else {
              setZoom(2.2);
            }
            lastLightboxTap = 0;
          } else {
            lastLightboxTap = now;
            toggleZoomBar();
          }
          return;
        }

        if (isPanning) {
          isPanning = false;
          updateImgTransform();
          return;
        }

        if (isTouchSwiping && zoomScale <= 1.05) {
          isTouchSwiping = false;
          const diffX = currentDragX;
          const isQuickSwipe = elapsed < 260 && Math.abs(diffX) > 35;
          const isThresholdPassed = Math.abs(diffX) > 60;

          if ((isThresholdPassed || isQuickSwipe) && getTotalPhotosCount() > 1 && !isTransitioning) {
            if (diffX < 0) {
              goToNextPhoto();
            } else {
              goToPrevPhoto();
            }
          } else {
            if (sliderTrack) {
              sliderTrack.style.transition = 'transform 0.22s cubic-bezier(0.16, 1, 0.3, 1)';
              sliderTrack.style.transform = 'translate3d(-100%, 0, 0)';
            }
          }
        }
      }
    }, { passive: true });

    // Desktop Mouse Drag / Click Handling
    let mouseIsDown = false;
    let mouseStartTime = 0;
    let mouseStartX = 0;
    let mouseStartY = 0;
    let mouseDragX = 0;
    let mouseDragY = 0;

    lightboxWrap.addEventListener('mousedown', e => {
      if (isTransitioning) return;
      if (e.target.closest('button, a, .lightbox-zoom-bar')) return;
      initLightboxSlider();

      mouseIsDown = true;
      mouseStartTime = Date.now();
      mouseStartX = e.clientX;
      mouseStartY = e.clientY;
      mouseDragX = 0;
      mouseDragY = 0;

      if (zoomScale > 1.05) {
        e.preventDefault();
        isPanning = true;
        startPanX = e.clientX - panX;
        startPanY = e.clientY - panY;
      } else {
        isPanning = false;
        if (sliderTrack) sliderTrack.style.transition = 'none';
      }
    });

    window.addEventListener('mousemove', e => {
      if (!mouseIsDown || isTransitioning) return;
      const diffX = e.clientX - mouseStartX;
      const diffY = e.clientY - mouseStartY;
      mouseDragX = diffX;
      mouseDragY = diffY;

      if (isPanning && zoomScale > 1.05) {
        panX = e.clientX - startPanX;
        panY = e.clientY - startPanY;
        updateImgTransform();
      } else if (zoomScale <= 1.05) {
        if (sliderTrack) {
          const resistance = (getTotalPhotosCount() <= 1) ? 0.25 : 1;
          sliderTrack.style.transform = `translate3d(calc(-100% + ${diffX * resistance}px), 0, 0)`;
        }
      }
    });

    window.addEventListener('mouseup', e => {
      if (!mouseIsDown) return;
      mouseIsDown = false;

      const elapsed = Date.now() - mouseStartTime;
      const dist = Math.hypot(mouseDragX, mouseDragY);

      if (dist < 8 && elapsed < 350) {
        if (e.target.closest('.lightbox-slide, .lightbox-slide-img')) {
          toggleZoomBar();
        }
        isPanning = false;
        return;
      }

      if (isPanning) {
        isPanning = false;
        updateImgTransform();
        return;
      }

      if (zoomScale <= 1.05 && !isTransitioning) {
        const diffX = mouseDragX;
        const isQuickSwipe = elapsed < 260 && Math.abs(diffX) > 35;
        const isThresholdPassed = Math.abs(diffX) > 60;

        if ((isThresholdPassed || isQuickSwipe) && getTotalPhotosCount() > 1) {
          if (diffX < 0) {
            goToNextPhoto();
          } else {
            goToPrevPhoto();
          }
        } else {
          if (sliderTrack) {
            sliderTrack.style.transition = 'transform 0.22s cubic-bezier(0.16, 1, 0.3, 1)';
            sliderTrack.style.transform = 'translate3d(-100%, 0, 0)';
          }
        }
      }
    });

    // Zoom Toolbar Control Listeners
    $('zoomInBtn')?.addEventListener('click', e => {
      e.stopPropagation();
      setZoom(zoomScale + 0.5);
    });

    $('zoomOutBtn')?.addEventListener('click', e => {
      e.stopPropagation();
      setZoom(zoomScale - 0.5);
    });

    $('zoomResetBtn')?.addEventListener('click', e => {
      e.stopPropagation();
      resetZoom();
    });

    $('closeZoomBarBtn')?.addEventListener('click', e => {
      e.stopPropagation();
      toggleZoomBar(false);
    });

    $('lightboxZoomToggleBtn')?.addEventListener('click', e => {
      e.stopPropagation();
      toggleZoomBar();
    });

    // Floating Action Button to Overview
    const fab = $('fabToOverview');
    if (fab) {
      window.addEventListener('scroll', () => {
        fab.classList.toggle('visible', window.scrollY > 350);
      }, { passive: true });
      fab.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
      });
    }


    // In-Browser Selfie Camera Controller (Mobile & Desktop)
    let mediaStream = null;
    let cameraFacingMode = 'user'; // front camera by default
    let cameraCanvas = null;

    function isCameraSupported() {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    async function openWebcamModal() {
      const modal = $('webcamModal');
      const video = $('webcamVideo');
      const previewImg = $('cameraPreviewImg');
      const captureControls = $('cameraCaptureControls');
      const previewControls = $('cameraPreviewControls');
      const faceGuide = $('cameraFaceGuide');
      const title = $('cameraModalTitle');
      const subtitle = $('cameraModalSubtitle');
      const flipBtn = $('flipCameraBtn');

      if (!modal || !video) return;

      if (!isCameraSupported() || (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1')) {
        console.warn('In-browser camera requires HTTPS or is unsupported; opening file upload fallback.');
        $('selfieFileInput').click();
        return;
      }

      // Reset to live capture mode
      if (previewImg) { previewImg.src = ''; previewImg.style.display = 'none'; }
      if (video) { video.style.display = 'block'; }
      if (faceGuide) { faceGuide.style.display = 'block'; }
      if (flipBtn) { flipBtn.style.display = 'flex'; }
      if (captureControls) { captureControls.style.display = 'flex'; }
      if (previewControls) { previewControls.style.display = 'none'; }
      if (title) { title.textContent = 'Take a Selfie'; }
      if (subtitle) { subtitle.textContent = 'Position your face inside the frame and tap Capture'; }

      modal.hidden = false;
      document.body.style.overflow = 'hidden';

      await startCameraStream();
    }

    async function startCameraStream() {
      const video = $('webcamVideo');
      if (!video) return;

      if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
      }

      video.style.transform = cameraFacingMode === 'user' ? 'scaleX(-1)' : 'scaleX(1)';

      try {
        const constraints = {
          video: {
            facingMode: cameraFacingMode,
            width: { ideal: 1280 },
            height: { ideal: 960 }
          },
          audio: false
        };
        mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
        video.srcObject = mediaStream;
        await video.play();
      } catch (err) {
        console.warn('Camera stream error:', err);
        try {
          mediaStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
          video.srcObject = mediaStream;
          await video.play();
        } catch (fallbackErr) {
          console.error('All camera attempts failed:', fallbackErr);
          stopWebcam();
          const statusEl = $('searchStatus');
          if (statusEl) {
            statusEl.innerHTML = `<span style="color:#d97706;font-weight:600;">Camera access was blocked or is unavailable. Please upload a photo instead.</span>`;
          }
          $('selfieFileInput').click();
        }
      }
    }

    async function toggleCameraFlip() {
      cameraFacingMode = cameraFacingMode === 'user' ? 'environment' : 'user';
      await startCameraStream();
    }

    function captureWebcamPhoto() {
      const video = $('webcamVideo');
      const previewImg = $('cameraPreviewImg');
      const captureControls = $('cameraCaptureControls');
      const previewControls = $('cameraPreviewControls');
      const faceGuide = $('cameraFaceGuide');
      const flipBtn = $('flipCameraBtn');
      const title = $('cameraModalTitle');
      const subtitle = $('cameraModalSubtitle');

      if (!video || !video.videoWidth) return;

      if (!cameraCanvas) {
        cameraCanvas = document.createElement('canvas');
      }
      cameraCanvas.width = video.videoWidth;
      cameraCanvas.height = video.videoHeight;
      const ctx = cameraCanvas.getContext('2d');

      // Mirror horizontally if front camera was used
      if (cameraFacingMode === 'user') {
        ctx.translate(cameraCanvas.width, 0);
        ctx.scale(-1, 1);
      }
      ctx.drawImage(video, 0, 0, cameraCanvas.width, cameraCanvas.height);

      const previewDataUrl = cameraCanvas.toDataURL('image/jpeg', 0.92);
      if (previewImg) {
        previewImg.src = previewDataUrl;
        previewImg.style.display = 'block';
      }
      video.style.display = 'none';
      if (faceGuide) faceGuide.style.display = 'none';
      if (flipBtn) flipBtn.style.display = 'none';
      if (captureControls) captureControls.style.display = 'none';
      if (previewControls) previewControls.style.display = 'flex';
      if (title) title.textContent = 'Preview Selfie';
      if (subtitle) subtitle.textContent = 'Is your face clear? Tap Use Photo to search or Retake';
    }

    function retakeWebcamPhoto() {
      const video = $('webcamVideo');
      const previewImg = $('cameraPreviewImg');
      const captureControls = $('cameraCaptureControls');
      const previewControls = $('cameraPreviewControls');
      const faceGuide = $('cameraFaceGuide');
      const flipBtn = $('flipCameraBtn');
      const title = $('cameraModalTitle');
      const subtitle = $('cameraModalSubtitle');

      if (previewImg) { previewImg.src = ''; previewImg.style.display = 'none'; }
      if (video) { video.style.display = 'block'; }
      if (faceGuide) faceGuide.style.display = 'block';
      if (flipBtn) flipBtn.style.display = 'flex';
      if (captureControls) captureControls.style.display = 'flex';
      if (previewControls) previewControls.style.display = 'none';
      if (title) title.textContent = 'Take a Selfie';
      if (subtitle) subtitle.textContent = 'Position your face inside the frame and tap Capture';
    }

    async function useCapturedPhoto() {
      if (!cameraCanvas) return;
      const useBtn = $('usePhotoBtn');
      const originalText = useBtn ? useBtn.innerHTML : '';
      if (useBtn) {
        useBtn.disabled = true;
        useBtn.innerHTML = `<span>Processing...</span>`;
      }

      try {
        const blob = await new Promise((resolve, reject) => {
          cameraCanvas.toBlob(b => {
            if (b) resolve(b);
            else reject(new Error('Failed to create photo image.'));
          }, 'image/jpeg', 0.92);
        });

        if (!blob || blob.size === 0) {
          throw new Error('Captured photo was empty. Please retake.');
        }

        const file = new File([blob], 'selfie.jpg', { type: 'image/jpeg', lastModified: Date.now() });
        stopWebcam();
        await processAndSearchSelfie(file);
      } catch (err) {
        console.error('Error in useCapturedPhoto:', err);
        alert(err.message || 'Could not process photo. Please try again.');
        retakeWebcamPhoto();
      } finally {
        if (useBtn) {
          useBtn.disabled = false;
          useBtn.innerHTML = originalText;
        }
      }
    }

    function stopWebcam() {
      if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
      }
      const modal = $('webcamModal');
      if (modal) modal.hidden = true;
      document.body.style.overflow = '';
    }

    // Stop camera if user navigates or switches away
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && mediaStream) {
        stopWebcam();
      }
    });

    function triggerCamera() {
      // In-browser camera works seamlessly on both mobile and desktop
      if (isCameraSupported()) {
        openWebcamModal();
      } else {
        $('selfieFileInput').click();
      }
    }

    let currentSearchFile = null;

    // --- Local Storage Saved Photos Cache & Real-Time Match Sync ---
    let isSyncingProfileCount = false;

    function checkSavedProfile() {
      const badge = $('savedProfileBadge');
      if (!badge) return;

      const savedThumb = localStorage.getItem('brk_saved_face_thumb');
      const savedPhotosJson = localStorage.getItem('brk_saved_photos');
      const savedCount = parseInt(localStorage.getItem('brk_saved_count') || '0', 10);
      const acknowledgedCount = parseInt(localStorage.getItem('brk_acknowledged_count') || `${savedCount}`, 10);

      if (savedThumb && savedPhotosJson && savedCount > 0) {
        try {
          const photos = JSON.parse(savedPhotosJson);
          if (Array.isArray(photos) && photos.length > 0) {
            const avatarImg = $('savedAvatarImg');
            if (avatarImg) avatarImg.src = savedThumb;
            const countText = $('savedPhotosCountText');
            if (countText) {
              countText.textContent = `Found ${savedCount} photo${savedCount === 1 ? '' : 's'} with BRK Sir`;
            }
            const newBadge = $('newBadgeIndicator');
            if (newBadge) {
              newBadge.hidden = !(savedCount > acknowledgedCount);
            }
            badge.hidden = false;
            return;
          }
        } catch (e) {
          console.warn('Error reading saved photos from localStorage:', e);
        }
      }
      badge.hidden = true;
    }

    async function syncLatestProfileCount() {
      if (isSyncingProfileCount) return;
      const savedThumb = localStorage.getItem('brk_saved_face_thumb');
      const savedEmbJson = localStorage.getItem('brk_saved_embedding');
      let savedEmb = null;
      if (savedEmbJson) {
        try { savedEmb = JSON.parse(savedEmbJson); } catch (e) {}
      }
      if (!savedThumb && !savedEmb) return;

      const lastCount = parseInt(localStorage.getItem('brk_saved_count') || '0', 10);
      const acknowledgedCount = parseInt(localStorage.getItem('brk_acknowledged_count') || `${lastCount}`, 10);

      isSyncingProfileCount = true;
      try {
        const payload = {
          face_thumb: savedThumb,
          embedding: savedEmb,
          last_count: lastCount,
          page: 1,
          page_size: 50
        };
        let res = await fetch('/api/check-profile-matches', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!res.ok) {
          res = await fetch(`/api/events/${slug}/check-profile-matches`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
        }
        if (res.ok) {
          const data = await res.json();
          if (data.search_id) {
            currentSearchId = data.search_id;
            try { localStorage.setItem('brk_current_search_id', data.search_id); } catch (e) {}
          }
          if (data.creation_token) {
            currentCreationToken = data.creation_token;
            try { localStorage.setItem('brk_current_creation_token', data.creation_token); } catch (e) {}
          }
          if (data.embedding && !savedEmb) {
            try { localStorage.setItem('brk_saved_embedding', JSON.stringify(data.embedding)); } catch (e) {}
          }
          const newCount = (typeof data.total_count === 'number' && data.total_count > 0) ? data.total_count : data.count;
          if (typeof newCount === 'number' && newCount > 0) {
            localStorage.setItem('brk_saved_count', newCount);
            if (data.date_summary) {
              serverDateSummary = data.date_summary;
              try {
                localStorage.setItem('brk_saved_date_summary', JSON.stringify(data.date_summary));
              } catch (e) {}
            }
            if (data.photos && data.photos.length > 0) {
              localStorage.setItem('brk_saved_photos', JSON.stringify(data.photos));
            }

            const countText = $('savedPhotosCountText');
            if (countText) {
              countText.textContent = `Found ${newCount} photo${newCount === 1 ? '' : 's'} with BRK Sir`;
            }

            const newBadge = $('newBadgeIndicator');
            if (newBadge) {
              newBadge.hidden = !(newCount > acknowledgedCount);
            }

            if (newCount > acknowledgedCount) {
              const diff = newCount - acknowledgedCount;
              const banner = $('newPhotosAlertBanner');
              const alertText = $('newPhotosAlertText');
              if (banner && alertText) {
                alertText.textContent = `🎉 ${diff} new photo${diff === 1 ? '' : 's'} found with BRK Sir since your last visit!`;
                banner.hidden = false;
              }
            }
          }
        }
      } catch (err) {
        // Quiet background check
      } finally {
        isSyncingProfileCount = false;
      }
    }

    function viewSavedPhotos(e) {
      if (e) e.preventDefault();
      const savedPhotosJson = localStorage.getItem('brk_saved_photos');
      const savedCount = parseInt(localStorage.getItem('brk_saved_count') || '0', 10);
      const savedThumb = localStorage.getItem('brk_saved_face_thumb');
      if (!savedPhotosJson) return;

      localStorage.setItem('brk_acknowledged_count', savedCount);
      const newBadge = $('newBadgeIndicator');
      if (newBadge) newBadge.hidden = true;
      const banner = $('newPhotosAlertBanner');
      if (banner) banner.hidden = true;

      try {
        const photos = JSON.parse(savedPhotosJson);
        if (Array.isArray(photos) && photos.length > 0) {
          const actualCount = savedCount > 0 ? savedCount : photos.length;
          totalMatchingPhotosCount = actualCount;
          currentSearchFaceThumb = savedThumb || null;
          const savedEmbJson = localStorage.getItem('brk_saved_embedding');
          if (savedEmbJson) {
            try { currentSearchEmbedding = JSON.parse(savedEmbJson); } catch (e) {}
          }
          currentSearchId = localStorage.getItem('brk_current_search_id') || null;
          currentCreationToken = localStorage.getItem('brk_current_creation_token') || null;
          currentSearchPage = Math.max(1, Math.floor(photos.length / 50));
          currentSearchHasMore = actualCount > photos.length;

          const savedDateSummaryJson = localStorage.getItem('brk_saved_date_summary');
          if (savedDateSummaryJson) {
            try {
              serverDateSummary = JSON.parse(savedDateSummaryJson);
            } catch (e) {}
          }

          const statusEl = $('searchStatus');
          if (statusEl) {
            statusEl.innerHTML = `<span style="color:var(--emerald,#059669);font-weight:700;">✓ Restored ${actualCount} saved photo${actualCount === 1 ? '' : 's'} with BRK Sir!</span>`;
          }
          $('photosSectionTitle').textContent = 'Photos with BRK Sir';
          $('photosSectionSubtitle').textContent = actualCount === 1 ? '1 photograph found' : `${actualCount} photographs found`;
          renderPhotos(photos);
          openGalleryOverlay();
          if (currentSearchHasMore) {
            fetchNextSearchPage();
          }
        }
      } catch (err) {
        console.error('Error rendering saved photos from storage:', err);
      }
    }

    function clearSavedProfile(e) {
      if (e) {
        e.preventDefault();
        e.stopPropagation();
      }
      localStorage.removeItem('brk_saved_face_thumb');
      localStorage.removeItem('brk_saved_embedding');
      localStorage.removeItem('brk_saved_photos');
      localStorage.removeItem('brk_saved_count');
      localStorage.removeItem('brk_acknowledged_count');
      localStorage.removeItem('brk_saved_time');
      localStorage.removeItem('brk_current_search_id');
      localStorage.removeItem('brk_current_creation_token');
      currentSearchFaceThumb = null;
      currentSearchEmbedding = null;
      currentSearchId = null;
      currentCreationToken = null;
      currentSelectedFaceIndex = null;
      currentSearchHasMore = false;
      totalMatchingPhotosCount = 0;
      const badge = $('savedProfileBadge');
      if (badge) badge.hidden = true;
    }

    // --- Multi-Face Detection & Group Photo Handling ---
    function showMultiFacePicker(faces) {
      const picker = $('multiFacePicker');
      const list = $('multiFaceList');
      const actionsRow = $('finderActionsRow');
      if (!picker || !list) return;

      const allBtn = $('searchAllFacesBtn');
      if (allBtn) {
        const textSpan = $('searchAllFacesBtnText');
        if (textSpan) textSpan.textContent = `Search for All ${faces.length} People`;
        allBtn.onclick = () => {
          if (currentSearchFile) {
            processAndSearchSelfie(currentSearchFile, 'all');
          }
        };
      }

      list.replaceChildren();
      faces.forEach((f, idx) => {
        const item = document.createElement('div');
        item.className = 'multi-face-item';
        item.title = `Search photos for Person ${idx + 1}`;
        item.innerHTML = `
          <img src="${f.thumb || ''}" class="multi-face-avatar" alt="Person ${idx + 1}">
          <span class="multi-face-btn-label">Person ${idx + 1}</span>
        `;
        item.addEventListener('click', () => {
          picker.querySelectorAll('.multi-face-item').forEach(el => el.classList.remove('active'));
          item.classList.add('active');
          if (currentSearchFile) {
            processAndSearchSelfie(currentSearchFile, f.face_index);
          }
        });
        list.appendChild(item);
      });

      if (actionsRow) actionsRow.hidden = true;
      picker.hidden = false;
      picker.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function hideMultiFacePicker() {
      const picker = $('multiFacePicker');
      const actionsRow = $('finderActionsRow');
      if (picker) picker.hidden = true;
      if (actionsRow) actionsRow.hidden = false;
    }

    async function processAndSearchSelfie(file, faceIndex = null) {
      if (!file) return;
      currentSearchFile = file;

      const statusEl = $('searchStatus');
      if (statusEl) {
        const text = faceIndex === 'all'
          ? 'Searching photographs for all detected people with BRK Sir...'
          : (faceIndex !== null
            ? 'Searching photographs for selected person with BRK Sir...'
            : 'Analyzing faces with AI and searching photographs with BRK Sir...');
        statusEl.innerHTML = `<span style="display:inline-flex;align-items:center;gap:8px;color:var(--accent,#0f5c4d);font-weight:600;">
          <svg class="icon-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
          ${text}
        </span>`;
        statusEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }

      const fd = new FormData();
      fd.append('selfie', file);
      if (faceIndex !== null && faceIndex !== undefined) {
        fd.append('face_index', String(faceIndex));
      }
      fd.append('page', 1);
      fd.append('page_size', 50);
      fd.append('session_id', getSessionId());
      fd.append('visitor_id', getVisitorId());
      fd.append('device_type', getDeviceType());

      try {
        let res = await fetch('/api/search', { method: 'POST', body: fd });
        if (!res.ok && res.status !== 400 && res.status !== 413 && res.status !== 422) {
          res = await fetch(`/api/${slug}/search`, { method: 'POST', body: fd });
        }
        let j;
        try {
          j = await res.json();
        } catch (_) {
          const txt = await res.text().catch(() => '');
          j = { detail: txt || 'Search could not be completed.' };
        }
        if (!res.ok) throw new Error(j.detail || 'Search could not be completed.');

        // Check if multiple people were detected in group photo
        if (j.multiple_faces === true && j.faces && j.faces.length > 1) {
          if (statusEl) {
            statusEl.innerHTML = `<span style="color:#0f5c4d;font-weight:700;">👥 Found ${j.total_faces} people in this photo. Search for all people together or pick an individual:</span>`;
          }
          showMultiFacePicker(j.faces);
          return;
        }

        // Single face or face index search completed
        hideMultiFacePicker();

        if (j.photos && j.photos.length > 0) {
          const totalMatches = (typeof j.total_count === 'number' && j.total_count > 0)
            ? j.total_count
            : (typeof j.count === 'number' && j.count > 0 ? j.count : j.photos.length);
          totalMatchingPhotosCount = totalMatches;

          if (j.date_summary) {
            serverDateSummary = j.date_summary;
            try {
              localStorage.setItem('brk_saved_date_summary', JSON.stringify(j.date_summary));
            } catch (e) {}
          }

          currentSearchFaceThumb = j.face_thumb || null;
          currentSearchEmbedding = j.embedding || null;
          currentSearchId = j.search_id || null;
          currentCreationToken = j.creation_token || null;
          if (currentSearchId) {
            try { localStorage.setItem('brk_current_search_id', currentSearchId); } catch(e){}
          }
          if (currentCreationToken) {
            try { localStorage.setItem('brk_current_creation_token', currentCreationToken); } catch(e){}
          }
          currentSelectedFaceIndex = faceIndex;
          currentSearchPage = j.page || 1;
          currentSearchHasMore = !!j.has_more;

          // Save result & face thumbnail in localStorage for 1-click return access
          try {
            if (j.face_thumb) {
              localStorage.setItem('brk_saved_face_thumb', j.face_thumb);
            }
            if (j.embedding) {
              localStorage.setItem('brk_saved_embedding', JSON.stringify(j.embedding));
            }
            localStorage.setItem('brk_saved_photos', JSON.stringify(j.photos));
            localStorage.setItem('brk_saved_count', totalMatches);
            localStorage.setItem('brk_acknowledged_count', totalMatches);
            localStorage.setItem('brk_saved_time', new Date().toISOString());
            checkSavedProfile();
          } catch (e) {
            console.warn('LocalStorage save failed:', e);
          }

          if (statusEl) {
            const groupSuffix = j.is_group_search ? ` featuring any of the ${j.total_faces_in_query || 'group'} people` : '';
            statusEl.innerHTML = `<span style="color:var(--emerald,#059669);font-weight:700;">✓ Found ${totalMatches} photo${totalMatches === 1 ? '' : 's'}${groupSuffix} with BRK Sir!</span>`;
          }
          if ($('emptyResultsCard')) $('emptyResultsCard').hidden = true;
          $('photosSectionTitle').textContent = 'Photos with BRK Sir';
          const subText = j.is_group_search
            ? `${totalMatches} photographs found featuring any of the ${j.total_faces_in_query || 'group'} people`
            : (totalMatches === 1 ? '1 photograph found featuring your face' : `${totalMatches} unique photographs found featuring your face`);
          $('photosSectionSubtitle').textContent = subText;
          renderPhotos(j.photos);
          openGalleryOverlay();
          if (j.has_more) {
            fetchNextSearchPage();
          }
        } else {
          if (statusEl) {
            statusEl.innerHTML = `<span style="color:#d97706;font-weight:600;">No matching photos found with your face. See tips below:</span>`;
          }
          $('photosSectionTitle').textContent = 'Search Results';
          $('photosSectionSubtitle').textContent = '0 matching photographs found';
          $('photosGrid').replaceChildren();
          if ($('discoveryToolbar')) $('discoveryToolbar').hidden = true;
          if ($('emptyResultsCard')) $('emptyResultsCard').hidden = false;
          openGalleryOverlay();
        }
      } catch (err) {
        if (statusEl) {
          statusEl.innerHTML = `<span style="color:#dc2626;font-weight:600;">${err.message || 'Error searching photos. Please try again.'}</span>`;
        }
      }
    }

    // Wire Event Listeners
    $('cameraBtn').addEventListener('click', triggerCamera);
    $('uploadFileBtn').addEventListener('click', () => $('selfieFileInput').click());

    $('selfieFileInput').addEventListener('change', () => {
      if ($('selfieFileInput').files && $('selfieFileInput').files[0]) {
        processAndSearchSelfie($('selfieFileInput').files[0]);
      }
    });

    $('snapPhotoBtn')?.addEventListener('click', captureWebcamPhoto);
    $('retakePhotoBtn')?.addEventListener('click', retakeWebcamPhoto);
    $('usePhotoBtn')?.addEventListener('click', useCapturedPhoto);
    $('flipCameraBtn')?.addEventListener('click', toggleCameraFlip);
    $('cancelWebcamBtn')?.addEventListener('click', stopWebcam);
    $('closeWebcamModalBtn')?.addEventListener('click', stopWebcam);

    // Saved Profile Listeners
    $('viewSavedPhotosBtn')?.addEventListener('click', viewSavedPhotos);
    $('savedAvatarClickWrap')?.addEventListener('click', viewSavedPhotos);
    $('savedInfoClickWrap')?.addEventListener('click', viewSavedPhotos);
    $('clearSavedBtn')?.addEventListener('click', clearSavedProfile);

    // New Photos Alert Banner Listeners
    $('viewNewPhotosBannerBtn')?.addEventListener('click', viewSavedPhotos);
    $('closeNewPhotosBannerBtn')?.addEventListener('click', () => {
      const banner = $('newPhotosAlertBanner');
      if (banner) banner.hidden = true;
    });

    // Empty Results State Action Listeners
    $('emptyRetryBtn')?.addEventListener('click', triggerCamera);
    $('emptyUploadBtn')?.addEventListener('click', () => $('selfieFileInput').click());

    // Selection & Bulk Download Listeners
    $('toggleSelectBtn')?.addEventListener('click', () => toggleSelectionMode());
    $('bulkSelectAllBtn')?.addEventListener('click', selectAllPhotos);
    $('bulkDownloadZipBtn')?.addEventListener('click', downloadSelectedZip);
    $('bulkCancelBtn')?.addEventListener('click', clearSelection);

    // =========================================================================
    // MY EVENT ALBUM: CLIENT LOGIC
    // =========================================================================

    function updateMyAlbumsBadge() {
      const badge = $('myAlbumsBadge');
      const navBadge = $('navAlbumsBadge');
      try {
        const albums = JSON.parse(localStorage.getItem('my_shared_albums') || '[]');
        const activeCount = albums.filter(a => !a.is_revoked && (!a.expires_at || new Date(a.expires_at) > new Date())).length;
        if (badge) badge.style.display = activeCount > 0 ? 'inline-block' : 'none';
        if (navBadge) navBadge.style.display = activeCount > 0 ? 'inline-block' : 'none';
      } catch (e) {
        if (badge) badge.style.display = 'none';
        if (navBadge) navBadge.style.display = 'none';
      }
    }

    function generate256BitHexToken(prefix = '') {
      const arr = new Uint8Array(32);
      if (window.crypto && window.crypto.getRandomValues) {
        window.crypto.getRandomValues(arr);
      } else {
        for (let i = 0; i < 32; i++) arr[i] = Math.floor(Math.random() * 256);
      }
      const hex = Array.from(arr, b => b.toString(16).padStart(2, '0')).join('');
      return prefix ? (prefix + hex) : hex;
    }

    async function openCreateAlbumModal() {
      if (selectedPhotos.size === 0) {
        showToast('Please select at least one photograph to create an album.');
        return;
      }
      if (selectedPhotos.size > 100) {
        showToast('You can select up to 100 photographs for an album.');
        return;
      }

      let sId = currentSearchId || localStorage.getItem('brk_current_search_id');
      let cTok = currentCreationToken || localStorage.getItem('brk_current_creation_token');

      if (!sId || !cTok) {
        // Attempt automatic background renewal of search proof if biometric profile is saved
        const savedThumb = localStorage.getItem('brk_saved_face_thumb');
        const savedEmbJson = localStorage.getItem('brk_saved_embedding');
        let savedEmb = null;
        if (savedEmbJson) {
          try { savedEmb = JSON.parse(savedEmbJson); } catch (e) {}
        }

        if (savedThumb || savedEmb) {
          const albumBtn = $('bulkCreateAlbumBtn');
          const albumText = $('bulkCreateAlbumText');
          const origText = albumText ? albumText.textContent : 'Create Album';
          if (albumText) albumText.textContent = 'Verifying...';
          if (albumBtn) albumBtn.disabled = true;

          try {
            const renewRes = await fetch('/api/check-profile-matches', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                face_thumb: savedThumb,
                embedding: savedEmb,
                session_id: getSessionId(),
                visitor_id: getVisitorId(),
                page: 1,
                page_size: 50
              })
            });
            if (renewRes.ok) {
              const renewData = await renewRes.json();
              if (renewData.search_id && renewData.creation_token) {
                currentSearchId = renewData.search_id;
                currentCreationToken = renewData.creation_token;
                try {
                  localStorage.setItem('brk_current_search_id', currentSearchId);
                  localStorage.setItem('brk_current_creation_token', currentCreationToken);
                } catch (e) {}
                sId = currentSearchId;
                cTok = currentCreationToken;
              }
            }
          } catch (e) {
            console.warn('Could not auto-renew search proof:', e);
          } finally {
            if (albumBtn) albumBtn.disabled = false;
            if (albumText) albumText.textContent = origText;
          }
        }
      }

      if (!sId || !cTok) {
        showToast('Search proof expired. Please find photos with your selfie to re-verify your album.');
        return;
      }

      // Reset any previous creation errors
      const errAlert = $('albumCreateErrorAlert');
      if (errAlert) {
        errAlert.textContent = '';
        errAlert.style.display = 'none';
      }

      // Populate selected preview count & thumbnails
      const countEl = $('albumModalSelectedCount');
      if (countEl) countEl.textContent = `Selected Photos: ${selectedPhotos.size}`;

      const strip = $('albumModalThumbStrip');
      if (strip) {
        strip.replaceChildren();
        const allItems = (currentPhotosList && currentPhotosList.length > 0)
          ? currentPhotosList
          : (window.rawPhotosList || []);

        const selectedArray = Array.from(selectedPhotos);
        selectedArray.slice(0, 20).forEach(path => {
          const item = allItems.find(it => (typeof it === 'string' ? it : it.rel_path) === path);
          const enc = item ? getPhotoEnc(item) : encodeURIComponent(path);
          const img = document.createElement('img');
          img.src = `/thumb/${enc}`;
          img.alt = 'Selected photo';
          img.style.cssText = 'width:56px;height:56px;object-fit:cover;border-radius:8px;border:1px solid rgba(255,255,255,0.1);flex-shrink:0;';
          strip.appendChild(img);
        });

        if (selectedArray.length > 20) {
          const more = document.createElement('div');
          more.style.cssText = 'width:56px;height:56px;border-radius:8px;background:rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:center;font-size:0.75rem;font-weight:700;color:var(--muted,#9ca3af);flex-shrink:0;';
          more.textContent = `+${selectedArray.length - 20}`;
          strip.appendChild(more);
        }
      }

      // Set default title
      const titleInput = $('inputAlbumTitle');
      if (titleInput) {
        const evTitle = $('heroTitle')?.textContent?.trim() || 'BRK Sir';
        titleInput.value = `${evTitle} — My Moments`;
      }

      const expirySelect = $('selectAlbumExpiry');
      if (expirySelect) expirySelect.value = '30';

      const modal = $('createAlbumModalBackdrop');
      if (modal) {
        modal.classList.add('active');
        modal.style.display = 'flex';
      }
    }

    function closeCreateAlbumModal() {
      const modal = $('createAlbumModalBackdrop');
      if (modal) {
        modal.classList.remove('active');
        modal.style.display = 'none';
      }
    }

    async function confirmCreateAlbum() {
      const sId = currentSearchId || localStorage.getItem('brk_current_search_id');
      const cTok = currentCreationToken || localStorage.getItem('brk_current_creation_token');

      if (!sId || !cTok) {
        showToast('Search session proof missing. Please search again with your selfie.');
        return;
      }

      const allItems = (currentPhotosList && currentPhotosList.length > 0)
        ? currentPhotosList
        : (window.rawPhotosList || []);

      const photoIds = [];
      for (const path of selectedPhotos) {
        const item = allItems.find(it => (typeof it === 'string' ? it : it.rel_path) === path);
        if (item && item.id) {
          photoIds.push(item.id);
        }
      }

      if (photoIds.length === 0) {
        showToast('Could not resolve photo IDs. Please ensure photos are loaded and try again.');
        return;
      }

      const title = $('inputAlbumTitle')?.value?.trim() || 'My Event Album';
      const expiryDays = parseInt($('selectAlbumExpiry')?.value || '30', 10);

      const confirmBtn = $('btnConfirmCreateAlbum');
      const origText = confirmBtn ? confirmBtn.innerHTML : '';
      if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<svg class="icon-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><span>Creating...</span>';
      }

      // Persist client creation credentials before initial request to guarantee survival across retries/lost responses
      const pendingKey = `brk_pending_album_${sId}`;
      let clientShareToken = null;
      let clientManageToken = null;
      try {
        const cached = JSON.parse(localStorage.getItem(pendingKey) || '{}');
        const sortedPhotoIds = photoIds.slice().sort((a,b)=>a-b).join(',');
        if (cached && cached.photo_ids_str === sortedPhotoIds && cached.share_token && cached.manage_token) {
          clientShareToken = cached.share_token;
          clientManageToken = cached.manage_token;
        }
      } catch (e) {}

      if (!clientShareToken || !clientManageToken) {
        clientShareToken = generate256BitHexToken('sh_');
        clientManageToken = generate256BitHexToken('mg_');
        try {
          localStorage.setItem(pendingKey, JSON.stringify({
            search_id: sId,
            photo_ids_str: photoIds.slice().sort((a,b)=>a-b).join(','),
            share_token: clientShareToken,
            manage_token: clientManageToken,
            title: title,
            expiry_days: expiryDays,
            timestamp: Date.now()
          }));
        } catch (e) {}
      }

      try {
        const payload = {
          search_id: sId,
          creation_token: cTok,
          photo_ids: photoIds,
          title: title,
          expiry_days: expiryDays,
          share_token: clientShareToken,
          manage_token: clientManageToken
        };

        const res = await fetch('/api/shared-albums', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Session-Id': getSessionId()
          },
          body: JSON.stringify(payload)
        });

        let j;
        try {
          j = await res.json();
        } catch (_) {
          const txt = await res.text().catch(() => '');
          j = { detail: txt || 'Album creation failed.' };
        }
        if (!res.ok) {
          throw new Error(j.detail || 'Album creation failed.');
        }

        try {
          localStorage.removeItem(pendingKey);
        } catch (e) {}

        closeCreateAlbumModal();

        // Save album to localStorage for creator management
        const fullShareUrl = window.location.origin + (j.share_url || `/s/${j.share_token}`);
        const savedAlbums = JSON.parse(localStorage.getItem('my_shared_albums') || '[]');
        savedAlbums.unshift({
          id: j.id,
          title: j.title || title,
          share_token: j.share_token,
          manage_token: j.manage_token,
          share_url: fullShareUrl,
          photo_count: j.photo_count || photoIds.length,
          created_at: j.created_at || new Date().toISOString(),
          expires_at: j.expires_at,
          is_revoked: false
        });
        localStorage.setItem('my_shared_albums', JSON.stringify(savedAlbums));
        updateMyAlbumsBadge();

        // Clear selection, reset cards, exit selection mode, and restore gallery nav
        clearSelection();

        // Populate Success Modal
        const linkInput = $('successShareLinkInput');
        if (linkInput) linkInput.value = fullShareUrl;

        const compactText = $('compactShareLinkText');
        if (compactText) {
          compactText.textContent = getCompactDisplayUrl(fullShareUrl);
        }
        const compactPill = $('compactShareLinkDisplay');
        if (compactPill) {
          compactPill.setAttribute('data-full-url', fullShareUrl);
          compactPill.title = `Click to copy full link:\n${fullShareUrl}`;
        }

        const waBtn = $('btnWhatsAppShare');
        if (waBtn) {
          const waText = encodeURIComponent(`Here is my private photo album "${j.title || title}":\n${fullShareUrl}`);
          waBtn.href = `https://api.whatsapp.com/send?text=${waText}`;
        }

        const subTitle = $('successAlbumSubtitle');
        if (subTitle) {
          subTitle.textContent = `${photoIds.length} photograph${photoIds.length === 1 ? '' : 's'} included. Link expires in ${expiryDays} days.`;
        }

        const succModal = $('albumSuccessModalBackdrop');
        if (succModal) {
          succModal.classList.add('active');
          succModal.style.display = 'flex';
        }
        showToast('Private album created successfully!');
      } catch (err) {
        const errAlert = $('albumCreateErrorAlert');
        if (errAlert) {
          errAlert.textContent = err.message || 'Album creation failed.';
          errAlert.style.display = 'block';
        }
        showToast(`Failed to create album: ${err.message}`);
      } finally {
        if (confirmBtn) {
          confirmBtn.disabled = false;
          confirmBtn.innerHTML = origText;
        }
      }
    }

    function getCompactDisplayUrl(fullUrl) {
      if (!fullUrl) return 'photos.bhashyamramakrishna.in/s/…';
      try {
        const u = new URL(fullUrl, window.location.origin);
        return `${u.host}/s/…`;
      } catch (e) {
        return 'photos.bhashyamramakrishna.in/s/…';
      }
    }

    function closeSuccessModal() {
      const modal = $('albumSuccessModalBackdrop');
      if (modal) {
        modal.classList.remove('active');
        modal.style.display = 'none';
      }
    }

    function openMyAlbumsModal() {
      renderMyAlbumsList();
      const modal = $('myAlbumsModalBackdrop');
      if (modal) {
        modal.classList.add('active');
        modal.style.display = 'flex';
      }
    }

    function closeMyAlbumsModal() {
      const modal = $('myAlbumsModalBackdrop');
      if (modal) {
        modal.classList.remove('active');
        modal.style.display = 'none';
      }
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function renderMyAlbumsList() {
      const container = $('myAlbumsListContainer');
      if (!container) return;
      container.replaceChildren();

      let albums = [];
      try {
        albums = JSON.parse(localStorage.getItem('my_shared_albums') || '[]');
      } catch (e) {
        albums = [];
      }

      if (albums.length === 0) {
        container.innerHTML = `
          <div style="text-align:center;padding:2.5rem 1rem;color:#64748b;">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="1.8" style="margin:0 auto 0.75rem;"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
            <p style="font-weight:700;font-size:1rem;color:#0f172a;margin-bottom:0.25rem;">No albums created yet</p>
            <p style="font-size:0.82rem;color:#64748b;margin:0;">Select photos from your face search results and click "Create Album" to share them privately.</p>
          </div>
        `;
        return;
      }

      albums.forEach((album, idx) => {
        const isExpired = album.expires_at && new Date(album.expires_at) < new Date();
        const isRevoked = !!album.is_revoked;
        const statusText = isRevoked ? 'Revoked' : (isExpired ? 'Expired' : 'Active');
        const statusBg = isRevoked ? '#fee2e2' : (isExpired ? '#fef3c7' : '#dcfce7');
        const statusColor = isRevoked ? '#b91c1c' : (isExpired ? '#b45309' : '#15803d');
        const statusBorder = isRevoked ? '#fecaca' : (isExpired ? '#fde68a' : '#bbf7d0');
        const compactUrl = getCompactDisplayUrl(album.share_url);

        const card = document.createElement('div');
        card.style.cssText = 'background:#f8fafc;border:1.5px solid #e2e8f0;border-radius:12px;padding:0.95rem;display:flex;flex-direction:column;gap:0.75rem;box-shadow:0 1px 3px rgba(0,0,0,0.02);';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem;">
            <div>
              <h4 style="font-size:1.05rem;font-weight:700;margin:0 0 0.25rem;color:#0f172a;line-height:1.3;">${escapeHtml(album.title || 'My Event Album')}</h4>
              <p style="font-size:0.8rem;color:#64748b;margin:0;">
                ${album.photo_count || 0} photos • Created ${album.created_at ? new Date(album.created_at).toLocaleDateString() : ''}
              </p>
            </div>
            <span style="display:inline-block;padding:3px 12px;border-radius:9999px;font-size:0.75rem;font-weight:700;background:${statusBg};color:${statusColor};border:1px solid ${statusBorder};white-space:nowrap;">
              ${statusText}
            </span>
          </div>

          <div style="display:flex;flex-direction:column;gap:0.6rem;">
            ${(!isRevoked && !isExpired) ? `
              <!-- Compact Link Box -->
              <div class="compact-link-pill btn-copy-album-link" data-url="${album.share_url}"
                   style="background:#ffffff;border:1.5px solid #cbd5e1;border-radius:8px;padding:0 0.75rem;height:38px;min-height:38px;flex:none;display:flex;align-items:center;gap:0.55rem;width:100%;box-sizing:border-box;cursor:pointer;"
                   title="Click to copy full link:\n${album.share_url}" role="button" tabindex="0">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="2.2" style="flex-shrink:0;">
                  <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
                  <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
                </svg>
                <span class="compact-link-text" style="color:#334155;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:0.86rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${compactUrl}</span>
              </div>

              <!-- Action Buttons Row -->
              <div style="display:flex;align-items:center;gap:0.45rem;flex-wrap:wrap;width:100%;">
                <!-- Copy Action -->
                <button type="button" class="btn-copy-action btn-copy-album-link" data-url="${album.share_url}"
                  style="background:#0f5c4d;color:#ffffff;border:none;border-radius:8px;padding:0 0.85rem;height:38px;min-height:38px;font-size:0.82rem;font-weight:700;display:inline-flex;align-items:center;gap:6px;cursor:pointer;white-space:nowrap;"
                  title="Copy full link to clipboard">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  <span class="btn-copy-label">Copy Link</span>
                </button>

                <!-- WhatsApp Official Brand SVG Icon Button -->
                <a href="https://api.whatsapp.com/send?text=${encodeURIComponent('Here is my photo album: ' + album.share_url)}" target="_blank" rel="noopener noreferrer"
                  class="btn-whatsapp-icon"
                  style="width:38px;height:38px;min-width:38px;min-height:38px;background:#25d366;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;color:#fff;text-decoration:none;border:none;flex-shrink:0;"
                  aria-label="Share on WhatsApp"
                  title="Share on WhatsApp">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                    <path fill-rule="evenodd" clip-rule="evenodd" d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91C2.13 13.66 2.59 15.36 3.45 16.86L2.05 22L7.3 20.63C8.75 21.41 10.38 21.83 12.04 21.83C17.5 21.83 21.95 17.38 21.95 11.92C21.95 9.27 20.92 6.78 19.05 4.91C17.18 3.03 14.69 2 12.04 2ZM12.04 20.15C10.55 20.15 9.09 19.75 7.81 19L7.51 18.82L4.39 19.64L5.22 16.6L5.02 16.29C4.16 14.93 3.71 13.44 3.71 11.91C3.71 7.32 7.45 3.58 12.04 3.58C14.27 3.58 16.36 4.45 17.93 6.02C19.5 7.59 20.37 9.68 20.37 11.91C20.37 16.5 16.63 20.15 12.04 20.15ZM16.6 14.34C16.35 14.21 15.12 13.61 14.89 13.52C14.66 13.43 14.49 13.39 14.32 13.64C14.15 13.89 13.66 14.47 13.51 14.64C13.36 14.81 13.21 14.83 12.96 14.71C12.71 14.58 11.91 14.32 10.96 13.47C10.22 12.81 9.72 11.99 9.57 11.74C9.42 11.49 9.55 11.36 9.68 11.23C9.79 11.12 9.93 10.94 10.06 10.79C10.19 10.64 10.23 10.54 10.31 10.37C10.39 10.2 10.35 10.06 10.29 9.93C10.23 9.8 9.74 8.59 9.53 8.1C9.33 7.62 9.12 7.69 8.97 7.68L8.49 7.67C8.32 7.67 8.06 7.73 7.83 7.98C7.6 8.23 6.95 8.84 6.95 10.08C6.95 11.32 7.85 12.52 7.98 12.69C8.11 12.86 9.75 15.38 12.26 16.46C12.86 16.72 13.32 16.87 13.68 16.99C14.28 17.18 14.82 17.15 15.26 17.09C15.75 17.02 16.76 16.48 16.97 15.89C17.18 15.3 17.18 14.8 17.12 14.69C17.06 14.58 16.85 14.47 16.6 14.34Z"/>
                  </svg>
                </a>

                <!-- Open Action -->
                <a href="${album.share_url}" target="_blank" rel="noopener noreferrer"
                  style="background:#ffffff;border:1.5px solid #0f5c4d;color:#0f5c4d;border-radius:8px;padding:0 0.85rem;height:38px;min-height:38px;font-size:0.82rem;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:6px;white-space:nowrap;"
                  title="Open album in new tab">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0f5c4d" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
                  </svg>
                  <span>Open</span>
                </a>

                <!-- Revoke Link Action -->
                <button type="button" class="btn-revoke-album" data-idx="${idx}" data-album-id="${album.id}" data-manage-token="${album.manage_token || ''}"
                  style="margin-left:auto;background:#ffffff;border:1.5px solid #dc2626;color:#dc2626;border-radius:8px;padding:0 0.85rem;height:38px;min-height:38px;font-size:0.82rem;font-weight:700;cursor:pointer;white-space:nowrap;">
                  Revoke Link
                </button>
              </div>
            ` : `
              <!-- Revoked or Expired State Notice -->
              <div style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;padding:0.65rem 0.85rem;font-size:0.8rem;color:#64748b;display:flex;align-items:center;gap:6px;">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>
                <span>This link is ${statusText.toLowerCase()} and cannot be viewed by recipients.</span>
              </div>
            `}
          </div>
        `;
        container.appendChild(card);
      });

      // Wire copy buttons in drawer
      container.querySelectorAll('.btn-copy-album-link').forEach(btn => {
        btn.addEventListener('click', () => {
          const url = btn.getAttribute('data-url');
          if (!url) return;
          const notify = () => {
            const label = btn.querySelector('.btn-copy-label');
            if (label) {
              const orig = label.textContent;
              label.textContent = '✓ Copied!';
              setTimeout(() => { label.textContent = orig; }, 2000);
            }
            showToast('Link copied to clipboard!');
          };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(url).then(notify).catch(() => {
              fallbackCopyText(url);
              notify();
            });
          } else {
            fallbackCopyText(url);
            notify();
          }
        });
      });

      // Wire revoke buttons in drawer
      container.querySelectorAll('.btn-revoke-album').forEach(btn => {
        btn.addEventListener('click', async () => {
          const idx = parseInt(btn.getAttribute('data-idx'), 10);
          const album = albums[idx];
          const albumId = (album && album.id) || btn.getAttribute('data-album-id');
          const mgToken = (album && album.manage_token) || btn.getAttribute('data-manage-token');
          if (!albumId || !mgToken) {
            showToast('Management credentials missing for this album.');
            return;
          }
          if (!confirm('Are you sure you want to revoke this album link? Anyone with the link will no longer be able to view these photos.')) {
            return;
          }

          btn.disabled = true;
          btn.textContent = 'Revoking...';
          try {
            const res = await fetch(`/api/shared-albums/${encodeURIComponent(albumId)}/revoke`, {
              method: 'POST',
              headers: {
                'X-Manage-Token': mgToken
              }
            });
            if (!res.ok) {
              const j = await res.json().catch(() => ({}));
              throw new Error(j.detail || 'Revocation failed.');
            }
            albums[idx].is_revoked = true;
            localStorage.setItem('my_shared_albums', JSON.stringify(albums));
            updateMyAlbumsBadge();
            renderMyAlbumsList();
            showToast('Album link revoked successfully.');
          } catch (e) {
            showToast('Could not revoke album: ' + e.message);
            btn.disabled = false;
            btn.textContent = 'Revoke Link';
          }
        });
      });
    }

    function fallbackCopyText(text) {
      const input = document.createElement('textarea');
      input.value = text;
      input.style.position = 'fixed';
      input.style.opacity = '0';
      document.body.appendChild(input);
      input.focus();
      input.select();
      try { document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(input);
    }

    function copyFullSuccessLink() {
      const link = $('successShareLinkInput')?.value || $('compactShareLinkDisplay')?.getAttribute('data-full-url');
      if (!link) return;

      const finishCopy = () => {
        const btnText = $('btnCopySuccessText');
        if (btnText) {
          const orig = btnText.textContent;
          btnText.textContent = '✓ Copied!';
          setTimeout(() => { btnText.textContent = orig; }, 2200);
        }
        const fb = $('copyFeedbackToast');
        if (fb) {
          fb.style.display = 'inline';
          setTimeout(() => { fb.style.display = 'none'; }, 2200);
        }
        showToast('Link copied to clipboard!');
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(link).then(finishCopy).catch(() => {
          fallbackCopyText(link);
          finishCopy();
        });
      } else {
        fallbackCopyText(link);
        finishCopy();
      }
    }

    // My Event Album Listeners
    $('bulkCreateAlbumBtn')?.addEventListener('click', openCreateAlbumModal);
    $('openMyAlbumsBtn')?.addEventListener('click', openMyAlbumsModal);
    $('btnCloseCreateAlbumModal')?.addEventListener('click', closeCreateAlbumModal);
    $('btnCancelCreateAlbum')?.addEventListener('click', closeCreateAlbumModal);
    $('btnConfirmCreateAlbum')?.addEventListener('click', confirmCreateAlbum);
    $('btnCloseSuccessModal')?.addEventListener('click', closeSuccessModal);
    $('btnCloseMyAlbumsModal')?.addEventListener('click', closeMyAlbumsModal);

    $('btnCopySuccessLink')?.addEventListener('click', copyFullSuccessLink);
    $('compactShareLinkDisplay')?.addEventListener('click', copyFullSuccessLink);
    $('compactShareLinkDisplay')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        copyFullSuccessLink();
      }
    });

    // Dismiss modals on backdrop click
    $('createAlbumModalBackdrop')?.addEventListener('click', (e) => {
      if (e.target === $('createAlbumModalBackdrop')) closeCreateAlbumModal();
    });
    $('albumSuccessModalBackdrop')?.addEventListener('click', (e) => {
      if (e.target === $('albumSuccessModalBackdrop')) closeSuccessModal();
    });
    $('myAlbumsModalBackdrop')?.addEventListener('click', (e) => {
      if (e.target === $('myAlbumsModalBackdrop')) closeMyAlbumsModal();
    });

    // Initialize My Albums Badge
    updateMyAlbumsBadge();

    // More Sheet Open / Close
    function openMoreSheet() {
      const backdrop = $('moreSheetBackdrop');
      if (!backdrop) return;
      backdrop.hidden = false;
      requestAnimationFrame(() => {
        backdrop.classList.add('active');
      });
      document.body.style.overflow = 'hidden';
    }

    function closeMoreSheet() {
      const backdrop = $('moreSheetBackdrop');
      if (!backdrop) return;
      backdrop.classList.remove('active');
      setTimeout(() => {
        backdrop.hidden = true;
        document.body.style.overflow = '';
      }, 250);
    }

    // Mobile Bottom Navigation Actions
    $('navBtnPhotos')?.addEventListener('click', () => {
      const scrollEl = $('galleryScrollBody');
      if (scrollEl) scrollEl.scrollTo({ top: 0, behavior: 'smooth' });
      document.querySelectorAll('.bottom-nav-item').forEach(btn => btn.classList.remove('active'));
      $('navBtnPhotos')?.classList.add('active');
    });
    $('navBtnFilter')?.addEventListener('click', openFilterSheet);
    $('navBtnAlbums')?.addEventListener('click', openMyAlbumsModal);
    $('navBtnMore')?.addEventListener('click', openMoreSheet);

    // More Sheet Listeners
    $('closeMoreSheetBtn')?.addEventListener('click', closeMoreSheet);
    $('moreSheetDragHandle')?.addEventListener('click', closeMoreSheet);
    $('moreSheetBackdrop')?.addEventListener('click', (e) => {
      if (e.target === $('moreSheetBackdrop')) closeMoreSheet();
    });
    $('sheetSelectPhotosBtn')?.addEventListener('click', () => {
      closeMoreSheet();
      toggleSelectionMode(true);
    });
    $('sheetNewSearchBtn')?.addEventListener('click', () => {
      closeMoreSheet();
      $('closePhotosBtn')?.click();
    });
    $('mobileRemoveActiveFilterBtn')?.addEventListener('click', () => {
      selectedCalendarDate = null;
      selectedOtherAlbum = null;
      applyPhotoFiltersAndSort();
      updateMainFilterControls();
    });

    // Discovery & Sort Listeners
    $('sortOrderBtn')?.addEventListener('click', toggleSortOrder);

    // Main Page Filter Actions
    $('openFilterSheetBtn')?.addEventListener('click', openFilterSheet);
    $('allPhotosChip')?.addEventListener('click', () => {
      selectedCalendarDate = null;
      selectedOtherAlbum = null;
      applyPhotoFiltersAndSort();
      updateMainFilterControls();
    });
    $('removeActiveFilterBtn')?.addEventListener('click', () => {
      selectedCalendarDate = null;
      selectedOtherAlbum = null;
      applyPhotoFiltersAndSort();
      updateMainFilterControls();
    });

    // Filter Bottom Sheet Actions
    $('sheetSortNewestBtn')?.addEventListener('click', () => {
      draftSortOrder = 'newest';
      renderSheetContent();
    });
    $('sheetSortOldestBtn')?.addEventListener('click', () => {
      draftSortOrder = 'oldest';
      renderSheetContent();
    });
    $('closeFilterSheetBtn')?.addEventListener('click', closeFilterSheet);
    $('filterSheetBackdrop')?.addEventListener('click', (e) => {
      if (e.target === $('filterSheetBackdrop')) closeFilterSheet();
    });
    $('sheetClearSelectionBtn')?.addEventListener('click', clearSheetDraftFilters);
    $('sheetClearAllBtn')?.addEventListener('click', clearSheetDraftFilters);
    $('sheetApplyBtn')?.addEventListener('click', applySheetFilters);
    $('sheetOtherAlbumsToggle')?.addEventListener('click', () => {
      const drawer = $('sheetOtherAlbumsDrawer');
      const btn = $('sheetOtherAlbumsToggle');
      if (drawer && btn) {
        drawer.hidden = !drawer.hidden;
        btn.classList.toggle('open', !drawer.hidden);
      }
    });

    // Calendar Navigation inside Filter Sheet
    $('calPrevMonthBtn')?.addEventListener('click', () => {
      hasUserNavigatedSheetMonth = true;
      sheetViewMonth--;
      if (sheetViewMonth < 0) {
        sheetViewMonth = 11;
        sheetViewYear--;
      }
      renderSheetContent();
    });
    $('calNextMonthBtn')?.addEventListener('click', () => {
      hasUserNavigatedSheetMonth = true;
      sheetViewMonth++;
      if (sheetViewMonth > 11) {
        sheetViewMonth = 0;
        sheetViewYear++;
      }
      renderSheetContent();
    });

    // Mobile Drag / Swipe Down to Dismiss Filter Sheet
    let sheetTouchStartY = 0;
    let sheetCurrentDiffY = 0;
    let isSheetDragging = false;
    const sheetContainer = $('filterSheetContainer');

    if (sheetContainer) {
      sheetContainer.addEventListener('touchstart', (e) => {
        if (e.touches.length !== 1) return;
        const content = $('filterSheetContent');
        if (e.target.closest('#filterSheetDragHandle, .filter-sheet-header') || (content && content.scrollTop <= 0)) {
          sheetTouchStartY = e.touches[0].clientY;
          sheetCurrentDiffY = 0;
          isSheetDragging = true;
          sheetContainer.style.transition = 'none';
        }
      }, { passive: true });

      sheetContainer.addEventListener('touchmove', (e) => {
        if (!isSheetDragging) return;
        const currentY = e.touches[0].clientY;
        const diffY = currentY - sheetTouchStartY;
        if (diffY > 0) {
          sheetCurrentDiffY = diffY;
          sheetContainer.style.transform = `translateY(${diffY}px)`;
          if (e.cancelable) e.preventDefault();
        } else {
          sheetCurrentDiffY = 0;
          sheetContainer.style.transform = '';
        }
      }, { passive: false });

      sheetContainer.addEventListener('touchend', () => {
        if (!isSheetDragging) return;
        isSheetDragging = false;
        sheetContainer.style.transition = 'transform 0.25s cubic-bezier(0.16, 1, 0.3, 1)';
        if (sheetCurrentDiffY > 80) {
          closeFilterSheet();
        } else {
          sheetContainer.style.transform = '';
        }
      }, { passive: true });
    }

    // Lightbox Desktop Double-Click Zoom & Native Share
    lightboxWrap?.addEventListener('dblclick', e => {
      if (e.target.closest('button, a, .lightbox-zoom-bar')) return;
      if (zoomScale > 1.2) {
        resetZoom();
      } else {
        setZoom(2.2);
      }
    });

    $('lightboxShareBtn')?.addEventListener('click', () => {
      const curItem = currentPhotosList[currentPhotoIndex];
      if (!curItem) return;
      const enc = getPhotoEnc(curItem);
      emitAnalyticsEvent('share_clicked', { photo_path: enc });
      const downloadUrl = `${window.location.origin}/download/hd/${enc}`;
      if (navigator.share) {
        navigator.share({
          title: 'Photo with BRK Sir',
          text: 'Check out my photograph with Bhashyam Ramakrishna Sir!',
          url: downloadUrl
        }).catch(() => {});
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(downloadUrl).then(() => {
          showToast('Photo download link copied to clipboard!');
        }).catch(() => {
          showToast('Photo link: ' + downloadUrl);
        });
      } else {
        showToast('Photo link: ' + downloadUrl);
      }
    });

    // Multi-Face Cancel / Reset
    $('cancelMultiFaceBtn')?.addEventListener('click', () => {
      hideMultiFacePicker();
      const statusEl = $('searchStatus');
      if (statusEl) statusEl.innerHTML = '';
      $('selfieFileInput').click();
    });

    // Register PWA Service Worker
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').then(reg => {
          console.log('BRK Photos PWA ServiceWorker registered with scope:', reg.scope);
        }).catch(err => {
          console.warn('BRK Photos PWA ServiceWorker registration failed:', err);
        });
      });
    }

    // Check for saved profile on startup and sync dynamic counts
    checkSavedProfile();
    syncLatestProfileCount();
    // Periodic background profile sync - only when page is visible
    setInterval(() => {
      if (document.visibilityState === 'visible') {
        syncLatestProfileCount();
      }
    }, 45000);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        syncLatestProfileCount();
      }
    });
    // Container-level long-press & drag suppression on gallery photos & lightbox
    const photosGridEl = $('photosGrid');
    if (photosGridEl) {
      photosGridEl.addEventListener('contextmenu', (e) => {
        if (e.target.closest('.photo-card, .photo-img-wrap, img, a')) {
          e.preventDefault();
        }
      });
      photosGridEl.addEventListener('dragstart', (e) => {
        if (e.target.closest('.photo-card, .photo-img-wrap, img, a')) {
          e.preventDefault();
        }
      });
    }

    const lightboxModalEl = $('photoLightboxModal');
    if (lightboxModalEl) {
      lightboxModalEl.addEventListener('contextmenu', (e) => {
        if (e.target.closest('.lightbox-slide-slot, .lightbox-img-wrap, .filmstrip-thumb, img, a')) {
          e.preventDefault();
        }
      });
      lightboxModalEl.addEventListener('dragstart', (e) => {
        if (e.target.closest('.lightbox-slide-slot, .lightbox-img-wrap, .filmstrip-thumb, img, a')) {
          e.preventDefault();
        }
      });
    }

    window.addEventListener('resize', updateMobileNavigationState);
    updateMobileNavigationState();
    loadEvent();


// Expose key functions to window for automated tests & external triggers
window.viewSavedPhotos = viewSavedPhotos;
window.closeGalleryOverlay = closeGalleryOverlay;
window.openGalleryOverlay = openGalleryOverlay;
window.toggleSelectionMode = toggleSelectionMode;
window.togglePhotoSelect = togglePhotoSelect;
window.confirmCreateAlbum = confirmCreateAlbum;
window.openCreateAlbumModal = openCreateAlbumModal;
window.closeCreateAlbumModal = closeCreateAlbumModal;
window.openMyAlbumsModal = openMyAlbumsModal;
window.closeMyAlbumsModal = closeMyAlbumsModal;
window.selectAllPhotos = selectAllPhotos;
window.clearSelection = clearSelection;
window.renderPhotos = renderPhotos;
window.applyPhotoFiltersAndSort = applyPhotoFiltersAndSort;
window.openPhotoLightbox = openPhotoLightbox;
window.closeLightbox = closeLightbox;
window.triggerCamera = triggerCamera;
