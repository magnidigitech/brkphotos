// BRK Photos Admin - State & Utility Store (ES Module)
export const adminState = {
  get storedSlug() {
    let s = localStorage.getItem('admin_active_event_slug');
    if (!s || ['wedding', 'anilkumar-vengamamba', 'default'].includes(s)) {
      s = 'brk-sir';
      localStorage.setItem('admin_active_event_slug', 'brk-sir');
    }
    return s;
  },
  set storedSlug(v) {
    localStorage.setItem('admin_active_event_slug', v);
  },
  activeSlug: 'brk-sir',
  allEvents: [],
  allAlbums: [],
  currentEvent: null,
  selectedPhotoIds: new Set(),
  activeFilterAlbumId: '',
  activeUploadAlbumSlug: '',
  lastHistoryRefreshTime: 0,
  lastSidebarAutoSyncTime: 0,
  lastQueueRefreshTime: 0,
  lastAutoPhotoRefreshTime: 0,
  hasActiveQueueTasks: false,
};

// Initialize activeSlug from stored
adminState.activeSlug = adminState.storedSlug;

// Shared state reactive binding across ES modules
const sharedKeys = [
  'activeSlug', 'allAlbums', 'allEvents', 'currentEvent',
  'selectedPhotoIds', 'activeFilterAlbumId', 'activeUploadAlbumSlug',
  'lastHistoryRefreshTime', 'lastSidebarAutoSyncTime', 'lastQueueRefreshTime',
  'lastAutoPhotoRefreshTime', 'hasActiveQueueTasks'
];

sharedKeys.forEach(key => {
  Object.defineProperty(globalThis, key, {
    get: () => adminState[key],
    set: (v) => { adminState[key] = v; },
    configurable: true
  });
});

export const $ = id => document.getElementById(id);
export const $$ = sel => document.querySelectorAll(sel);

function formatDateDDMMYYYY(d) {
  const day = String(d.getDate()).padStart(2, '0');
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const year = d.getFullYear();
  return `${day}-${month}-${year}`;
}

function formatDateYYYYMMDD(d) {
  const day = String(d.getDate()).padStart(2, '0');
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const year = d.getFullYear();
  return `${year}-${month}-${day}`;
}

function generateSlug(text) {
  return (text || '')
    .toString()
    .toLowerCase()
    .trim()
    .replace(/[\s_]+/g, '-')       // replace spaces and underscores with hyphens
    .replace(/[^a-z0-9\-]/g, '')   // remove disallowed characters except letters, digits, and hyphens
    .replace(/-+/g, '-')           // collapse consecutive hyphens
    .replace(/^-|-$/g, '');        // trim leading/trailing hyphens
}


const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
];
const MONTH_ABBRS = {
  jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
  jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12
};

