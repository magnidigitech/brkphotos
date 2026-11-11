// BRK Photos Admin - API & Modal Helpers (ES Module)
import { $ } from './state.js?v=2.2.0';
import { closeMobileSidebar } from './dashboard.js?v=2.2.0';

const originalFetch = window.fetch;
window.fetch = async function(url, options = {}) {
  options = options || {};
  options.headers = options.headers || {};
  const token = localStorage.getItem('admin_token');
  if (token) {
    if (options.headers instanceof Headers) {
      if (!options.headers.has('X-Admin-Token')) options.headers.set('X-Admin-Token', token);
    } else if (Array.isArray(options.headers)) {
      if (!options.headers.some(([k]) => k.toLowerCase() === 'x-admin-token')) {
        options.headers.push(['X-Admin-Token', token]);
      }
    } else {
      if (!options.headers['X-Admin-Token']) {
        options.headers['X-Admin-Token'] = token;
      }
    }
  }
  if (!options.credentials) {
    options.credentials = 'same-origin';
  }
  const res = await originalFetch.call(window, url, options);
  // Auto-redirect to login on 401 Unauthorized for admin API calls
  if (res.status === 401 && typeof url === 'string' && url.includes('/api/admin/') && !url.includes('/api/admin/login')) {
    localStorage.removeItem('admin_token');
    $('authView').hidden = false;
    $('appShell').hidden = true;
  }
  return res;
};


function openModal(id) {
  closeMobileSidebar();
  $(id).hidden = false;
}
function closeModal(id) { $(id).hidden = true; }


let pendingDeleteAction = null;
let pendingDeleteExpected = '';
function openDeleteConfirmModal(title, desc, expectedText, confirmCallback, confirmBtnLabel = 'Delete') {
  $('deleteModalTitle').textContent = title;
  $('deleteModalDesc').innerHTML = desc;
  pendingDeleteAction = confirmCallback;
  pendingDeleteExpected = (expectedText || '').trim();

  const inputWrap = $('deleteModalInputWrap');
  const input = $('deleteConfirmInput');
  const btn = $('deleteModalConfirmBtn');

  if (pendingDeleteExpected) {
    if (inputWrap) inputWrap.hidden = false;
    if ($('deleteExpectedText')) $('deleteExpectedText').textContent = pendingDeleteExpected;
    if (input) input.value = '';
    btn.disabled = true;
  } else {
    if (inputWrap) inputWrap.hidden = true;
    btn.disabled = false;
  }
  btn.textContent = confirmBtnLabel;
  openModal('deleteConfirmModal');
  if (pendingDeleteExpected && input) {
    setTimeout(() => input.focus(), 100);
  }
}

$('deleteConfirmInput')?.addEventListener('input', e => {
  const typed = e.target.value.trim();
  const match = typed.toLowerCase() === pendingDeleteExpected.toLowerCase();
  $('deleteModalConfirmBtn').disabled = !match;
});

$('deleteModalConfirmBtn')?.addEventListener('click', async () => {
  if (pendingDeleteAction) {
    const btn = $('deleteModalConfirmBtn');
    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = 'Processing...';
    try {
      await pendingDeleteAction();
    } catch (e) {
      alert('Error during delete: ' + e);
    } finally {
      btn.disabled = false;
      btn.textContent = oldText;
      closeModal('deleteConfirmModal');
      pendingDeleteAction = null;
      pendingDeleteExpected = '';
    }
  }
});

// AJAX Refresh Photo Library
let isRefreshingPhotos = false;

async function safeFetchJson(url, options = {}, timeoutMs = 15000) {
  let timer = null;
  let signal = options.signal;
  if (!signal && timeoutMs > 0 && typeof AbortController !== 'undefined') {
    const controller = new AbortController();
    signal = controller.signal;
    timer = setTimeout(() => controller.abort(), timeoutMs);
  }
  try {
    const fetchOptions = { ...options };
    if (signal) fetchOptions.signal = signal;
    const res = await fetch(url, fetchOptions);
    if (timer) clearTimeout(timer);

    // Read body text exactly once to avoid ReadableStream already-read errors
    let rawText = '';
    try {
      rawText = await res.text();
    } catch (_) {
      rawText = '';
    }

    let data = null;
    if (rawText && rawText.trim()) {
      try {
        data = JSON.parse(rawText);
      } catch (_) {
        const clean = rawText.trim();
        const isHtmlOrRaw = clean.startsWith('<') || clean.toLowerCase().includes('internal server error') || clean.length > 200;
        data = {
          detail: isHtmlOrRaw
            ? (res.status >= 500 ? 'Service temporarily unavailable. Please try again shortly.' : `Server returned error (${res.status}).`)
            : clean
        };
      }
    } else {
      data = { detail: res.ok ? '' : `Server returned error (${res.status}).` };
    }

    return { ok: res.ok, status: res.status, data };
  } catch (netErr) {
    if (timer) clearTimeout(timer);
    const isTimeout = netErr.name === 'AbortError';
    return {
      ok: false,
      status: 0,
      data: {
        detail: isTimeout
          ? 'Network request timed out. Please check your connection and try again.'
          : (netErr.message || 'Network connection failed')
      }
    };
  }
}

let isLoggingIn = false;


export {
  originalFetch,
  safeFetchJson,
  openModal,
  closeModal,
  openDeleteConfirmModal
};
