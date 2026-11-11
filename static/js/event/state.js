// State management and common utility primitives for public event gallery
export const $ = id => document.getElementById(id);
export const slug = window.location.pathname.replace(/^\/|\/$/g, '') || 'brk-sir';
export const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const state = {
  slug,
  eventData: null,
  currentPhotosList: [],
  rawPhotosList: [],
  currentPhotoIndex: 0,
  totalMatchingPhotosCount: 0,
  sortOrder: 'newest', // 'newest' | 'oldest'

  // Applied Filters State
  selectedCalendarDate: null, // 'YYYY-MM-DD'
  selectedOtherAlbum: null,   // rawAlbum string e.g. 'PART 01'
  serverDateSummary: null,    // Server aggregated date summary
  datePhotoCounts: new Map(), // 'YYYY-MM-DD' -> count
  otherAlbumCounts: new Map(), // rawAlbum -> count
  newestCalendarDate: null,

  // History & Scroll
  isClosingLightboxViaCode: false,
  lastGalleryScrollTop: 0,

  // Draft Filter Sheet State
  draftCalendarDate: null,
  draftOtherAlbum: null,
  draftSortOrder: 'newest',
  sheetViewYear: 2026,
  sheetViewMonth: 10,
  hasUserNavigatedSheetMonth: false,

  // Selection Mode State
  isSelectionMode: false,
  selectedPhotos: new Set(),

  // Progressive Grid Batching
  gridRenderQueue: [],
  gridQueueCursor: 0,
  gridScrollObserver: null,
  isRenderingBatch: false,
  GRID_BATCH_SIZE: 40,

  // Search & Pagination Session State
  currentSearchFile: null,
  currentSearchFaceThumb: null,
  currentSearchEmbedding: null,
  currentSelectedFaceIndex: null,
  currentSearchId: null,
  currentCreationToken: null,
  currentSearchPage: 1,
  isFetchingNextSearchPage: false,
  hasMoreSearchPages: false,

  // Camera & Webcam Stream State
  webcamStream: null,
  webcamFacingMode: 'user',
  capturedBlob: null
};

// --- Privacy-Preserving Analytics Telemetry ---
export function getVisitorId() {
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

export function getSessionId() {
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

export function getDeviceType() {
  const w = window.innerWidth;
  const ua = navigator.userAgent || '';
  if (/Mobi|Android|iPhone/i.test(ua) || w <= 640) return 'mobile';
  if (/iPad|Tablet/i.test(ua) || (w > 640 && w <= 1024)) return 'tablet';
  return 'desktop';
}

export function emitAnalyticsEvent(eventType, extra = {}) {
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

let photoViewDebounceTimer = null;
const viewedPhotosInSession = new Set();

export function trackGenuinePhotoView(item) {
  if (!item) return;
  if (photoViewDebounceTimer) clearTimeout(photoViewDebounceTimer);
  const photoPath = typeof item === 'string' ? item : (item.rel_path || item.filename || '');
  if (!photoPath) return;

  photoViewDebounceTimer = setTimeout(() => {
    if (!viewedPhotosInSession.has(photoPath)) {
      viewedPhotosInSession.add(photoPath);
      const parsed = parsePhotoDate(item);
      emitAnalyticsEvent('photo_opened', {
        photo_path: photoPath,
        album_name: parsed.title || ''
      });
    }
  }, 1000);
}

let toastTimeout = null;
export function showToast(msg) {
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

export function normalizeBaseFilename(fname) {
  if (!fname || typeof fname !== 'string') return '';
  const base = fname.split('/').pop().toLowerCase().trim();
  const lastDot = base.lastIndexOf('.');
  const stem = lastDot > 0 ? base.substring(0, lastDot) : base;
  return stem
    .replace(/[\s_]*\((?:[0-9]+|copy(?:\s+[0-9]+)?)\)$/i, '')
    .replace(/[\s_-]+copy(?:[\s_-]*[0-9]+|\([0-9]+\))?$/i, '')
    .trim();
}

export function parseDateString(text) {
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

export function parsePhotoDate(item) {
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

export function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