function parseAlbumDate(name, slug = '') {
  const str = `${name} ${slug}`.toLowerCase();
  
  // 1. Check DD-MM-YYYY or DD-MM-YY or DD/MM/YYYY or DD.MM.YY or DD MM YY
  const dmyMatch = str.match(/\b(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{2,4})\b/);
  if (dmyMatch) {
    let day = parseInt(dmyMatch[1], 10);
    let month = parseInt(dmyMatch[2], 10);
    let year = parseInt(dmyMatch[3], 10);
    if (year < 100) year += 2000;
    if (month > 12 && day <= 12) {
      const tmp = day; day = month; month = tmp;
    }
    if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
      const monthKey = `${year}-${String(month).padStart(2, '0')}`;
      const monthLabel = `${MONTH_NAMES[month - 1]} ${year}`;
      const sortVal = year * 10000 + month * 100 + day;
      return { year, month, day, monthKey, monthLabel, sortVal, hasDate: true };
    }
  }

  // 2. Check YYYY-MM-DD
  const ymdMatch = str.match(/\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b/);
  if (ymdMatch) {
    let year = parseInt(ymdMatch[1], 10);
    let month = parseInt(ymdMatch[2], 10);
    let day = parseInt(ymdMatch[3], 10);
    if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
      const monthKey = `${year}-${String(month).padStart(2, '0')}`;
      const monthLabel = `${MONTH_NAMES[month - 1]} ${year}`;
      const sortVal = year * 10000 + month * 100 + day;
      return { year, month, day, monthKey, monthLabel, sortVal, hasDate: true };
    }
  }

  // 3. Text month e.g. "01 JUN 2026" or "JUN 2026" or "01 JUN"
  const textMonthMatch = str.match(/\b(\d{1,2})?[-/\s.]*([a-z]{3,9})[-/\s.]*(\d{2,4})?\b/);
  if (textMonthMatch) {
    const dStr = textMonthMatch[1];
    const mStr = textMonthMatch[2].slice(0, 3);
    const yStr = textMonthMatch[3];
    if (MONTH_ABBRS[mStr]) {
      const month = MONTH_ABBRS[mStr];
      let day = dStr ? parseInt(dStr, 10) : 1;
      let year = yStr ? parseInt(yStr, 10) : new Date().getFullYear();
      if (year < 100) year += 2000;
      const monthKey = `${year}-${String(month).padStart(2, '0')}`;
      const monthLabel = `${MONTH_NAMES[month - 1]} ${year}`;
      const sortVal = year * 10000 + month * 100 + day;
      return { year, month, day, monthKey, monthLabel, sortVal, hasDate: true };
    }
  }

  // 4. Check MM-YYYY or MM-YY (e.g. "10-2026" or "10-26")
  const myMatch = str.match(/\b(\d{1,2})[-/.](\d{2,4})\b/);
  if (myMatch) {
    let month = parseInt(myMatch[1], 10);
    let year = parseInt(myMatch[2], 10);
    if (year < 100) year += 2000;
    if (month >= 1 && month <= 12 && year >= 2000 && year <= 2099) {
      const monthKey = `${year}-${String(month).padStart(2, '0')}`;
      const monthLabel = `${MONTH_NAMES[month - 1]} ${year}`;
      const sortVal = year * 10000 + month * 100 + 1;
      return { year, month, day: 1, monthKey, monthLabel, sortVal, hasDate: true };
    }
  }

  return { year: 0, month: 0, day: 0, monthKey: 'other', monthLabel: 'Other Collections', sortVal: -1, hasDate: false };
}

// Render Dynamic Subfolders in Sidebar (Sorted Latest Date First)

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// AJAX Search for Albums & Photos
let albumSearchDebounceTimer = null;


function parseDateUtcOrLocal(dateVal) {
  if (!dateVal) return null;
  if (dateVal instanceof Date) return dateVal;
  const s = String(dateVal).trim();
  if (!s || s === 'None' || s === 'null') return null;
  // If it's SQLite CURRENT_TIMESTAMP like "YYYY-MM-DD HH:MM:SS" without offset, it's stored in UTC
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(s)) {
    return new Date(s.replace(' ', 'T') + 'Z');
  }
  if (!s.endsWith('Z') && !s.includes('+') && !s.includes('T')) {
    return new Date(s.replace(' ', 'T') + 'Z');
  }
  return new Date(s);
}

function formatToIST(dateVal, includeDate = true) {
  if (!dateVal) return '—';
  try {
    const d = parseDateUtcOrLocal(dateVal);
    if (!d || isNaN(d.getTime())) return String(dateVal);
    const opts = {
      timeZone: 'Asia/Kolkata',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    };
    if (includeDate) {
      opts.day = 'numeric';
      opts.month = 'short';
    }
    return d.toLocaleString('en-IN', opts) + (includeDate ? ' IST' : '');
  } catch (e) {
    return String(dateVal);
  }
}

function formatTimeShort(dateStr) {
  return formatToIST(dateStr, false);
}

let isFetchingQueue = false;
let currentQueueTasksList = [];


export {
  escapeHtml,
  formatDateDDMMYYYY,
  formatDateYYYYMMDD,
  parseAlbumDate,
  parseDateUtcOrLocal,
  formatToIST,
  formatTimeShort,
  generateSlug,
  MONTH_NAMES
};
