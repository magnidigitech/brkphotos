const $ = id => document.getElementById(id);

// Global Authenticated Fetch Wrapper ensuring X-Admin-Token & credentials
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

let storedSlug = localStorage.getItem('admin_active_event_slug');
if (!storedSlug || ['wedding', 'anilkumar-vengamamba', 'default'].includes(storedSlug)) {
  storedSlug = 'brk-sir';
  localStorage.setItem('admin_active_event_slug', 'brk-sir');
}
let activeSlug = storedSlug;
let allEvents = [];
let allAlbums = [];
let currentEvent = null;
let selectedPhotoIds = new Set();
let activeFilterAlbumId = '';
let activeUploadAlbumSlug = '';
let lastHistoryRefreshTime = 0;
let lastSidebarAutoSyncTime = 0;
let lastQueueRefreshTime = 0;
let lastAutoPhotoRefreshTime = 0;
let hasActiveQueueTasks = false;

// Mobile Sidebar Off-Canvas Navigation Controls
function openMobileSidebar() {
  const sb = $('mainSidebar');
  const bd = $('sidebarBackdrop');
  if (sb) sb.classList.add('mobile-open');
  if (bd) bd.classList.add('active');
  if (window.innerWidth <= 992) {
    document.body.style.overflow = 'hidden';
  }
}

function closeMobileSidebar() {
  const sb = $('mainSidebar');
  const bd = $('sidebarBackdrop');
  if (sb) sb.classList.remove('mobile-open');
  if (bd) bd.classList.remove('active');
  document.body.style.overflow = '';
}

function toggleMobileSidebar() {
  const sb = $('mainSidebar');
  if (sb && sb.classList.contains('mobile-open')) {
    closeMobileSidebar();
  } else {
    openMobileSidebar();
  }
}

// Close sidebar on Escape key
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeMobileSidebar();
  }
});

// Close sidebar when window is resized past mobile breakpoint
window.addEventListener('resize', () => {
  if (window.innerWidth > 992) {
    closeMobileSidebar();
  }
});

// Navigation Tabs Handling with State Persistence
function switchTab(tabName) {
  closeMobileSidebar();
  // Sync Sidebar active states (photos tab maps to albums in sidebar)
  const sidebarTab = tabName === 'photos' ? 'albums' : tabName;
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.getAttribute('data-tab') === sidebarTab);
  });

  // Hide all panels, show target
  document.querySelectorAll('main.workspace-canvas section').forEach(sec => sec.hidden = true);
  const target = $(`panel-${tabName}`);
  if (target) target.hidden = false;

  localStorage.setItem('admin_active_tab', tabName);
  try {
    const url = new URL(window.location);
    url.searchParams.set('event', activeSlug);
    url.searchParams.set('tab', tabName);
    window.history.replaceState({}, '', url);
  } catch(e) {}

  if (tabName === 'dashboard') loadDashboard();
  if (tabName === 'events') loadEvents();
  if (tabName === 'albums') loadAlbums();
  if (tabName === 'photos') {
    loadPhotos(activeFilterAlbumId);
    loadDuplicatesBadge();
  }
  if (tabName === 'people') loadPeople();
  if (tabName === 'upload') setupUploadTab();
  if (tabName === 'storage') loadStorage();
  if (tabName === 'analytics') loadAnalytics();
  if (tabName === 'logs') {
    loadLogs();
    loadIndexingErrors();
  }
  if (tabName === 'trash') loadTrash();
  if (tabName === 'settings') loadSystemSettings();
  if (tabName === 'shared-albums') loadAdminSharedAlbums();
}

function openAllPhotos() {
  closeMobileSidebar();
  activeFilterAlbumId = '';
  const filterSelect = $('filterAlbumSelect');
  if (filterSelect) filterSelect.value = '';
  switchTab('photos');
  loadPhotos('', false);
}

document.querySelectorAll('.sidebar-nav-list > .nav-item').forEach(el => {
  el.addEventListener('click', () => {
    closeMobileSidebar();
    const tab = el.getAttribute('data-tab');
    if (tab === 'photos') activeFilterAlbumId = ''; // Reset album filter when clicking Photo Library directly
    switchTab(tab);
  });
});

function openModal(id) {
  closeMobileSidebar();
  $(id).hidden = false;
}
function closeModal(id) { $(id).hidden = true; }

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

function openNewAlbumModal() {
  const today = new Date();
  const dateFormatted = formatDateDDMMYYYY(today);
  const pickerVal = formatDateYYYYMMDD(today);
  
  if ($('albumDatePicker')) $('albumDatePicker').value = pickerVal;
  if ($('albumNameInput')) $('albumNameInput').value = dateFormatted;
  if ($('albumSlugInput')) $('albumSlugInput').value = dateFormatted;
  
  openModal('albumModal');
  setTimeout(() => $('albumNameInput')?.focus(), 80);
}

// Delete Confirmation Modal Handling with Text Input Verification
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
async function ajaxRefreshPhotos() {
  if (isRefreshingPhotos) return;
  isRefreshingPhotos = true;
  const icon = $('ajaxRefreshIcon');
  const txt = $('ajaxRefreshBtnText');
  if (icon) icon.style.transform = 'rotate(360deg)';
  if (txt) txt.textContent = 'Syncing...';
  try {
    await loadPhotos($('filterAlbumSelect')?.value || '');
    await loadAlbums();
  } catch (e) {
    console.error('AJAX refresh error:', e);
  } finally {
    setTimeout(() => {
      if (icon) icon.style.transform = 'none';
      if (txt) txt.textContent = 'Refresh';
      isRefreshingPhotos = false;
    }, 450);
  }
}

$('thresholdRange')?.addEventListener('input', e => {
  $('thresholdVal').textContent = parseFloat(e.target.value).toFixed(2);
});

// Check Authentication Session with Saved State
async function checkAuth() {
  try {
    const res = await fetch('/api/admin/overview');
    if (res.ok) {
      const data = await res.json();
      $('authView').hidden = true;
      $('appShell').hidden = false;

      const events = data.events || [];
      const urlParams = new URLSearchParams(window.location.search);
      const targetSlug = urlParams.get('event') || localStorage.getItem('admin_active_event_slug');
      if (targetSlug && events.some(e => e.slug === targetSlug)) {
        activeSlug = targetSlug;
      } else if (events.length > 0) {
        activeSlug = events[0].slug;
      }
      localStorage.setItem('admin_active_event_slug', activeSlug);

      populateEventSelector(events);
      await setEventWorkspace(activeSlug, false);

      initSidebarCollapsibles();
      setupAlbumSearch();

      const targetTab = urlParams.get('tab') || localStorage.getItem('admin_active_tab') || 'dashboard';
      switchTab(targetTab);
      pollTaskStatus();
      return true;
    } else {
      localStorage.removeItem('admin_token');
      $('authView').hidden = false;
      $('appShell').hidden = true;
      return false;
    }
  } catch (e) {
    console.error('checkAuth error:', e);
    if (!localStorage.getItem('admin_token')) {
      $('authView').hidden = false;
      $('appShell').hidden = true;
    }
    return false;
  }
}

let currentChallengeId = null;

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let str = '';
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function base64urlToBuffer(base64url) {
  let str = (base64url || '').replace(/-/g, '+').replace(/_/g, '/');
  while (str.length % 4 !== 0) str += '=';
  const binary = atob(str);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function resetLoginSteps() {
  currentChallengeId = null;
  if ($('stepPasswordWrap')) $('stepPasswordWrap').hidden = false;
  if ($('step2faWrap')) $('step2faWrap').hidden = true;
  if ($('stepRecoveryWrap')) $('stepRecoveryWrap').hidden = true;
  if ($('loginAlert')) $('loginAlert').hidden = true;
  if ($('step2faAlert')) $('step2faAlert').hidden = true;
  if ($('stepRecoveryAlert')) $('stepRecoveryAlert').hidden = true;
  if ($('adminKey')) $('adminKey').focus();
}

function show2faStep() {
  if ($('stepPasswordWrap')) $('stepPasswordWrap').hidden = true;
  if ($('step2faWrap')) $('step2faWrap').hidden = false;
  if ($('stepRecoveryWrap')) $('stepRecoveryWrap').hidden = true;
  if ($('totpCodeInput')) {
    $('totpCodeInput').value = '';
    $('totpCodeInput').focus();
  }
}

function showRecoveryCodeStep() {
  if ($('stepPasswordWrap')) $('stepPasswordWrap').hidden = true;
  if ($('step2faWrap')) $('step2faWrap').hidden = true;
  if ($('stepRecoveryWrap')) $('stepRecoveryWrap').hidden = false;
  if ($('recoveryCodeInput')) {
    $('recoveryCodeInput').value = '';
    $('recoveryCodeInput').focus();
  }
}

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

async function handleAdminLogin(e) {
  if (e && typeof e.preventDefault === 'function') e.preventDefault();
  if (isLoggingIn) return false;

  const keyInput = $('adminKey');
  if (!keyInput) return false;
  const key = keyInput.value.trim();
  if (!key) {
    keyInput.focus();
    return false;
  }

  isLoggingIn = true;
  const btn = $('loginBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Authenticating...';
  }
  const alertEl = $('loginAlert');
  if (alertEl) {
    alertEl.hidden = true;
    alertEl.textContent = '';
  }

  // Safety fallback: ensure button is never stuck indefinitely
  const safetyTimer = setTimeout(() => {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Sign In';
    }
    isLoggingIn = false;
  }, 25000);

  try {
    const { ok, status, data: j } = await safeFetchJson('/api/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key })
    }, 20000);

    clearTimeout(safetyTimer);

    // Check if 2FA is required
    if (ok && j && j.status === '2fa_required') {
      currentChallengeId = j.challenge_id;
      show2faStep();
      return false;
    }

    if (ok && j && j.token) {
      localStorage.setItem('admin_token', j.token);
      localStorage.setItem('admin_active_tab', 'dashboard');
      $('authView').hidden = true;
      $('appShell').hidden = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Sign In';
      }
      isLoggingIn = false;
      checkAuth().catch(err => console.error('checkAuth post-login error:', err));
      return true;
    } else {
      const errMsg = (j && (j.detail || j.message)) || (status === 401 ? 'Incorrect security passkey or password.' : 'Authentication failed. Please try again.');
      if (alertEl) {
        alertEl.textContent = errMsg;
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    clearTimeout(safetyTimer);
    if (alertEl) {
      alertEl.textContent = 'Server connection error: ' + (err.message || 'Unable to reach backend.');
      alertEl.hidden = false;
    }
  } finally {
    clearTimeout(safetyTimer);
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Sign In';
    }
    isLoggingIn = false;
  }
  return false;
}

async function handle2faVerify() {
  const codeInput = $('totpCodeInput');
  if (!codeInput || !currentChallengeId) return;
  const code = codeInput.value.trim();
  if (!code) return;

  const btn = $('step2faBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Verifying...';
  }
  const alertEl = $('step2faAlert');
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/auth/2fa/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ challenge_id: currentChallengeId, code: code })
    }, 10000);
    if (ok && j.token) {
      localStorage.setItem('admin_token', j.token);
      localStorage.setItem('admin_active_tab', 'dashboard');
      $('authView').hidden = true;
      $('appShell').hidden = false;
      resetLoginSteps();
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Verify & Sign In';
      }
      checkAuth().catch(err => console.error('checkAuth post-2fa error:', err));
      return;
    } else {
      if (alertEl) {
        alertEl.textContent = j.detail || 'Invalid 6-digit authenticator code.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Connection error: ' + (err.message || 'Verification failed.');
      alertEl.hidden = false;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Verify & Sign In';
    }
  }
}

async function handleRecoveryCodeVerify() {
  const codeInput = $('recoveryCodeInput');
  if (!codeInput || !currentChallengeId) return;
  const code = codeInput.value.trim();
  if (!code) return;

  const btn = $('stepRecoveryBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Verifying...';
  }
  const alertEl = $('stepRecoveryAlert');
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/auth/recovery-code/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ challenge_id: currentChallengeId, recovery_code: code })
    }, 10000);
    if (ok && j.token) {
      localStorage.setItem('admin_token', j.token);
      localStorage.setItem('admin_active_tab', 'dashboard');
      if (j.warning) {
        alert(j.warning);
      }
      $('authView').hidden = true;
      $('appShell').hidden = false;
      resetLoginSteps();
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Authenticate with Recovery Code';
      }
      checkAuth().catch(err => console.error('checkAuth post-recovery error:', err));
      return;
    } else {
      if (alertEl) {
        alertEl.textContent = j.detail || 'Invalid or already-consumed recovery code.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Connection error: ' + (err.message || 'Recovery failed.');
      alertEl.hidden = false;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Authenticate with Recovery Code';
    }
  }
}

async function handlePasskeyLogin() {
  const alertEl = $('loginAlert');
  if (alertEl) alertEl.hidden = true;

  if (!window.PublicKeyCredential) {
    if (alertEl) {
      alertEl.textContent = 'Passkeys are not supported by this browser. Please use password login.';
      alertEl.hidden = false;
    }
    return;
  }

  const btn = $('passkeyLoginBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Connecting Passkey...';
  }

  try {
    // 1. Fetch challenge from backend safely
    const { ok: chalOk, data: chalData } = await safeFetchJson('/api/admin/auth/passkey/generate-challenge', { method: 'POST' }, 10000);
    if (!chalOk) {
      throw new Error(chalData?.detail || 'Could not initialize passkey login.');
    }

    // 2. Format options for WebAuthn get
    const publicKeyOptions = {
      challenge: base64urlToBuffer(chalData.challenge),
      rpId: chalData.rpId,
      allowCredentials: (chalData.allowCredentials || []).map(c => ({
        type: c.type,
        id: base64urlToBuffer(c.id),
        transports: c.transports
      })),
      userVerification: chalData.userVerification || 'preferred',
      timeout: chalData.timeout || 60000
    };

    // 3. Request credential from browser / authenticator
    const credential = await navigator.credentials.get({ publicKey: publicKeyOptions });
    if (!credential) throw new Error('Passkey verification was cancelled.');

    // 4. Send assertion to server
    const clientDataJSON = bufferToBase64url(credential.response.clientDataJSON);
    const authenticatorData = bufferToBase64url(credential.response.authenticatorData);
    const signature = bufferToBase64url(credential.response.signature);
    const credentialId = credential.id;

    const { ok: verifyOk, data: verifyData } = await safeFetchJson('/api/admin/auth/passkey/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: chalData.challenge_id,
        credential_id: credentialId,
        client_data_json: clientDataJSON,
        authenticator_data: authenticatorData,
        signature: signature
      })
    }, 10000);
    if (!verifyOk) {
      throw new Error(verifyData?.detail || 'Passkey verification failed on server.');
    }

    // 5. Store token & enter admin console
    localStorage.setItem('admin_token', verifyData.token);
    localStorage.setItem('admin_active_tab', 'dashboard');
    $('authView').hidden = true;
    $('appShell').hidden = false;
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `
        <svg class="icon" style="width:18px;height:18px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm0 2a3 3 0 0 1 3 3v3H9V7a3 3 0 0 1 3-3z"/><circle cx="12" cy="16" r="1"/></svg>
        Sign in with Passkey / Face ID
      `;
    }
    checkAuth().catch(err => console.error('checkAuth post-passkey error:', err));
  } catch (err) {
    if (err.name !== 'NotAllowedError') {
      if (alertEl) {
        alertEl.textContent = (err.message || 'Passkey authentication failed.');
        alertEl.hidden = false;
      }
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `
        <svg class="icon" style="width:18px;height:18px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm0 2a3 3 0 0 1 3 3v3H9V7a3 3 0 0 1 3-3z"/><circle cx="12" cy="16" r="1"/></svg>
        Sign in with Passkey / Face ID
      `;
    }
  }
}

// Single submission handler wired to loginForm onsubmit (duplicate listener removed)

$('logoutBtn').addEventListener('click', async () => {
  localStorage.removeItem('admin_token');
  localStorage.removeItem('admin_active_tab');
  try {
    await fetch('/api/admin/logout', { method: 'POST' });
  } catch (e) {}
  $('appShell').hidden = true;
  $('authView').hidden = false;
  window.location.href = '/admin';
});

// Event Selector & Workspace Management
function populateEventSelector(events) {
  allEvents = events || [];
  if (events && events.length > 0) {
    activeSlug = events[0].slug;
  }
  updateActiveEventButtons();
}

$('activeEventSelect')?.addEventListener('change', async e => {
  await setEventWorkspace(e.target.value, true);
});

function updateActiveEventButtons() {
  if ($('btnViewActiveEvent')) $('btnViewActiveEvent').href = '/';
}

async function setEventWorkspace(slug, refreshActiveTab = true) {
  activeSlug = slug || 'brk-sir';
  if (activeSlug === 'wedding' || activeSlug === 'default') activeSlug = 'brk-sir';
  if ($('activeEventSelect')) $('activeEventSelect').value = activeSlug;
  updateActiveEventButtons();

  // Fetch event workspace details
  try {
    let res = await fetch(`/api/admin/albums`);
    if (!res.ok) res = await fetch(`/api/admin/events/${slug}`);
    if (res.ok) {
      const d = await res.json();
      currentEvent = d.event || { name: 'BRK Photos', slug: activeSlug };
      allAlbums = d.albums || [];
      $('sidebarAlbumCount').textContent = allAlbums.length;

      // Render dynamic sidebar subfolders
      renderSidebarSubfolders();

      // Render upload manager targets
      renderUploadTargets();

      // Update panel headers
      if ($('albumsPanelTitle')) $('albumsPanelTitle').textContent = `Albums & Folders`;
      if ($('photosPanelTitle')) $('photosPanelTitle').textContent = `Photo Library`;

      if (refreshActiveTab) {
        const activeTab = document.querySelector('.sidebar-nav-list > .nav-item.active')?.getAttribute('data-tab') || 'albums';
        switchTab(activeTab);
      }
    }
  } catch (e) {
    console.error('Failed to load workspace:', e);
  }
}

// Date Parser for Albums & Months
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
function renderSidebarSubfolders() {
  const tree = $('sidebarSubfoldersList');
  tree.replaceChildren();

  if (allAlbums.length === 0) {
    const empty = document.createElement('li');
    empty.style.padding = '0.2rem 0.5rem';
    empty.style.color = '#9ca3af';
    empty.style.fontSize = '0.75rem';
    empty.textContent = 'No folders yet';
    tree.append(empty);
  } else {
    // Sort subfolders latest date first
    const sortedSidebarAlbums = allAlbums.slice().sort((a, b) => {
      const aVal = a._date ? a._date.sortVal : parseAlbumDate(a.name, a.slug).sortVal;
      const bVal = b._date ? b._date.sortVal : parseAlbumDate(b.name, b.slug).sortVal;
      if (bVal !== aVal) return bVal - aVal;
      return b.name.localeCompare(a.name, undefined, { numeric: true });
    });

    for (const a of sortedSidebarAlbums) {
      const li = document.createElement('li');
      li.className = 'subfolder-item';
      li.id = `subfolder-item-${a.id}`;
      li.innerHTML = `
        <div style="display:flex;align-items:center;gap:0.4rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
          <svg class="icon-sm" viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <span style="overflow:hidden;text-overflow:ellipsis;">${a.name}</span>
        </div>
        <span class="subfolder-badge">${a.photo_count || 0}</span>
      `;
      li.addEventListener('click', () => {
        closeMobileSidebar();
        openAlbumPhotos(a.id, a.name);
      });
      tree.append(li);
    }
  }

  // Add subtle "+ New Folder" link at bottom
  const addLink = document.createElement('li');
  addLink.className = 'subfolder-create-link';
  addLink.innerHTML = `
    <svg class="icon-sm" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
    <span>New Folder</span>
  `;
  addLink.addEventListener('click', () => {
    closeMobileSidebar();
    openNewAlbumModal();
  });
  tree.append(addLink);

  const albumsCollapsed = localStorage.getItem('admin_albums_tree_collapsed') === 'true';
  if (albumsCollapsed) {
    tree.classList.add('collapsed');
    $('toggleAlbumsTreeBtn')?.classList.add('collapsed');
  }
}

// Render Upload Targets in Sidebar and Upload Panel
function renderUploadTargets() {
  const tree = $('sidebarUploadTargetsList');
  tree.replaceChildren();

  for (const a of allAlbums) {
    const li = document.createElement('li');
    li.className = 'subfolder-item';
    li.innerHTML = `
      <div style="display:flex;align-items:center;gap:0.4rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
        <svg class="icon-sm" viewBox="0 0 24 24"><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <span style="overflow:hidden;text-overflow:ellipsis;">${a.name}</span>
      </div>
    `;
    li.addEventListener('click', () => {
      closeMobileSidebar();
      selectUploadTargetAlbum(a.slug, a.name);
      switchTab('upload');
    });
    tree.append(li);
  }

  const uploadCollapsed = localStorage.getItem('admin_upload_tree_collapsed') === 'true';
  if (uploadCollapsed) {
    tree.classList.add('collapsed');
    $('toggleUploadTreeBtn')?.classList.add('collapsed');
  }

  // Setup chips in Upload Manager panel
  setupUploadTab();
}

function initSidebarCollapsibles() {
  const albumsTree = $('sidebarSubfoldersList');
  const uploadTree = $('sidebarUploadTargetsList');
  const albumsToggleBtn = $('toggleAlbumsTreeBtn');
  const uploadToggleBtn = $('toggleUploadTreeBtn');

  const albumsCollapsed = localStorage.getItem('admin_albums_tree_collapsed') === 'true';
  const uploadCollapsed = localStorage.getItem('admin_upload_tree_collapsed') === 'true';

  if (albumsTree && albumsCollapsed) {
    albumsTree.classList.add('collapsed');
    albumsToggleBtn?.classList.add('collapsed');
  }
  if (uploadTree && uploadCollapsed) {
    uploadTree.classList.add('collapsed');
    uploadToggleBtn?.classList.add('collapsed');
  }

  albumsToggleBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!albumsTree) return;
    const isCollapsed = albumsTree.classList.toggle('collapsed');
    albumsToggleBtn.classList.toggle('collapsed', isCollapsed);
    localStorage.setItem('admin_albums_tree_collapsed', isCollapsed);
  });

  uploadToggleBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!uploadTree) return;
    const isCollapsed = uploadTree.classList.toggle('collapsed');
    uploadToggleBtn.classList.toggle('collapsed', isCollapsed);
    localStorage.setItem('admin_upload_tree_collapsed', isCollapsed);
  });
}

function openAlbumPhotos(albumId, albumName) {
  activeFilterAlbumId = albumId;
  switchTab('photos');
  
  // Highlight active subfolder in sidebar
  document.querySelectorAll('.subfolder-item').forEach(el => el.classList.remove('active'));
  const activeLi = $(`subfolder-item-${albumId}`);
  if (activeLi) activeLi.classList.add('active');
}

function selectUploadTargetAlbum(slug, name) {
  activeUploadAlbumSlug = slug;
  if ($('uploadTargetAlbumSelect')) $('uploadTargetAlbumSelect').value = slug;
  if ($('gdriveTargetAlbumSelect')) $('gdriveTargetAlbumSelect').value = slug;
  if ($('activeUploadFolderLabel')) $('activeUploadFolderLabel').textContent = `Destination: ${name} (slug: ${slug})`;
  
  // Highlight active chip
  document.querySelectorAll('.folder-chip').forEach(c => {
    c.classList.toggle('active', c.getAttribute('data-slug') === slug);
  });
}

$('btnFastIndexActive')?.addEventListener('click', async (e) => {
  e?.preventDefault();
  const btn = $('btnFastIndexActive');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const originalHtml = btn.innerHTML;
  btn.innerHTML = `<svg class="icon-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><span>Starting Indexing...</span>`;

  // Immediately redirect to Activity Logs page without waiting
  switchTab('logs');

  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/index`, { method: 'POST' });
    if (!res.ok && res.status === 409) {
      console.warn('Indexing task already running');
    }
  } catch (err) {
    console.error('Failed to trigger indexing:', err);
  } finally {
    pollTaskStatus();
    loadTaskHistory(true);
    setTimeout(() => {
      btn.innerHTML = originalHtml;
      btn.disabled = false;
    }, 2000);
  }
});

// Dashboard
async function loadDashboard() {
  try {
    const res = await fetch('/api/admin/overview');
    if (res.ok) {
      const d = await res.json();
      if ($('metricAlbums')) $('metricAlbums').textContent = d.metrics.total_albums || 0;
      if ($('metricActiveAlbums')) $('metricActiveAlbums').textContent = d.metrics.total_albums || 0;
      if ($('metricPhotos')) $('metricPhotos').textContent = d.metrics.total_photos || 0;
      if ($('metricPeople')) $('metricPeople').textContent = d.metrics.identified_people || 0;
    }
  } catch (e) {}
  loadSystemMetrics();
}

async function loadSystemMetrics(isManual = false) {
  try {
    const res = await fetch('/api/admin/system/metrics');
    if (!res.ok) return;
    const d = await res.json();
    if ($('sysCpuLoad') && d.load_avg) {
      $('sysCpuLoad').textContent = d.load_avg.map(n => Number(n).toFixed(2)).join(', ');
    }
    if ($('sysMemRss')) {
      $('sysMemRss').textContent = `${d.memory_rss_mb || 0} MB`;
    }
    if ($('sysDiskUsage') && d.disk) {
      $('sysDiskUsage').textContent = `${d.disk.free_gb || 0} GB Free`;
      if ($('sysDiskSub')) {
        $('sysDiskSub').textContent = `${d.disk.used_gb || 0} / ${d.disk.total_gb || 0} GB (${d.disk.used_pct || 0}% used)`;
      }
    }
    if ($('sysDbSize')) {
      $('sysDbSize').textContent = `${d.db_size_mb || 0} MB`;
    }
    if ($('sysStatusBadge')) {
      const isHealthy = (d.status === 'healthy');
      $('sysStatusBadge').textContent = isHealthy ? 'HEALTHY' : 'CHECK REQ';
      $('sysStatusBadge').style.background = isHealthy ? 'rgba(5,150,105,0.12)' : 'rgba(239,68,68,0.12)';
      $('sysStatusBadge').style.color = isHealthy ? '#059669' : '#ef4444';
    }
    if (isManual && typeof showToast === 'function') {
      showToast('System health metrics refreshed.');
    }
  } catch (e) {
    console.error('Error loading system metrics:', e);
  }
}

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

function setupAlbumSearch() {
  const input = $('albumSearchInput');
  const clearBtn = $('clearAlbumSearchBtn');
  const indicator = $('albumSearchIndicator');
  const resultsContainer = $('albumSearchResults');
  const albumsGrid = $('albumsGrid');

  if (!input) return;

  clearBtn?.addEventListener('click', () => {
    input.value = '';
    clearBtn.hidden = true;
    if (indicator) indicator.hidden = true;
    if (resultsContainer) resultsContainer.hidden = true;
    if (albumsGrid) albumsGrid.hidden = false;
    input.focus();
  });

  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (clearBtn) clearBtn.hidden = (q.length === 0);

    if (albumSearchDebounceTimer) clearTimeout(albumSearchDebounceTimer);

    if (q.length === 0) {
      if (indicator) indicator.hidden = true;
      if (resultsContainer) resultsContainer.hidden = true;
      if (albumsGrid) albumsGrid.hidden = false;
      return;
    }

    albumSearchDebounceTimer = setTimeout(async () => {
      if (indicator) indicator.hidden = false;
      try {
        const res = await fetch(`/api/admin/events/${activeSlug}/search?q=${encodeURIComponent(q)}`);
        if (!res.ok) throw new Error('Search request failed');
        const data = await res.json();
        renderAlbumSearchResults(data, q);
      } catch (err) {
        console.error('Album search error:', err);
      } finally {
        if (indicator) indicator.hidden = true;
      }
    }, 250);
  });
}

function renderAlbumSearchResults(data, query) {
  const resultsContainer = $('albumSearchResults');
  const albumsGrid = $('albumsGrid');
  if (!resultsContainer) return;

  const albums = data.albums || [];
  const photos = data.photos || [];
  const total = albums.length + photos.length;

  if (albumsGrid) albumsGrid.hidden = true;
  resultsContainer.hidden = false;
  resultsContainer.replaceChildren();

  if (total === 0) {
    resultsContainer.innerHTML = `
      <div style="text-align:center;padding:2.5rem 1rem;color:var(--muted,#6b7280);">
        <svg style="width:36px;height:36px;margin:0 auto 0.75rem;color:#9ca3af;display:block;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <div style="font-weight:600;font-size:0.95rem;color:#111827;margin-bottom:0.25rem;">No results found for "${escapeHtml(query)}"</div>
        <div style="font-size:0.84rem;">Try searching for a different folder name, date (e.g. "29-10"), or photo filename.</div>
      </div>
    `;
    return;
  }

  const header = document.createElement('div');
  header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;padding-bottom:0.65rem;border-bottom:1px solid #f3f4f6;';
  header.innerHTML = `
    <span style="font-weight:700;font-size:0.92rem;color:#111827;">Found ${total} match${total === 1 ? '' : 'es'} for "${escapeHtml(query)}"</span>
    <span style="font-size:0.8rem;color:#6b7280;">${albums.length} folder${albums.length === 1 ? '' : 's'}, ${photos.length} photo${photos.length === 1 ? '' : 's'}</span>
  `;
  resultsContainer.appendChild(header);

  // If albums matched
  if (albums.length > 0) {
    const albumsTitle = document.createElement('div');
    albumsTitle.className = 'search-section-title';
    albumsTitle.innerHTML = `<svg class="icon" viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg> Matching Albums & Folders (${albums.length})`;
    resultsContainer.appendChild(albumsTitle);

    const albumsGridEl = document.createElement('div');
    albumsGridEl.className = 'search-albums-grid';
    albums.forEach(a => {
      const card = document.createElement('div');
      card.className = 'search-album-card';
      card.title = `Open album: ${a.name}`;
      const thumb = a.cover_thumb || '/favicon.ico';
      card.innerHTML = `
        <img class="search-album-thumb" src="${thumb}" alt="${escapeHtml(a.name)}" onerror="this.src='/favicon.ico'">
        <div class="search-album-meta">
          <div class="search-album-name">${escapeHtml(a.name)}</div>
          <div class="search-album-count">${a.photo_count || 0} photos</div>
        </div>
      `;
      card.addEventListener('click', () => {
        openAlbumPhotos(a.id, a.name);
      });
      albumsGridEl.appendChild(card);
    });
    resultsContainer.appendChild(albumsGridEl);
  }

  // If photos matched
  if (photos.length > 0) {
    const photosTitle = document.createElement('div');
    photosTitle.className = 'search-section-title';
    photosTitle.innerHTML = `<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg> Matching Photos (${photos.length})`;
    resultsContainer.appendChild(photosTitle);

    const photosGridEl = document.createElement('div');
    photosGridEl.className = 'search-photos-grid';
    photos.forEach(p => {
      const card = document.createElement('div');
      card.className = 'search-photo-card';
      card.title = `${p.filename} (${p.album_name || 'Album'}) - Click for HD Preview`;
      const thumb = p.thumb_url || p.high_res_url || '';
      card.innerHTML = `
        <img class="search-photo-img" src="${thumb}" alt="${escapeHtml(p.filename)}" loading="lazy">
        <div class="search-photo-badge">${escapeHtml(p.filename)}</div>
      `;
      card.addEventListener('click', () => {
        openHdLightbox(p.high_res_url || p.thumb_url, p.filename, p.album_name, p.high_res_url || p.thumb_url);
      });
      photosGridEl.appendChild(card);
    });
    resultsContainer.appendChild(photosGridEl);
  }
}

// Albums (Grouped by Month, Latest Date First, Collapsible Accordions)
async function loadAlbums() {
  try {
    let res = await fetch('/api/admin/albums');
    if (!res.ok) res = await fetch(`/api/admin/events/${activeSlug}`);
    if (!res.ok) return;
    const d = await res.json();
    allAlbums = d.albums || [];
    currentEvent = d.event || { name: 'BRK Photos', slug: activeSlug };
    $('sidebarAlbumCount').textContent = allAlbums.length;
    renderSidebarSubfolders();

    const searchInput = $('albumSearchInput');
    if (searchInput && searchInput.value.trim().length > 0) {
      return; // Do not overwrite active search results
    }

    const container = $('albumsGrid');
    container.replaceChildren();

    if (allAlbums.length === 0) {
      container.innerHTML = '<div style="color:var(--muted);padding:1.5rem;text-align:center;">No albums found. Click "+ New Album" to create one.</div>';
      return;
    }

    // 1. Parse dates and group albums by month
    const monthGroups = {};
    for (const a of allAlbums) {
      a._date = parseAlbumDate(a.name, a.slug);
      const mKey = a._date.monthKey;
      if (!monthGroups[mKey]) {
        monthGroups[mKey] = {
          key: mKey,
          label: a._date.monthLabel,
          sortVal: a._date.hasDate ? (a._date.year * 100 + a._date.month) : -1,
          albums: []
        };
      }
      monthGroups[mKey].albums.push(a);
    }

    // 2. Sort month groups descending (latest month first)
    const sortedGroups = Object.values(monthGroups).sort((g1, g2) => g2.sortVal - g1.sortVal);

    // 3. Within each month, sort albums descending: latest date first, old date past
    for (const grp of sortedGroups) {
      grp.albums.sort((a, b) => {
        if (b._date.sortVal !== a._date.sortVal) {
          return b._date.sortVal - a._date.sortVal; // Latest date first
        }
        return b.name.localeCompare(a.name, undefined, { numeric: true });
      });
    }

    // 4. Render collapsible accordion for each month
    for (const grp of sortedGroups) {
      const accordion = document.createElement('div');
      accordion.className = 'month-accordion open';
      accordion.id = `accordion-${grp.key}`;

      const totalPhotosInMonth = grp.albums.reduce((acc, al) => acc + (al.photo_count || 0), 0);

      const header = document.createElement('div');
      header.className = 'month-accordion-header';
      header.innerHTML = `
        <div class="month-accordion-title">
          <svg class="icon-sm" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span>${grp.label}</span>
        </div>
        <div class="month-accordion-meta">
          <span class="month-badge">${grp.albums.length} albums • ${totalPhotosInMonth.toLocaleString()} photos</span>
          <svg class="month-accordion-chevron" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg>
        </div>
      `;
      header.addEventListener('click', () => {
        accordion.classList.toggle('open');
      });

      const body = document.createElement('div');
      body.className = 'month-accordion-body';

      const grid = document.createElement('div');
      grid.className = 'albums-grid';

      for (const a of grp.albums) {
        const card = document.createElement('div');
        card.className = 'person-card';
        card.style.width = '100%';
        const coverUrl = a.cover_image_path ? `/thumb/${a.cover_image_path}` : 'https://images.unsplash.com/photo-1519741497674-611481863552?auto=format&fit=crop&w=400&q=80';
        card.innerHTML = `
          <img src="${coverUrl}" style="width:68px;height:68px;border-radius:10px;object-fit:cover;background:#e5e7eb;">
          <div style="flex:1;">
            <h4 style="font-size:1rem;font-weight:700;">${a.name}</h4>
            <div style="font-size:0.78rem;color:var(--muted);font-family:monospace;">slug: ${a.slug}</div>
            <div style="font-size:0.82rem;color:var(--primary);font-weight:600;margin-top:0.2rem;">${a.photo_count || 0} photos</div>
            <div style="display:flex;gap:0.4rem;margin-top:0.5rem;flex-wrap:wrap;">
              <button class="btn-primary" style="padding:0.25rem 0.6rem;font-size:0.75rem;" onclick="openAlbumPhotos(${a.id}, '${a.name.replace(/'/g, "\\'")}')">Browse</button>
              <button class="btn-outline" style="padding:0.25rem 0.6rem;font-size:0.75rem;" onclick="openEditAlbumModal(${a.id}, '${a.name.replace(/'/g, "\\'")}', '${a.slug}')">Edit</button>
              <button class="btn-outline" style="padding:0.25rem 0.6rem;font-size:0.75rem;color:var(--err);" onclick="promptDeleteAlbum(${a.id}, '${a.name.replace(/'/g, "\\'")}', '${a.slug}')">Delete</button>
            </div>
          </div>
        `;
        grid.append(card);
      }

      body.append(grid);
      accordion.append(header, body);
      container.append(accordion);
    }
  } catch (e) {
    console.error('Error loading albums:', e);
  }
}

// Edit Album Modal Handlers
function openEditAlbumModal(albumId, albumName, albumSlug) {
  $('editAlbumId').value = albumId;
  $('editAlbumName').value = albumName;
  $('editAlbumSlug').value = albumSlug;
  openModal('editAlbumModal');
}

$('editAlbumName')?.addEventListener('input', () => {
  $('editAlbumSlug').value = generateSlug($('editAlbumName').value);
});

$('editAlbumForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const id = $('editAlbumId').value;
  const name = $('editAlbumName').value.trim();
  const slug = $('editAlbumSlug').value.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-').replace(/^-+|-+$/g, '');
  if (!name || !slug) { alert('Name and slug are required'); return; }

  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/albums/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, slug })
    });
    if (res.ok) {
      closeModal('editAlbumModal');
      await setEventWorkspace(activeSlug, false);
      loadAlbums();
    } else {
      const j = await res.json();
      alert(j.detail || 'Failed to update album');
    }
  } catch (err) {
    alert('Error updating album: ' + err);
  }
});

function promptDeleteAlbum(albumId, albumName, albumSlug) {
  const confirmText = albumSlug || albumName.toLowerCase().replace(/[^a-z0-9_-]/g, '-');
  openDeleteConfirmModal(
    `Move Folder "${albumName}" to Trash?`,
    `Are you sure you want to move folder <strong>"${albumName}"</strong> to the Trash Bin? All photos in this folder will be moved to the Trash Bin.`,
    confirmText,
    async () => {
      let res = await fetch(`/api/admin/events/${activeSlug}/albums/${albumId}`, { method: 'DELETE' });
      if (res.status === 405 || !res.ok) {
        res = await fetch(`/api/admin/events/${activeSlug}/albums/${albumId}/delete`, { method: 'POST' });
      }
      if (res.ok) {
        await setEventWorkspace(activeSlug, false);
        loadAlbums();
      } else {
        const j = await res.json();
        alert(j.detail || 'Failed to move folder to trash');
      }
    },
    'Move to Trash'
  );
}

// Photos (Natural Alphanumeric Sort by Name, Featured First, HD Lightbox Preview)
async function loadPhotos(filterAlbumId = '', preserveSelection = false) {
  if (!preserveSelection) {
    selectedPhotoIds.clear();
  }
  updateBulkToolbar();

  const fSel = $('filterAlbumSelect');
  const bSel = $('bulkTargetAlbumSelect');
  const floatSel = $('floatingTargetAlbumSelect');
  fSel.replaceChildren();
  bSel.replaceChildren();
  if (floatSel) floatSel.replaceChildren();

  const defOpt = document.createElement('option');
  defOpt.value = '';
  defOpt.textContent = 'All Folders / Albums';
  fSel.append(defOpt);

  for (const a of allAlbums) {
    const o = document.createElement('option');
    o.value = a.id;
    o.textContent = a.name;
    if (String(a.id) === String(filterAlbumId)) o.selected = true;
    fSel.append(o);

    const bo = document.createElement('option');
    bo.value = a.id;
    bo.textContent = `Move to: ${a.name}`;
    bSel.append(bo);

    if (floatSel) {
      const fo = document.createElement('option');
      fo.value = a.id;
      fo.textContent = `Move to: ${a.name}`;
      floatSel.append(fo);
    }
  }

  const url = filterAlbumId 
    ? `/api/admin/events/${activeSlug}/photos?album_id=${filterAlbumId}` 
    : `/api/admin/events/${activeSlug}/photos`;

  try {
    const res = await fetch(url);
    if (!res.ok) return;
    const j = await res.json();
    const grid = $('photoLibraryGrid');
    grid.replaceChildren();

    // Sort: Featured photos placed in the beginning, followed by descending date order (newest to oldest)
    const photos = (j.photos || []).slice().sort((a, b) => {
      const featA = a.is_featured ? 1 : 0;
      const featB = b.is_featured ? 1 : 0;
      if (featA !== featB) return featB - featA; // Featured photos first
      const dtA = (a.captured_at || a.created_at || '');
      const dtB = (b.captured_at || b.created_at || '');
      if (dtA && dtB && dtA !== dtB) return dtB.localeCompare(dtA);
      if (dtB && !dtA) return 1;
      if (dtA && !dtB) return -1;
      return (b.id || 0) - (a.id || 0);
    });

    const evName = currentEvent ? currentEvent.name : activeSlug;
    $('photosPanelTitle').textContent = `Photo Library — ${evName} (${photos.length} photos)`;

    if (photos.length === 0) {
      grid.innerHTML = '<div style="color:var(--muted);grid-column:1/-1;">No photos in this album.</div>';
      return;
    }

    for (const p of photos) {
      const item = document.createElement('div');
      item.className = 'photo-item';
      item.id = `photo-item-${p.id}`;
      const enc = p.rel_path.split('/').map(encodeURIComponent).join('/');
      const isFeatured = !!p.is_featured;
      const isChecked = selectedPhotoIds.has(p.id);
      if (isChecked) item.classList.add('selected');

      const safeFilename = (p.filename || '').replace(/'/g, "\\'");
      const safeAlbumName = (p.album_name || '').replace(/'/g, "\\'");

      item.innerHTML = `
        <input type="checkbox" class="photo-check" data-id="${p.id}" ${isChecked ? 'checked' : ''}>
        ${isFeatured ? '<span class="badge badge-featured" style="position:absolute;top:8px;right:8px;font-size:0.7rem;font-weight:700;z-index:2;box-shadow:0 1px 3px rgba(0,0,0,0.3);">★ Featured</span>' : ''}
        <img src="/thumb/${enc}" loading="lazy" style="cursor:zoom-in;" title="Click to view HD Resolution" onclick="openHdLightbox('/preview/${enc}', '${safeFilename}', '${safeAlbumName}', '/download/hd/${enc}')">
        <div class="photo-meta">
          <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;" title="Click to view HD Resolution" onclick="openHdLightbox('/preview/${enc}', '${safeFilename}', '${safeAlbumName}', '/download/hd/${enc}')">${p.filename}</div>
          <div style="color:var(--muted);font-size:0.75rem;">${p.album_name || 'Unassigned'}</div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:0.4rem;gap:0.3rem;">
            <a href="/download/hd/${enc}" download="${p.filename}" style="color:var(--primary);text-decoration:none;font-weight:600;font-size:0.75rem;display:inline-flex;align-items:center;gap:2px;">Download HD ↗</a>
            <div style="display:flex;gap:0.25rem;">
              <button class="btn-outline" style="padding:0.15rem 0.4rem;font-size:0.7rem;${isFeatured ? 'background:#fef3c7;border-color:#f59e0b;color:#b45309;font-weight:700;' : ''}" onclick="toggleFeaturedPhoto(${p.id})">${isFeatured ? '★ Featured' : '☆ Feature'}</button>
              <button class="btn-outline" style="padding:0.15rem 0.4rem;font-size:0.7rem;" onclick="setAsCover(${p.album_id}, '${p.rel_path}')">Cover</button>
            </div>
          </div>
        </div>
      `;

      item.querySelector('.photo-check').addEventListener('change', e => {
        e.stopPropagation();
        if (e.target.checked) selectedPhotoIds.add(p.id);
        else selectedPhotoIds.delete(p.id);
        item.classList.toggle('selected', e.target.checked);
        updateBulkToolbar();
      });

      grid.append(item);
    }
  } catch (e) {}
}

// HD Lightbox Viewer
function openHdLightbox(previewUrl, filename, albumName, downloadUrl) {
  if ($('hdLightboxImg')) $('hdLightboxImg').src = previewUrl;
  if ($('hdLightboxTitle')) $('hdLightboxTitle').textContent = filename;
  if ($('hdLightboxSub')) $('hdLightboxSub').textContent = `Folder: ${albumName || 'BRK Photos'}`;
  if ($('hdLightboxDownloadBtn')) {
    $('hdLightboxDownloadBtn').href = downloadUrl;
    $('hdLightboxDownloadBtn').setAttribute('download', filename);
  }
  openModal('hdLightboxModal');
}

$('hdLightboxModal')?.addEventListener('click', e => {
  if (e.target.id === 'hdLightboxModal') {
    closeModal('hdLightboxModal');
  }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeModal('hdLightboxModal');
  }
});

async function toggleFeaturedPhoto(photoId) {
  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/photos/${photoId}/toggle-feature`, { method: 'POST' });
    if (res.ok) {
      loadPhotos($('filterAlbumSelect')?.value || '', selectedPhotoIds.size > 0);
    } else {
      const j = await res.json();
      alert(j.detail || 'Could not toggle featured status');
    }
  } catch (e) {
    console.error('Error toggling feature:', e);
  }
}

$('filterAlbumSelect').addEventListener('change', e => {
  activeFilterAlbumId = e.target.value;
  loadPhotos(activeFilterAlbumId, false);
  
  // Sync sidebar active subfolder
  document.querySelectorAll('.subfolder-item').forEach(el => el.classList.remove('active'));
  if (activeFilterAlbumId) {
    const activeLi = $(`subfolder-item-${activeFilterAlbumId}`);
    if (activeLi) activeLi.classList.add('active');
  }
});

function updateBulkToolbar() {
  const count = selectedPhotoIds.size;
  if ($('selectedCountBadge')) {
    $('selectedCountBadge').textContent = `${count} selected`;
    $('selectedCountBadge').hidden = count === 0;
  }
  if ($('clearSelectBtn')) $('clearSelectBtn').hidden = count === 0;
  if ($('bulkActionGroup')) $('bulkActionGroup').hidden = count === 0;

  // Floating bottom pill bar (stays visible regardless of scroll position)
  const pill = $('floatingBulkPill');
  if (pill) {
    pill.hidden = count === 0;
    if ($('floatingCountBadge')) $('floatingCountBadge').textContent = `${count} selected`;
  }
}

$('selectAllBtn')?.addEventListener('click', () => {
  document.querySelectorAll('.photo-check').forEach(cb => {
    cb.checked = true;
    selectedPhotoIds.add(Number(cb.getAttribute('data-id')));
    cb.closest('.photo-item')?.classList.add('selected');
  });
  updateBulkToolbar();
});

$('clearSelectBtn')?.addEventListener('click', () => {
  document.querySelectorAll('.photo-check').forEach(cb => {
    cb.checked = false;
    cb.closest('.photo-item')?.classList.remove('selected');
  });
  selectedPhotoIds.clear();
  updateBulkToolbar();
});

// Floating Bar Action Listeners
$('floatingClearSelectBtn')?.addEventListener('click', () => {
  document.querySelectorAll('.photo-check').forEach(cb => {
    cb.checked = false;
    cb.closest('.photo-item')?.classList.remove('selected');
  });
  selectedPhotoIds.clear();
  updateBulkToolbar();
});

$('floatingBulkMoveBtn')?.addEventListener('click', async () => {
  const targetAlbumId = $('floatingTargetAlbumSelect')?.value || $('bulkTargetAlbumSelect')?.value;
  if (!targetAlbumId) return;
  await fetch(`/api/admin/events/${activeSlug}/photos/bulk-move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      photo_ids: Array.from(selectedPhotoIds),
      target_album_id: Number(targetAlbumId)
    })
  });
  selectedPhotoIds.clear();
  updateBulkToolbar();
  loadPhotos($('filterAlbumSelect').value);
  await setEventWorkspace(activeSlug, false);
});

$('floatingBulkFeatureBtn')?.addEventListener('click', () => runBulkAction('feature'));
$('floatingBulkUnfeatureBtn')?.addEventListener('click', () => runBulkAction('unfeature'));
$('floatingBulkHideBtn')?.addEventListener('click', () => runBulkAction('hide'));
$('floatingBulkDeleteBtn')?.addEventListener('click', () => {
  if (selectedPhotoIds.size === 0) return;
  openDeleteConfirmModal(
    `Move ${selectedPhotoIds.size} Selected Photos to Trash?`,
    `Are you sure you want to move <strong>${selectedPhotoIds.size} selected photos</strong> to the Trash Bin? You can restore them or permanently delete them from the Trash Bin.`,
    'DELETE',
    async () => {
      await runBulkAction('delete');
    },
    'Move to Trash'
  );
});

$('bulkMoveBtn')?.addEventListener('click', async () => {
  const targetAlbumId = $('bulkTargetAlbumSelect').value;
  await fetch(`/api/admin/events/${activeSlug}/photos/bulk-move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      photo_ids: Array.from(selectedPhotoIds),
      target_album_id: Number(targetAlbumId)
    })
  });
  selectedPhotoIds.clear();
  updateBulkToolbar();
  loadPhotos($('filterAlbumSelect').value);
  await setEventWorkspace(activeSlug, false);
});

$('bulkFeatureBtn')?.addEventListener('click', () => runBulkAction('feature'));
$('bulkUnfeatureBtn')?.addEventListener('click', () => runBulkAction('unfeature'));
$('bulkHideBtn')?.addEventListener('click', () => runBulkAction('hide'));
$('bulkDeleteBtn')?.addEventListener('click', () => {
  if (selectedPhotoIds.size === 0) return;
  openDeleteConfirmModal(
    `Move ${selectedPhotoIds.size} Selected Photos to Trash?`,
    `Are you sure you want to move <strong>${selectedPhotoIds.size} selected photos</strong> to the Trash Bin? You can restore them or permanently delete them from the Trash Bin.`,
    'DELETE',
    async () => {
      await runBulkAction('delete');
    },
    'Move to Trash'
  );
});

async function runBulkAction(action) {
  await fetch(`/api/admin/events/${activeSlug}/photos/bulk-action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      photo_ids: Array.from(selectedPhotoIds),
      action: action
    })
  });
  selectedPhotoIds.clear();
  updateBulkToolbar();
  loadPhotos($('filterAlbumSelect').value);
  await setEventWorkspace(activeSlug, false);
}

async function setAsCover(albumId, relPath) {
  if (!albumId) { alert('Assign photo to an album first.'); return; }
  await fetch(`/api/admin/events/${activeSlug}/albums/${albumId}/set-cover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rel_path: relPath })
  });
  alert('Album cover updated successfully.');
}

// People Directory
async function loadPeople() {
  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/people`);
    if (!res.ok) return;
    const j = await res.json();
    const grid = $('peopleGrid');
    grid.replaceChildren();

    if (!j.people || j.people.length === 0) {
      grid.innerHTML = '<div style="color:var(--muted);grid-column:1/-1;">No people identified yet. Click "Re-Cluster Faces" to generate face profiles from index.</div>';
      return;
    }

    for (const p of j.people) {
      const card = document.createElement('div');
      card.className = 'person-card';
      const thumbUrl = p.avatar_thumb_path ? `/thumb/${p.avatar_thumb_path}` : 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=200&q=80';
      card.innerHTML = `
        <img src="${thumbUrl}" class="person-thumb">
        <div style="flex:1;">
          <h4 style="font-size:0.95rem;font-weight:700;">${p.name}</h4>
          <div style="font-size:0.8rem;color:var(--muted);">${p.photo_count} photos</div>
          <div style="margin-top:0.4rem;display:flex;gap:0.4rem;">
            <button class="btn-outline" style="padding:0.2rem 0.5rem;font-size:0.75rem;" onclick="openRenamePersonModal(${p.id}, '${p.name}')">Rename</button>
            <button class="btn-primary" style="padding:0.2rem 0.5rem;font-size:0.75rem;" onclick="viewPersonPhotos(${p.id})">Photos</button>
          </div>
        </div>
      `;
      grid.append(card);
    }
  } catch (e) {}
}

$('reclusterPeopleBtn')?.addEventListener('click', async (e) => {
  e?.preventDefault();
  const btn = $('reclusterPeopleBtn');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const originalHtml = btn.innerHTML;
  btn.innerHTML = `<svg class="icon-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><span>Starting Re-Clustering...</span>`;

  // Immediately redirect to Activity Logs page without waiting
  switchTab('logs');

  try {
    const thresholdInput = $('thresholdRange');
    const thresholdVal = thresholdInput ? parseFloat(thresholdInput.value) : 0.45;
    const res = await fetch(`/api/admin/events/${activeSlug}/people/recluster`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ threshold: thresholdVal })
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      console.warn('Reclustering notice:', errData.detail || 'Could not start');
    }
  } catch (err) {
    console.error('Failed to trigger reclustering:', err);
  } finally {
    pollTaskStatus();
    loadTaskHistory(true);
    setTimeout(() => {
      btn.innerHTML = originalHtml;
      btn.disabled = false;
    }, 2000);
  }
});

function openRenamePersonModal(id, currentName) {
  $('renamePersonId').value = id;
  $('renamePersonNameInput').value = currentName;
  openModal('renamePersonModal');
}

$('renamePersonForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const id = $('renamePersonId').value;
  const name = $('renamePersonNameInput').value.trim();
  await fetch(`/api/admin/events/${activeSlug}/people/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  closeModal('renamePersonModal');
  loadPeople();
});

async function viewPersonPhotos(personId) {
  const res = await fetch(`/api/admin/events/${activeSlug}/people/${personId}/photos`);
  if (!res.ok) return;
  const j = await res.json();
  switchTab('photos');
  const grid = $('photoLibraryGrid');
  grid.replaceChildren();
  const photos = (j.photos || []).slice().sort((a, b) => {
    const featA = a.is_featured ? 1 : 0;
    const featB = b.is_featured ? 1 : 0;
    if (featA !== featB) return featB - featA;
    const dtA = (a.captured_at || a.created_at || '');
    const dtB = (b.captured_at || b.created_at || '');
    if (dtA && dtB && dtA !== dtB) return dtB.localeCompare(dtA);
    if (dtB && !dtA) return 1;
    if (dtA && !dtB) return -1;
    return (b.id || 0) - (a.id || 0);
  });
  for (const p of photos) {
    const item = document.createElement('div');
    item.className = 'photo-item';
    const enc = p.rel_path.split('/').map(encodeURIComponent).join('/');
    item.innerHTML = `
      <img src="/thumb/${enc}" loading="lazy">
      <div class="photo-meta">
        <div style="font-weight:600;">${p.filename}</div>
        <div style="color:var(--muted);">${p.album_name || 'Event Photo'}</div>
        <a href="/photo/${enc}" target="_blank" style="color:var(--primary);text-decoration:none;font-weight:600;margin-top:0.2rem;display:inline-block;">Download ↗</a>
      </div>
    `;
    grid.append(item);
  }
}

// Upload Manager Setup
function setupUploadTab() {
  // Populate Google Drive and Automation target subfolder dropdowns
  const gdSel = $('gdriveTargetAlbumSelect');
  const autoSel = $('autoTargetAlbumSelect');

  const populateSelector = (sel) => {
    if (!sel) return;
    sel.replaceChildren();
    const autoOpt = document.createElement('option');
    autoOpt.value = 'auto';
    autoOpt.textContent = '⚡ Auto-detect albums from Google Drive folders (Recommended)';
    autoOpt.selected = true;
    sel.append(autoOpt);

    for (const a of allAlbums) {
      const o = document.createElement('option');
      o.value = a.slug;
      o.textContent = `Specific Album: ${a.name}`;
      sel.append(o);
    }
  };

  populateSelector(gdSel);
  populateSelector(autoSel);

  // Load Automations & Queue
  loadAutomations();
  loadUploadQueue();
}

// Google Drive Ingest & Queue Submissions
let isSubmittingGdrive = false;

$('startGdriveSyncBtn')?.addEventListener('click', async () => {
  await submitGdriveTask('now');
});

$('btnGdriveAddToQueue')?.addEventListener('click', async () => {
  await submitGdriveTask('queue');
});

async function submitGdriveTask(action) {
  if (isSubmittingGdrive) return;
  const url = $('gdriveUrlInput').value.trim();
  if (!url) {
    alert('Please enter a Google Drive folder URL or ID.');
    return;
  }
  isSubmittingGdrive = true;

  const customSlug = $('gdriveCustomSlugInput').value.trim();
  const selectSlug = $('gdriveTargetAlbumSelect').value;
  const albumSlug = customSlug || selectSlug || 'auto';

  const allowed = [];
  if ($('gdriveFmtJpg')?.checked) allowed.push('jpg', 'jpeg');
  if ($('gdriveFmtPng')?.checked) allowed.push('png');
  if ($('gdriveFmtWebp')?.checked) allowed.push('webp');
  if ($('gdriveFmtHeic')?.checked) allowed.push('heic', 'heif');

  const btnSync = $('startGdriveSyncBtn');
  const btnQueue = $('btnGdriveAddToQueue');
  if (btnSync) btnSync.disabled = true;
  if (btnQueue) btnQueue.disabled = true;

  const statusEl = $('gdriveStatusText');
  if (statusEl) statusEl.textContent = action === 'now' ? 'Starting ingest...' : 'Adding to queue...';

  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/gdrive-queue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, album_slug: albumSlug, allowed_extensions: allowed, action })
    });
    const j = await res.json();
    if (res.ok) {
      if (statusEl) statusEl.textContent = j.message || 'Task submitted successfully!';
      $('gdriveUrlInput').value = '';
      await loadUploadQueue();
      pollTaskStatus();
    } else {
      if (statusEl) statusEl.textContent = j.detail || 'Failed to submit Google Drive task.';
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Server connection error.';
  } finally {
    isSubmittingGdrive = false;
    if (btnSync) btnSync.disabled = false;
    if (btnQueue) btnQueue.disabled = false;
  }
}

// --- Scheduled Automations Management ---
let currentAutomationsList = [];
let isFetchingAutomations = false;

async function loadAutomations() {
  if (isFetchingAutomations) return;
  isFetchingAutomations = true;
  const tbody = $('automationsTableBody');
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 12000);
    const res = await fetch(`/api/admin/automations?event_slug=${activeSlug}`, { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!res.ok) {
      throw new Error(`Server returned HTTP ${res.status}`);
    }
    const data = await res.json();
    currentAutomationsList = data.automations || [];
    renderAutomationsTable(currentAutomationsList);
  } catch (err) {
    console.error('Failed to load automations:', err);
    if (tbody && (!currentAutomationsList || currentAutomationsList.length === 0)) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" style="text-align:center;padding:2rem 1rem;color:var(--err,#dc2626);">
            <div style="font-weight:600;font-size:0.92rem;margin-bottom:0.35rem;">Could not load automations</div>
            <div style="font-size:0.8rem;color:var(--muted,#6b7280);margin-bottom:0.75rem;">${escapeHtml(err.message || 'Request timed out or connection interrupted')}</div>
            <button class="btn-outline" onclick="loadAutomations()" style="font-size:0.8rem;padding:0.35rem 0.85rem;cursor:pointer;">⟳ Retry</button>
          </td>
        </tr>
      `;
    }
  } finally {
    isFetchingAutomations = false;
  }
}

function renderAutomationsTable(automations) {
  const tbody = $('automationsTableBody');
  if (!tbody) return;

  if (!automations || automations.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align:center;padding:2.5rem 1rem;color:var(--muted);">
          <div style="font-weight:600;font-size:0.95rem;color:#111827;margin-bottom:0.25rem;">No scheduled automations yet</div>
          <div style="font-size:0.84rem;">Click "+ Create Automation" to schedule automatic Google Drive ingests into the queue.</div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.replaceChildren();
  for (const a of automations) {
    const tr = document.createElement('tr');
    tr.style.cssText = 'border-bottom:1px solid #f1f5f9;transition:background 0.15s;';
    tr.onmouseover = () => tr.style.background = '#f8fafc';
    tr.onmouseout = () => tr.style.background = 'transparent';

    const isActive = a.is_active == 1;
    const statusBadge = isActive 
      ? `<span class="q-badge badge-running" style="background:#ecfdf5;color:#047857;border-color:#a7f3d0;">ACTIVE</span>`
      : `<span class="q-badge badge-cancelled" style="background:#f1f5f9;color:#64748b;border-color:#cbd5e1;">PAUSED</span>`;

    const repeatLabel = a.repeat_interval === 'daily' ? 'Daily' : (a.repeat_interval === 'weekly' ? 'Weekly' : 'One-time');
    const nextTrigger = a.next_trigger_at ? a.next_trigger_at : (isActive ? `${a.trigger_date} ${a.trigger_time}` : '—');
    const lastResult = a.last_triggered_at 
      ? `<div>${formatTimeShort(a.last_triggered_at)}</div><div style="font-size:0.75rem;color:var(--muted);">${a.last_status || 'queued'}</div>` 
      : '<span style="color:var(--muted);">Never</span>';

    // Source link formatting
    let driveDisplay = a.gdrive_url;
    if (driveDisplay.length > 32) driveDisplay = driveDisplay.substring(0, 16) + '...' + driveDisplay.substring(driveDisplay.length - 12);

    tr.innerHTML = `
      <td style="padding:0.75rem 1rem;">
        <div style="font-weight:700;color:#111827;">${escapeHtml(a.name)}</div>
        <div style="font-size:0.75rem;color:var(--muted);font-family:monospace;">#AUTO-${a.id}</div>
      </td>
      <td style="padding:0.75rem 0.75rem;">
        <div style="font-size:0.8rem;color:#0f5c4d;font-weight:600;max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${escapeHtml(a.gdrive_url)}">${escapeHtml(driveDisplay)}</div>
        <div style="font-size:0.75rem;color:var(--muted);">Folder: <strong>${escapeHtml(a.target_album_slug || 'auto')}</strong></div>
      </td>
      <td style="padding:0.75rem 0.75rem;font-weight:600;color:#334155;white-space:nowrap;">
        ${nextTrigger}
      </td>
      <td style="padding:0.75rem 0.75rem;color:#475569;">
        <span style="display:inline-block;padding:2px 8px;background:#f1f5f9;border-radius:4px;font-size:0.78rem;font-weight:600;">${repeatLabel}</span>
      </td>
      <td style="padding:0.75rem 0.75rem;font-size:0.8rem;">
        ${lastResult}
      </td>
      <td style="padding:0.75rem 0.75rem;">
        ${statusBadge}
      </td>
      <td style="padding:0.75rem 1rem;text-align:right;white-space:nowrap;">
        <button class="btn-primary" style="padding:0.25rem 0.55rem;font-size:0.75rem;background:#0f5c4d;margin-right:3px;" onclick="runAutomationNow(${a.id})" title="Trigger right now and queue">Run Now</button>
        <button class="btn-outline" style="padding:0.25rem 0.55rem;font-size:0.75rem;margin-right:3px;" onclick="toggleAutomationStatus(${a.id})">${isActive ? 'Pause' : 'Resume'}</button>
        <button class="btn-outline" style="padding:0.25rem 0.55rem;font-size:0.75rem;margin-right:3px;" onclick="openEditAutomationModal(${a.id})">Edit</button>
        <button class="btn-outline" style="padding:0.25rem 0.55rem;font-size:0.75rem;margin-right:3px;" onclick="viewAutomationHistoryModal(${a.id})">History</button>
        <button class="btn-outline" style="padding:0.25rem 0.55rem;font-size:0.75rem;color:#ef4444;border-color:#fca5a5;" onclick="deleteAutomationAction(${a.id})">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  }
}

function openCreateAutomationModal() {
  $('autoModalHeading').textContent = 'Create Automation';
  $('autoEditId').value = '';
  $('autoNameInput').value = '';
  $('autoGdriveUrlInput').value = '';
  $('autoCustomSlugInput').value = '';
  $('autoRepeatSelect').value = 'none';
  $('autoIsActiveInput').checked = true;

  // Set default trigger date to today (or tomorrow) and next hour
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  $('autoTriggerDateInput').value = `${yyyy}-${mm}-${dd}`;
  
  now.setHours(now.getHours() + 1);
  const hh = String(now.getHours()).padStart(2, '0');
  $('autoTriggerTimeInput').value = `${hh}:00`;

  openModal('createAutomationModal');
}

function openEditAutomationModal(autoId) {
  const auto = currentAutomationsList.find(a => a.id === autoId);
  if (!auto) return;

  $('autoModalHeading').textContent = 'Edit Automation';
  $('autoEditId').value = auto.id;
  $('autoNameInput').value = auto.name || '';
  $('autoGdriveUrlInput').value = auto.gdrive_url || '';
  if ($('autoTargetAlbumSelect')) $('autoTargetAlbumSelect').value = auto.target_album_slug || 'auto';
  $('autoCustomSlugInput').value = auto.target_album_slug === 'auto' ? '' : (auto.target_album_slug || '');
  $('autoTriggerDateInput').value = auto.trigger_date || '';
  $('autoTriggerTimeInput').value = auto.trigger_time || '';
  $('autoRepeatSelect').value = auto.repeat_interval || 'none';
  $('autoIsActiveInput').checked = auto.is_active == 1;

  try {
    const fmts = JSON.parse(auto.formats_json || '["jpg","png","webp"]');
    $('autoFmtJpg').checked = fmts.includes('jpg') || fmts.includes('jpeg');
    $('autoFmtPng').checked = fmts.includes('png');
    $('autoFmtWebp').checked = fmts.includes('webp');
    $('autoFmtHeic').checked = fmts.includes('heic') || fmts.includes('heif');
  } catch(e) {}

  openModal('createAutomationModal');
}

async function submitAutomationForm() {
  const name = $('autoNameInput').value.trim();
  const gdriveUrl = $('autoGdriveUrlInput').value.trim();
  const triggerDate = $('autoTriggerDateInput').value.trim();
  const triggerTime = $('autoTriggerTimeInput').value.trim();
  const repeat = $('autoRepeatSelect').value;
  const isActive = $('autoIsActiveInput').checked;
  const editId = $('autoEditId').value;

  if (!name) {
    alert('Please enter an automation name.');
    return;
  }
  if (!gdriveUrl) {
    alert('Please enter a Google Drive folder URL or ID.');
    return;
  }
  if (!triggerDate || !triggerTime) {
    alert('Please select both trigger date and trigger time.');
    return;
  }

  const customSlug = $('autoCustomSlugInput').value.trim();
  const targetAlbum = customSlug || $('autoTargetAlbumSelect')?.value || 'auto';

  const formats = [];
  if ($('autoFmtJpg')?.checked) formats.push('jpg', 'jpeg');
  if ($('autoFmtPng')?.checked) formats.push('png');
  if ($('autoFmtWebp')?.checked) formats.push('webp');
  if ($('autoFmtHeic')?.checked) formats.push('heic', 'heif');

  const payload = {
    name,
    gdrive_url: gdriveUrl,
    target_album_slug: targetAlbum,
    formats,
    trigger_date: triggerDate,
    trigger_time: triggerTime,
    repeat_interval: repeat,
    is_active: isActive,
    event_slug: activeSlug
  };

  const btn = $('btnSaveAutomation');
  if (btn) btn.disabled = true;

  try {
    const url = editId ? `/api/admin/automations/${editId}` : '/api/admin/automations';
    const method = editId ? 'PUT' : 'POST';
    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      closeModal('createAutomationModal');
      await loadAutomations();
    } else {
      const j = await res.json();
      alert(j.detail || 'Failed to save automation.');
    }
  } catch (err) {
    alert('Network error: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function runAutomationNow(autoId) {
  if (!confirm('Run this automation now? It will be immediately enqueued into the upload queue.')) return;
  try {
    const res = await fetch(`/api/admin/automations/${autoId}/run-now`, { method: 'POST' });
    if (res.ok) {
      await loadAutomations();
      await loadUploadQueue();
      pollTaskStatus();
    } else {
      const j = await res.json();
      alert(j.detail || 'Could not trigger automation.');
    }
  } catch(e) {
    alert('Error triggering automation: ' + e.message);
  }
}

async function toggleAutomationStatus(autoId) {
  try {
    const res = await fetch(`/api/admin/automations/${autoId}/toggle`, { method: 'POST' });
    if (res.ok) {
      await loadAutomations();
    }
  } catch (e) {
    alert('Error toggling automation: ' + e.message);
  }
}

async function deleteAutomationAction(autoId) {
  if (!confirm('Are you sure you want to delete this automation schedule?')) return;
  try {
    const res = await fetch(`/api/admin/automations/${autoId}`, { method: 'DELETE' });
    if (res.ok) {
      await loadAutomations();
    }
  } catch(e) {
    alert('Error deleting automation: ' + e.message);
  }
}

async function viewAutomationHistoryModal(autoId) {
  const auto = currentAutomationsList.find(a => a.id === autoId);
  if ($('autoHistoryModalTitle')) {
    $('autoHistoryModalTitle').textContent = auto ? `History: ${auto.name}` : 'Automation History';
  }
  const tbody = $('autoHistoryTableBody');
  if (tbody) tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:1.5rem;color:var(--muted);">Loading history...</td></tr>';
  openModal('automationHistoryModal');

  try {
    const res = await fetch(`/api/admin/automations/${autoId}/history`);
    if (!res.ok) throw new Error('Failed to fetch history');
    const data = await res.json();
    const list = data.history || [];

    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:1.5rem;color:var(--muted);">No execution history recorded yet.</td></tr>';
      return;
    }

    tbody.replaceChildren();
    for (const h of list) {
      const tr = document.createElement('tr');
      tr.style.cssText = 'border-bottom:1px solid #f1f5f9;font-size:0.8rem;';
      const triggerTime = h.triggered_at ? formatTimeShort(h.triggered_at) : '—';
      const statusBadge = `<span class="q-badge badge-${h.status || 'queued'}">${(h.status || 'queued').toUpperCase()}</span>`;

      tr.innerHTML = `
        <td style="padding:0.55rem 0.75rem;white-space:nowrap;color:#475569;">${triggerTime}</td>
        <td style="padding:0.55rem 0.75rem;font-family:monospace;font-weight:600;color:#0f5c4d;">#${h.task_id}</td>
        <td style="padding:0.55rem 0.75rem;">${statusBadge}</td>
        <td style="padding:0.55rem 0.75rem;color:#334155;">
          <div>${escapeHtml(h.result_message || 'Task submitted to queue')}</div>
          ${h.error_details ? `<div style="color:#ef4444;font-size:0.75rem;margin-top:2px;">${escapeHtml(h.error_details)}</div>` : ''}
        </td>
      `;
      tbody.appendChild(tr);
    }
  } catch(e) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:1.5rem;color:#ef4444;">${e.message}</td></tr>`;
  }
}

// --- Standardized Indian Standard Time (IST) Date/Time Parsing & Formatting ---
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

async function loadUploadQueue() {
  if (isFetchingQueue) return;
  isFetchingQueue = true;
  const tbody = $('uploadQueueTableBody');
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 12000);
    const res = await fetch('/api/admin/queue?limit=30', { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!res.ok) {
      throw new Error(`Server returned HTTP ${res.status}`);
    }
    const data = await res.json();
    currentQueueTasksList = data.tasks || [];
    renderUploadQueueUI(data);
  } catch (e) {
    console.error('Error loading upload queue:', e);
    if (tbody && currentQueueTasksList.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" style="text-align:center;padding:2rem 1rem;color:var(--err,#dc2626);">
            <div style="font-weight:600;font-size:0.92rem;margin-bottom:0.35rem;">Could not load upload queue</div>
            <div style="font-size:0.8rem;color:var(--muted,#6b7280);margin-bottom:0.75rem;">${escapeHtml(e.message || 'Request timed out or connection interrupted')}</div>
            <button class="btn-outline" onclick="loadUploadQueue()" style="font-size:0.8rem;padding:0.35rem 0.85rem;cursor:pointer;">⟳ Retry</button>
          </td>
        </tr>
      `;
    }
  } finally {
    isFetchingQueue = false;
  }
}

function renderUploadQueueUI(data) {
  const stats = data.stats || { total: 0, running: 0, queued: 0, completed: 0, failed: 0 };
  const tasks = data.tasks || [];
  hasActiveQueueTasks = ((stats.running || 0) > 0 || (stats.queued || 0) > 0);

  if ($('qStatTotal')) $('qStatTotal').textContent = stats.total;
  if ($('qStatRunning')) $('qStatRunning').textContent = stats.running;
  if ($('qStatQueued')) $('qStatQueued').textContent = stats.queued;
  if ($('qStatCompleted')) $('qStatCompleted').textContent = stats.completed;
  if ($('qStatFailed')) $('qStatFailed').textContent = stats.failed;

  const tbody = $('uploadQueueTableBody');
  if (!tbody) return;

  if (tasks.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align:center;padding:2.5rem 1rem;color:var(--muted,#6b7280);">
          <div style="font-weight:600;font-size:0.95rem;color:#111827;margin-bottom:0.25rem;">No upload tasks in queue</div>
          <div style="font-size:0.84rem;">Drop files above or add a Google Drive folder to submit sequential upload jobs.</div>
        </td>
      </tr>
    `;
    return;
  }

  // Remove empty placeholder row if real tasks arrived
  const emptyRow = tbody.querySelector('td[colspan="7"]');
  if (emptyRow) tbody.innerHTML = '';

  const taskIds = new Set(tasks.map(t => t.task_id));

  for (let i = 0; i < tasks.length; i++) {
    const t = tasks[i];
    let badgeClass = 'badge-queued';
    let statusLabel = 'Queued';
    if (t.status === 'running') {
      badgeClass = 'badge-running';
      statusLabel = 'Running';
    } else if (t.status === 'completed') {
      badgeClass = 'badge-completed';
      statusLabel = 'Completed';
    } else if (t.status === 'failed') {
      badgeClass = 'badge-failed';
      statusLabel = 'Failed';
    } else if (t.status === 'cancelled') {
      badgeClass = 'badge-cancelled';
      statusLabel = 'Cancelled';
    }

    const pct = t.progress_pct || 0;
    const stepText = t.current_step || 'Waiting in queue...';
    const filesCount = t.file_count || t.processed_count || 0;
    const createdAt = t.created_at ? formatToIST(t.created_at) : '—';
    const timing = t.completed_at ? `Done ${formatToIST(t.completed_at, false)}` : (t.started_at ? `Started ${formatToIST(t.started_at, false)}` : 'Waiting');

    let tr = document.getElementById(`queue-row-${t.task_id}`);
    if (tr) {
      // In-place AJAX update without replacing elements (prevents UI flicker)
      const badgeEl = tr.querySelector('.q-badge');
      if (badgeEl && (badgeEl.textContent !== statusLabel || !badgeEl.classList.contains(badgeClass))) {
        badgeEl.className = `q-badge ${badgeClass}`;
        badgeEl.textContent = statusLabel;
      }
      const progressBar = tr.querySelector('.q-progress-bar');
      if (progressBar) {
        progressBar.style.width = `${pct}%`;
        progressBar.style.background = t.status === 'failed' ? '#ef4444' : '#0f5c4d';
      }
      const progressPctText = tr.querySelector('.q-progress-pct');
      if (progressPctText && progressPctText.textContent !== `${pct}%`) {
        progressPctText.textContent = `${pct}%`;
      }
      const stepEl = tr.querySelector('.q-step-text');
      if (stepEl && stepEl.textContent !== stepText) {
        stepEl.textContent = stepText;
        stepEl.title = stepText;
      }
      const timingEl = tr.querySelector('.q-timing-text');
      if (timingEl && timingEl.textContent !== timing) {
        timingEl.textContent = timing;
      }
      const filesEl = tr.querySelector('.q-files-text');
      if (filesEl) {
        filesEl.textContent = `${filesCount} photo${filesCount === 1 ? '' : 's'}`;
      }
    } else {
      tr = document.createElement('tr');
      tr.id = `queue-row-${t.task_id}`;
      tr.style.cssText = 'border-bottom:1px solid #f1f5f9;transition:background 0.15s;';
      tr.onmouseover = () => tr.style.background = '#f8fafc';
      tr.onmouseout = () => tr.style.background = 'transparent';

      tr.innerHTML = `
        <td style="padding:0.65rem 0.75rem;">
          <div style="font-weight:700;color:#111827;">${escapeHtml(t.task_name)}</div>
          <div style="font-size:0.75rem;color:var(--muted);font-family:monospace;">#${t.task_id} • Folder: ${escapeHtml(t.target_album_slug || 'auto')}</div>
        </td>
        <td class="q-files-text" style="padding:0.65rem 0.75rem;font-weight:600;color:#374151;">
          ${filesCount} photo${filesCount === 1 ? '' : 's'}
        </td>
        <td style="padding:0.65rem 0.75rem;">
          <span class="q-badge ${badgeClass}">${statusLabel}</span>
        </td>
        <td style="padding:0.65rem 0.75rem;">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
            <div style="flex:1;background:#e2e8f0;height:6px;border-radius:99px;overflow:hidden;">
              <div class="q-progress-bar" style="width:${pct}%;background:${t.status === 'failed' ? '#ef4444' : '#0f5c4d'};height:100%;transition:width 0.3s;"></div>
            </div>
            <span class="q-progress-pct" style="font-size:0.75rem;font-weight:700;color:#475569;min-width:32px;">${pct}%</span>
          </div>
          <div class="q-step-text" style="font-size:0.75rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px;" title="${escapeHtml(stepText)}">${escapeHtml(stepText)}</div>
        </td>
        <td style="padding:0.65rem 0.75rem;color:#64748b;font-size:0.8rem;">
          ${createdAt}
        </td>
        <td class="q-timing-text" style="padding:0.65rem 0.75rem;color:#64748b;font-size:0.8rem;">
          ${timing}
        </td>
        <td style="padding:0.65rem 0.75rem;text-align:right;white-space:nowrap;">
          <button class="btn-outline" style="padding:0.25rem 0.55rem;font-size:0.75rem;" onclick="viewQueueTaskDetails('${t.task_id}')">Details</button>
          ${(t.status === 'queued' || t.status === 'running') ? `<button class="btn-outline" style="padding:0.25rem 0.55rem;font-size:0.75rem;color:#ef4444;border-color:#fca5a5;margin-left:4px;" onclick="cancelQueueTaskAction('${t.task_id}')">Cancel</button>` : ''}
          ${(t.status === 'failed' || t.status === 'cancelled') ? `<button class="btn-primary" style="padding:0.25rem 0.55rem;font-size:0.75rem;margin-left:4px;background:#0f5c4d;" onclick="retryQueueTaskAction('${t.task_id}')">Retry</button>` : ''}
        </td>
      `;
      tbody.appendChild(tr);
    }
  }

  // Remove rows no longer in queue tasks
  const allQueueRows = Array.from(tbody.querySelectorAll('tr[id^="queue-row-"]'));
  for (const r of allQueueRows) {
    const tid = r.id.replace('queue-row-', '');
    if (!taskIds.has(tid)) {
      r.remove();
    }
  }
}

async function viewQueueTaskDetails(taskId) {
  try {
    const res = await fetch(`/api/admin/queue/${taskId}`);
    if (!res.ok) throw new Error('Task not found');
    const data = await res.json();
    const t = data.task;

    if ($('qModalTitle')) $('qModalTitle').textContent = t.task_name || `Task #${t.task_id}`;
    const badge = $('qModalStatusBadge');
    if (badge) {
      badge.className = `q-badge badge-${t.status}`;
      badge.textContent = t.status.toUpperCase();
    }
    const meta = $('qModalMeta');
    if (meta) {
      meta.innerHTML = `
        <div><strong>Task ID:</strong> <span style="font-family:monospace;">${t.task_id}</span></div>
        <div><strong>Task Type:</strong> ${t.task_type}</div>
        <div><strong>Target Album:</strong> ${escapeHtml(t.target_album_name || t.target_album_slug)}</div>
        <div><strong>Files Count:</strong> ${t.file_count || 0}</div>
        <div><strong>Created At:</strong> ${formatToIST(t.created_at)}</div>
        <div><strong>Completed At:</strong> ${t.completed_at ? formatToIST(t.completed_at) : 'In progress / waiting'}</div>
        ${t.error_message ? `<div style="grid-column:1/-1;color:#b91c1c;"><strong>Error:</strong> ${escapeHtml(t.error_message)}</div>` : ''}
      `;
    }
    const logBox = $('qModalLogText');
    if (logBox) {
      logBox.textContent = t.log_text || 'No logs recorded for this task yet.';
      logBox.scrollTop = logBox.scrollHeight;
    }
    openModal('queueTaskDetailsModal');
  } catch (err) {
    alert('Could not load task details: ' + err.message);
  }
}

async function cancelQueueTaskAction(taskId) {
  if (!confirm(`Cancel upload task #${taskId}?`)) return;
  try {
    const res = await fetch(`/api/admin/queue/${taskId}/cancel`, { method: 'POST' });
    if (res.ok) {
      await loadUploadQueue();
      pollTaskStatus();
    } else {
      const j = await res.json();
      alert(j.detail || 'Could not cancel task.');
    }
  } catch (err) {
    alert('Error cancelling task: ' + err.message);
  }
}

async function retryQueueTaskAction(taskId) {
  try {
    const res = await fetch(`/api/admin/queue/${taskId}/retry`, { method: 'POST' });
    if (res.ok) {
      await loadUploadQueue();
      pollTaskStatus();
    } else {
      const j = await res.json();
      alert(j.detail || 'Could not retry task.');
    }
  } catch (err) {
    alert('Error retrying task: ' + err.message);
  }
}

$('btnClearCompletedQueue')?.addEventListener('click', async () => {
  try {
    const res = await fetch('/api/admin/queue/clear-completed', { method: 'POST' });
    if (res.ok) {
      await loadUploadQueue();
    }
  } catch (err) {
    console.error('Error clearing completed queue:', err);
  }
});

$('btnRefreshQueue')?.addEventListener('click', async () => {
  await loadUploadQueue();
});

// Storage
async function loadStorage() {
  try {
    const res = await fetch('/api/admin/storage');
    if (!res.ok) return;
    const j = await res.json();
    $('storageVpsUsed').textContent = `${(j.vps_used_bytes / (1024 * 1024)).toFixed(1)} MB`;
  } catch (e) {}
}

// --- Dedicated Analytics & Telemetry Controller ---
let analyticsCurrentRange = '7d';
let analyticsCustomFrom = '';
let analyticsCustomTo = '';
let analyticsCurrentSubTab = 'overview';
let analyticsData = null;
let analyticsSystemData = null;
let isAnalyticsInitialized = false;

function initAnalyticsEventListeners() {
  if (isAnalyticsInitialized) return;
  isAnalyticsInitialized = true;

  // Range Pills
  $('analyticsRangePills')?.querySelectorAll('.analytics-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      $('analyticsRangePills').querySelectorAll('.analytics-pill').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const r = btn.dataset.range;
      analyticsCurrentRange = r;

      const customWrap = $('analyticsCustomDateWrap');
      if (r === 'custom') {
        if (customWrap) customWrap.hidden = false;
        // Pre-fill inputs if empty
        const todayStr = new Date().toISOString().split('T')[0];
        const weekAgoStr = new Date(Date.now() - 7 * 86400000).toISOString().split('T')[0];
        if (!$('analyticsFromDate').value) $('analyticsFromDate').value = weekAgoStr;
        if (!$('analyticsToDate').value) $('analyticsToDate').value = todayStr;
      } else {
        if (customWrap) customWrap.hidden = true;
        analyticsCustomFrom = '';
        analyticsCustomTo = '';
        loadAnalytics(false);
      }
    });
  });

  // Apply Custom Date Button
  $('analyticsApplyCustomBtn')?.addEventListener('click', () => {
    analyticsCustomFrom = $('analyticsFromDate').value;
    analyticsCustomTo = $('analyticsToDate').value;
    if (!analyticsCustomFrom) {
      alert('Please select a valid start date');
      return;
    }
    loadAnalytics(false);
  });

  // Sub-Tab Navigation
  document.querySelectorAll('.analytics-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.analytics-tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const subtab = btn.dataset.subtab;
      analyticsCurrentSubTab = subtab;

      const subpanels = ['overview', 'visitors', 'photos', 'system'];
      subpanels.forEach(sp => {
        const el = $(`subtab-${sp}`);
        if (el) el.hidden = (sp !== subtab);
      });

      if (analyticsData) {
        renderCurrentSubTabCharts();
      }
    });
  });

  // Refresh Button
  $('analyticsRefreshBtn')?.addEventListener('click', () => {
    const btn = $('analyticsRefreshBtn');
    btn.classList.add('loading');
    loadAnalytics(true).finally(() => {
      setTimeout(() => btn.classList.remove('loading'), 300);
    });
  });

  // Export CSV Dropdown Toggle
  const exportBtn = $('analyticsExportMenuBtn');
  const exportMenu = $('analyticsExportDropdown');
  if (exportBtn && exportMenu) {
    exportBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      exportMenu.style.display = exportMenu.style.display === 'block' ? 'none' : 'block';
    });
    document.addEventListener('click', () => {
      if (exportMenu) exportMenu.style.display = 'none';
    });
  }
}

function triggerCsvExport(type = 'daily') {
  let url = `/api/admin/analytics/export?type=${type}&range=${analyticsCurrentRange}`;
  if (analyticsCurrentRange === 'custom') {
    if (analyticsCustomFrom) url += `&from_date=${analyticsCustomFrom}`;
    if (analyticsCustomTo) url += `&to_date=${analyticsCustomTo}`;
  }
  const a = document.createElement('a');
  a.href = url;
  a.download = `brk_analytics_${type}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function loadAnalytics(forceRefresh = false) {
  initAnalyticsEventListeners();

  let dashUrl = `/api/admin/analytics/dashboard?range=${analyticsCurrentRange}`;
  if (analyticsCurrentRange === 'custom' && analyticsCustomFrom) {
    dashUrl += `&from_date=${analyticsCustomFrom}&to_date=${analyticsCustomTo}`;
  }
  if (forceRefresh) dashUrl += '&refresh=true';

  let sysUrl = `/api/admin/analytics/system`;
  if (forceRefresh) sysUrl += '?refresh=true';

  try {
    const [dashRes, sysRes] = await Promise.all([
      fetch(dashUrl),
      fetch(sysUrl)
    ]);

    if (dashRes.ok) {
      analyticsData = await dashRes.json();
      populateDashboardMetrics(analyticsData);
    }
    if (sysRes.ok) {
      analyticsSystemData = await sysRes.json();
      populateSystemMetrics(analyticsSystemData);
    }

    renderCurrentSubTabCharts();
  } catch (err) {
    console.error('Error loading analytics dashboard:', err);
  }
}

function populateDashboardMetrics(data) {
  if (!data) return;

  // Header Date Label
  const lbl = $('analyticsDateRangeLabel');
  if (lbl) {
    lbl.textContent = `Showing metrics for: ${data.window_label || 'Current Period'} (${data.start_time.split(' ')[0]} to ${data.end_time.split(' ')[0]})`;
  }

  // 1. Overview Tab KPI Cards
  const ov = data.overview || {};
  $('kpiTotalPhotos').textContent = (ov.total_photographs || 0).toLocaleString();
  $('kpiIndexedPhotos').textContent = (ov.indexed_photographs || 0).toLocaleString();
  $('kpiPendingPhotos').textContent = (ov.pending_photographs || 0).toLocaleString();
  $('kpiTotalSearches').textContent = (ov.total_searches || 0).toLocaleString();
  $('kpiSuccessfulSearches').textContent = (ov.successful_searches || 0).toLocaleString();
  $('kpiNoMatchSearches').textContent = (ov.no_match_searches || 0).toLocaleString();
  $('kpiUniqueVisitors').textContent = (ov.unique_visitors || 0).toLocaleString();
  $('kpiPhotosViewed').textContent = (ov.photos_viewed || 0).toLocaleString();
  $('kpiZipDownloads').textContent = (ov.zip_downloads || 0).toLocaleString();
  $('kpiSingleDownloads').textContent = (ov.original_downloads || 0).toLocaleString();
  $('kpiSuccessRateText').textContent = `${(ov.search_success_rate || 0.0).toFixed(1)}%`;

  // 2. Visitors Tab
  const vis = data.visitor_analytics || {};
  $('visTotalVisits').textContent = (vis.total_visits || 0).toLocaleString();
  $('visFirstTime').textContent = (vis.first_time_visitors || 0).toLocaleString();
  $('visReturning').textContent = (vis.returning_visitors || 0).toLocaleString();
  $('visAbandonment').textContent = `${(vis.abandonment_rate || 0.0).toFixed(1)}%`;
  $('visAvgMatches').textContent = (vis.avg_photos_per_match || 0.0).toFixed(1);

  // Device percentage
  const mob = vis.mobile_percent || 65.0;
  const dsk = vis.desktop_percent || 35.0;
  $('visMobilePercent').textContent = `${mob.toFixed(1)}%`;
  $('visDesktopPercent').textContent = `${dsk.toFixed(1)}%`;
  if ($('visMobileBar')) $('visMobileBar').style.width = `${mob}%`;
  if ($('visDesktopBar')) $('visDesktopBar').style.width = `${dsk}%`;

  // Search details
  const srch = data.search_analytics || {};
  $('srchAvgLatency').textContent = `${(srch.avg_duration_ms || 0.0).toFixed(1)} ms`;
  $('srchFaceDetectFails').textContent = (srch.face_detection_failures || 0).toLocaleString();
  $('srchSlowCount').textContent = (srch.slow_searches || 0).toLocaleString();
  $('srchTechnicalFails').textContent = (srch.search_failures || 0).toLocaleString();

  // 3. Photos Tab (Top 10 Most-Viewed)
  const pe = data.photo_engagement || {};
  const topPhotos = pe.top_photos || [];
  const tbody = $('topPhotosTableBody');
  if (tbody) {
    if (topPhotos.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="padding:1.5rem;text-align:center;color:var(--muted);">No photo engagement recorded for this timeframe yet.</td></tr>`;
    } else {
      tbody.innerHTML = topPhotos.map((p, idx) => `
        <tr style="border-bottom:1px solid var(--line);">
          <td style="padding:0.75rem 1rem;font-weight:700;color:var(--muted);">#${idx + 1}</td>
          <td style="padding:0.75rem 1rem;">
            <div class="analytics-top-photo-row">
              <img src="${p.thumb_url}" class="analytics-top-photo-thumb" alt="${p.filename}" onerror="this.src='/static/images/favicon.png'">
              <div>
                <div style="font-weight:600;color:var(--ink);font-size:0.85rem;">${p.filename}</div>
                <div style="font-size:0.75rem;color:var(--muted);">${p.captured_at || 'Captured date unrecorded'}</div>
              </div>
            </div>
          </td>
          <td style="padding:0.75rem 1rem;color:var(--muted);">${p.album_name || 'BRK Sir Event'}</td>
          <td style="padding:0.75rem 1rem;text-align:center;font-weight:700;color:var(--accent);">${p.views.toLocaleString()}</td>
          <td style="padding:0.75rem 1rem;text-align:center;font-weight:700;color:var(--emerald);">${p.downloads.toLocaleString()}</td>
          <td style="padding:0.75rem 1rem;text-align:right;">
            <a href="${p.preview_url}" target="_blank" class="btn-icon-sub" title="Inspect Photo" style="display:inline-flex;">
              <svg class="icon-sm" viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </a>
          </td>
        </tr>
      `).join('');
    }
  }

  // Download History
  const dl = data.download_analytics || {};
  $('dlCompletionRateBadge').textContent = `${(dl.zip_completion_rate || 100.0).toFixed(1)}% completed`;
  const dlHistory = dl.download_history || [];
  const dlTbody = $('downloadHistoryTableBody');
  if (dlTbody) {
    if (dlHistory.length === 0) {
      dlTbody.innerHTML = `<tr><td colspan="7" style="padding:1.5rem;text-align:center;color:var(--muted);">No recent download jobs recorded.</td></tr>`;
    } else {
      dlTbody.innerHTML = dlHistory.map(j => {
        let statusBadge = '<span class="badge badge-published">Completed</span>';
        if (j.status === 'failed') statusBadge = '<span class="badge" style="background:#fee2e2;color:#dc2626;">Failed</span>';
        else if (j.status === 'processing') statusBadge = '<span class="badge" style="background:#fef3c7;color:#d97706;">Processing</span>';

        return `
          <tr style="border-bottom:1px solid var(--line);">
            <td style="padding:0.75rem 1rem;font-family:monospace;font-size:0.8rem;">${j.started_at}</td>
            <td style="padding:0.75rem 1rem;font-family:monospace;font-weight:600;color:var(--muted);">${j.job_id}</td>
            <td style="padding:0.75rem 1rem;text-transform:uppercase;font-size:0.75rem;font-weight:700;">${j.job_type}</td>
            <td style="padding:0.75rem 1rem;font-weight:600;">${j.photo_count} photos</td>
            <td style="padding:0.75rem 1rem;">${j.size_mb} MB</td>
            <td style="padding:0.75rem 1rem;">${j.duration_s}s</td>
            <td style="padding:0.75rem 1rem;text-align:right;">${statusBadge}</td>
          </tr>
        `;
      }).join('');
    }
  }

  // 4. Photo Library Breakdown
  const lib = data.library_analytics || {};
  $('libDetectedFaces').textContent = (lib.detected_faces || 0).toLocaleString();
  $('libPeopleClusters').textContent = (lib.people_clusters || 0).toLocaleString();
}

function populateSystemMetrics(sys) {
  if (!sys) return;

  // CPU
  const cpu = sys.cpu_percent || 0.0;
  $('sysCpuPercent').textContent = `${cpu.toFixed(1)}%`;
  if ($('sysCpuBar')) $('sysCpuBar').style.width = `${Math.min(100, cpu)}%`;

  // RAM
  const ram = sys.ram || {};
  const ramPct = ram.percent || 0.0;
  $('sysRamPercent').textContent = `${ramPct.toFixed(1)}%`;
  $('sysRamUsed').textContent = Math.round(ram.used_mb || 0);
  $('sysRamTotal').textContent = Math.round(ram.total_mb || 0);
  if ($('sysRamBar')) $('sysRamBar').style.width = `${Math.min(100, ramPct)}%`;

  // Disk
  const disk = sys.disk || {};
  const diskPct = disk.percent || 0.0;
  $('sysDiskPercent').textContent = `${diskPct.toFixed(1)}%`;
  $('sysDiskAvail').textContent = (disk.available_gb || 0).toFixed(1);
  $('sysDiskTotal').textContent = (disk.total_gb || 0).toFixed(1);
  if ($('sysDiskBar')) $('sysDiskBar').style.width = `${Math.min(100, diskPct)}%`;

  // Storage
  const st = sys.storage || {};
  $('sysDbSize').textContent = `${(st.database_mb || 0.0).toFixed(2)} MB`;
  $('libThumbsSize').textContent = `${(st.thumbnails_mb || 0.0).toFixed(1)} MB`;

  // Queue
  const q = sys.queues || {};
  $('libQueueBacklog').textContent = `${q.upload_queue_len || 0} queued / ${q.active_tasks || 0} active`;

  // Sampled at badge
  if ($('sysSampledAtBadge')) $('sysSampledAtBadge').textContent = `Sampled: ${sys.sampled_at || 'Just now'}`;

  // Warnings
  const warnBanner = $('analyticsWarningBanner');
  const warnText = $('analyticsWarningText');
  if (sys.warnings && sys.warnings.length > 0) {
    if (warnBanner && warnText) {
      warnBanner.style.display = 'flex';
      warnText.textContent = sys.warnings.map(w => w.message).join(' | ');
    }
  } else if (warnBanner) {
    warnBanner.style.display = 'none';
  }
}

function renderCurrentSubTabCharts() {
  if (!analyticsData) return;
  const series = analyticsData.chart_series || [];

  if (analyticsCurrentSubTab === 'overview') {
    renderDailyTrafficChart(series);
    renderSearchOutcomeChart(series);
  } else if (analyticsCurrentSubTab === 'visitors') {
    const hourly = analyticsData.visitor_analytics?.hourly_distribution || {};
    renderHourlyActivityChart(hourly);
  }
}

// --- Interactive Native SVG Chart Generators (Zero Dependencies) ---

function renderDailyTrafficChart(series) {
  const wrap = $('chartDailyVisitorsSvgWrap');
  if (!wrap) return;
  if (!series || series.length === 0) {
    wrap.innerHTML = '<span style="color:var(--muted);font-size:0.8rem;">No activity data recorded in this period</span>';
    return;
  }

  const w = wrap.clientWidth || 500;
  const h = 210;
  const padL = 40;
  const padR = 20;
  const padT = 20;
  const padB = 30;

  const maxVal = Math.max(10, ...series.map(s => Math.max(s.visits || 0, s.total_searches || 0)));
  const usableW = w - padL - padR;
  const usableH = h - padT - padB;
  const stepX = series.length > 1 ? usableW / (series.length - 1) : usableW;

  const pointsVisits = series.map((s, i) => {
    const x = padL + i * stepX;
    const y = padT + usableH - ((s.visits || 0) / maxVal) * usableH;
    return { x, y, val: s.visits || 0, label: s.label };
  });

  const pointsSearches = series.map((s, i) => {
    const x = padL + i * stepX;
    const y = padT + usableH - ((s.total_searches || 0) / maxVal) * usableH;
    return { x, y, val: s.total_searches || 0, label: s.label };
  });

  const lineVisitsD = pointsVisits.reduce((acc, p, i) => i === 0 ? `M ${p.x} ${p.y}` : `${acc} L ${p.x} ${p.y}`, '');
  const areaVisitsD = pointsVisits.length > 0
    ? `${lineVisitsD} L ${pointsVisits[pointsVisits.length - 1].x} ${padT + usableH} L ${pointsVisits[0].x} ${padT + usableH} Z`
    : '';

  const lineSearchesD = pointsSearches.reduce((acc, p, i) => i === 0 ? `M ${p.x} ${p.y}` : `${acc} L ${p.x} ${p.y}`, '');

  // Grid Lines
  const gridLines = [0, 0.33, 0.66, 1.0].map(ratio => {
    const y = padT + usableH - (ratio * usableH);
    const val = Math.round(ratio * maxVal);
    return `
      <line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="#f1f5f9" stroke-width="1"/>
      <text x="${padL - 8}" y="${y + 4}" font-size="10" fill="#94a3b8" text-anchor="end">${val}</text>
    `;
  }).join('');

  // X-Axis Labels (show subset if many days)
  const skipMod = Math.max(1, Math.floor(series.length / 7));
  const xLabels = pointsVisits.map((p, i) => {
    if (i % skipMod !== 0 && i !== pointsVisits.length - 1) return '';
    return `<text x="${p.x}" y="${h - 8}" font-size="10" fill="#94a3b8" text-anchor="middle">${p.label}</text>`;
  }).join('');

  // Data Dots
  const dotsVisits = pointsVisits.map(p => `
    <circle cx="${p.x}" cy="${p.y}" r="3.5" fill="#0f5c4d" stroke="#ffffff" stroke-width="1.5">
      <title>Visitors on ${p.label}: ${p.val}</title>
    </circle>
  `).join('');

  const dotsSearches = pointsSearches.map(p => `
    <circle cx="${p.x}" cy="${p.y}" r="3.5" fill="#0284c7" stroke="#ffffff" stroke-width="1.5">
      <title>Searches on ${p.label}: ${p.val}</title>
    </circle>
  `).join('');

  wrap.innerHTML = `
    <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="overflow:visible;">
      ${gridLines}
      <path d="${areaVisitsD}" fill="rgba(15, 92, 77, 0.08)"/>
      <path d="${lineVisitsD}" fill="none" stroke="#0f5c4d" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="${lineSearchesD}" fill="none" stroke="#0284c7" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      ${dotsVisits}
      ${dotsSearches}
      ${xLabels}
    </svg>
  `;
}

function renderSearchOutcomeChart(series) {
  const wrap = $('chartSearchOutcomeSvgWrap');
  if (!wrap) return;
  if (!series || series.length === 0) {
    wrap.innerHTML = '<span style="color:var(--muted);font-size:0.8rem;">No search outcomes recorded in this period</span>';
    return;
  }

  const w = wrap.clientWidth || 500;
  const h = 210;
  const padL = 40;
  const padR = 20;
  const padT = 20;
  const padB = 30;

  const maxVal = Math.max(10, ...series.map(s => (s.successful_searches || 0) + (s.zero_results || 0)));
  const usableW = w - padL - padR;
  const usableH = h - padT - padB;
  const barSlotW = usableW / series.length;
  const barWidth = Math.max(4, Math.min(24, barSlotW * 0.6));

  const gridLines = [0, 0.5, 1.0].map(ratio => {
    const y = padT + usableH - (ratio * usableH);
    const val = Math.round(ratio * maxVal);
    return `
      <line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="#f1f5f9" stroke-width="1"/>
      <text x="${padL - 8}" y="${y + 4}" font-size="10" fill="#94a3b8" text-anchor="end">${val}</text>
    `;
  }).join('');

  const skipMod = Math.max(1, Math.floor(series.length / 7));
  const bars = series.map((s, i) => {
    const cx = padL + i * barSlotW + barSlotW / 2;
    const x = cx - barWidth / 2;

    const succH = ((s.successful_searches || 0) / maxVal) * usableH;
    const zeroH = ((s.zero_results || 0) / maxVal) * usableH;

    const succY = padT + usableH - succH;
    const zeroY = succY - zeroH;

    const xLabel = (i % skipMod === 0 || i === series.length - 1)
      ? `<text x="${cx}" y="${h - 8}" font-size="10" fill="#94a3b8" text-anchor="middle">${s.label}</text>`
      : '';

    return `
      <g>
        <title>${s.label}: ${s.successful_searches} successful matches, ${s.zero_results} no-match</title>
        <!-- Successful Matches Bar (Green) -->
        <rect x="${x}" y="${succY}" width="${barWidth}" height="${succH}" fill="#10b981" rx="2"/>
        <!-- Zero Matches Bar (Orange Stacked) -->
        <rect x="${x}" y="${zeroY}" width="${barWidth}" height="${zeroH}" fill="#f97316" rx="2"/>
        ${xLabel}
      </g>
    `;
  }).join('');

  wrap.innerHTML = `
    <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="overflow:visible;">
      ${gridLines}
      ${bars}
    </svg>
  `;
}

function renderHourlyActivityChart(hourlyMap) {
  const wrap = $('chartHourlyActivitySvgWrap');
  if (!wrap) return;

  const hours = Object.keys(hourlyMap).sort();
  const values = hours.map(h => hourlyMap[h] || 0);
  const maxVal = Math.max(5, ...values);

  const w = wrap.clientWidth || 600;
  const h = 180;
  const padL = 30;
  const padR = 20;
  const padT = 15;
  const padB = 25;

  const usableW = w - padL - padR;
  const usableH = h - padT - padB;
  const slotW = usableW / 24;
  const barW = Math.max(3, slotW * 0.7);

  const bars = hours.map((hourKey, i) => {
    const val = hourlyMap[hourKey] || 0;
    const barH = (val / maxVal) * usableH;
    const cx = padL + i * slotW + slotW / 2;
    const x = cx - barW / 2;
    const y = padT + usableH - barH;

    // Show label every 3 hours: 00, 03, 06, 09, 12, 15, 18, 21
    const showLabel = (i % 3 === 0);
    const label = showLabel ? `<text x="${cx}" y="${h - 6}" font-size="9" fill="#94a3b8" text-anchor="middle">${hourKey.split(':')[0]}h</text>` : '';

    return `
      <g>
        <title>${hourKey}: ${val} portal events</title>
        <rect x="${x}" y="${y}" width="${barW}" height="${barH}" fill="#0f5c4d" rx="2"/>
        ${label}
      </g>
    `;
  }).join('');

  wrap.innerHTML = `
    <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="overflow:visible;">
      <line x1="${padL}" y1="${padT + usableH}" x2="${w - padR}" y2="${padT + usableH}" stroke="#e2e8f0" stroke-width="1"/>
      ${bars}
    </svg>
  `;
}

// Activity Logs & Task Management
let pollTimer = null;
let taskDismissTimer = null;
let isPollingTaskStatus = false;

function dismissTaskCard() {
  $('visualTaskCard').hidden = true;
  const taskSubtext = $('taskEventTargetText')?.textContent || '';
  sessionStorage.setItem('dismissed_task_recent', 'true');
}

// Pause/slowdown polling when browser tab is hidden to save server and client resources
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    clearTimeout(pollTimer);
    pollTaskStatus();
  }
});

async function pollTaskStatus() {
  if (isPollingTaskStatus) return;
  // If tab is currently backgrounded/hidden, reduce polling drastically
  if (document.hidden) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(pollTaskStatus, 25000);
    return;
  }

  isPollingTaskStatus = true;
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8000);
    const res = await fetch('/api/admin/tasks/current', { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!res.ok) return;
    const data = await res.json();
    const task = data.latest_task || data.memory_task;
    const isRunning = data.is_running;

    if (!task || (!isRunning && task.status !== 'running' && task.status !== 'completed' && task.status !== 'failed' && task.status !== 'cancelled')) {
      $('visualTaskCard').hidden = true;
      return;
    }

    const taskId = task.task_id || task.id;
    currentRunningTaskId = taskId;
    if (sessionStorage.getItem('dismissed_task_' + taskId)) {
      $('visualTaskCard').hidden = true;
      return;
    }

    // Hide completed/failed/cancelled task if older than 15 seconds
    if ((task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') && task.completed_at) {
      const completedTime = new Date(task.completed_at.includes('Z') ? task.completed_at : task.completed_at + 'Z').getTime();
      const diffSec = (Date.now() - completedTime) / 1000;
      if (diffSec > 15) { // 15 seconds
        $('visualTaskCard').hidden = true;
        return;
      }
    }

    $('visualTaskCard').hidden = false;
    const pill = $('taskStatusPill');
    const pulse = $('taskPulseDot');
    const statusTxt = $('taskStatusText');

    const status = task.status || (isRunning ? 'running' : 'completed');
    const isCancelled = status === 'cancelled';
    pill.className = `task-status-pill ${status === 'running' ? 'running' : (status === 'completed' ? 'completed' : 'failed')}`;
    pulse.style.display = status === 'running' ? 'inline-block' : 'none';
    statusTxt.textContent = isCancelled ? 'STOPPED' : status.toUpperCase();

    const stopBtn = $('taskStopBtn');
    if (stopBtn) {
      stopBtn.style.display = (isRunning || status === 'running') ? 'inline-flex' : 'none';
    }

    // Task Name & Target
    const isGdrive = (task.task_type || task.type) === 'gdrive_ingest' || (task.task_type || task.type) === 'gdrive';
    $('taskNameText').textContent = isGdrive ? 'Google Drive Ingestion & Compression' : 'AI Facial Recognition Indexing';
    let targetEv = task.event_slug || task.event || activeSlug;
    if (['wedding', 'default', 'brk-sir', 'brksir'].includes(targetEv)) targetEv = 'BRK Sir';
    $('taskEventTargetText').textContent = `${targetEv} > ${task.target_folder || task.target || 'All Albums'}`;

    // Progress Bar
    const pct = Math.min(100, Math.max(0, task.progress_pct !== undefined ? task.progress_pct : (status === 'completed' ? 100 : 15)));
    $('taskProgressFill').style.width = `${pct}%`;

    // Step Description
    $('taskStepDesc').textContent = task.current_step || task.message || (status === 'completed' ? 'Task finished successfully.' : 'Processing in background...');

    // Stats
    const files = task.processed_files !== undefined && task.total_files ? `${task.processed_files} / ${task.total_files} files` : `${task.processed_files || 0} files`;
    $('taskFilesStat').textContent = files;

    const savedMb = task.storage_saved_mb || 0;
    $('taskStorageStat').textContent = `${savedMb.toFixed(1)} MB saved (98.5% VPS offload)`;

    const nowMs = Date.now();

    // Rate-limited auto-sync photo library if task is running and user is viewing photos (at most once every 15s)
    if ((isRunning || status === 'running') && $('autoRefreshCheckbox')?.checked && !isRefreshingPhotos && (nowMs - lastAutoPhotoRefreshTime > 15000)) {
      const curTab = localStorage.getItem('admin_active_tab');
      if (curTab === 'photos') {
        lastAutoPhotoRefreshTime = nowMs;
        loadPhotos($('filterAlbumSelect')?.value || '', true);
      }
    }

    // Auto-update Upload Queue if upload panel is active or if tasks are running/queued (spaced cleanly)
    const uploadPanel = $('panel-upload');
    const isUploadPanelVisible = uploadPanel && !uploadPanel.hidden;
    if (isUploadPanelVisible || hasActiveQueueTasks) {
      if (nowMs - lastQueueRefreshTime > 5000) {
        lastQueueRefreshTime = nowMs;
        loadUploadQueue();
      }
    }

    if (isRunning || status === 'running') {
      if (nowMs - lastHistoryRefreshTime > 6000) {
        lastHistoryRefreshTime = nowMs;
        loadTaskHistory(true);
      }
      if (currentInlineLogTaskId) {
        openInlineTaskLogs(currentInlineLogTaskId, true);
      }
      if (nowMs - lastSidebarAutoSyncTime > 30000) {
        lastSidebarAutoSyncTime = nowMs;
        loadAlbums();
      }
    } else {
      if (nowMs - lastHistoryRefreshTime > 20000) {
        lastHistoryRefreshTime = nowMs;
        loadTaskHistory(true);
        loadAlbums();
      }
    }

    // Polling rate and auto-close timer on completion
    clearTimeout(pollTimer);
    if (isRunning || status === 'running' || hasActiveQueueTasks) {
      pollTimer = setTimeout(pollTaskStatus, 3500);
    } else {
      if (!taskDismissTimer) {
        taskDismissTimer = setTimeout(() => {
          $('visualTaskCard').hidden = true;
          sessionStorage.setItem('dismissed_task_' + taskId, 'true');
          taskDismissTimer = null;
        }, 6000);
      }
      pollTimer = setTimeout(pollTaskStatus, 15000);
    }
  } catch (e) {
    console.error('Error polling task:', e);
    clearTimeout(pollTimer);
    pollTimer = setTimeout(pollTaskStatus, 8000);
  } finally {
    isPollingTaskStatus = false;
  }
}

async function stopActiveTask() {
  const btn = $('taskStopBtn');
  if (!confirm('Are you sure you want to stop this running background task?')) return;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span>Stopping...</span>';
  }

  // Immediately reflect stopped state in UI
  const statusTxt = $('taskStatusText');
  if (statusTxt) statusTxt.textContent = 'STOPPED';
  const pill = $('taskStatusPill');
  if (pill) pill.className = 'task-status-pill failed';
  const pulse = $('taskPulseDot');
  if (pulse) pulse.style.display = 'none';
  $('taskStepDesc').textContent = 'Task stopped by administrator.';

  try {
    const res = await fetch('/api/admin/tasks/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    let j = {};
    try {
      j = await res.json();
    } catch (e) {}

    if (btn) {
      btn.style.display = 'none';
    }
    pollTaskStatus();
    loadTaskHistory();
  } catch (e) {
    console.warn('Note stopping task:', e);
    if (btn) {
      btn.style.display = 'none';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2"/></svg><span>Stop Task</span>`;
    }
  }
}

let currentInlineLogTaskId = null;
let currentRunningTaskId = null;

function viewRunningTaskLogsFromBanner() {
  switchTab('logs');
  if (currentRunningTaskId) {
    setTimeout(() => {
      openInlineTaskLogs(currentRunningTaskId);
      const row = document.getElementById(`task-row-${currentRunningTaskId}`);
      if (row) row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 150);
  }
}

function toggleTaskLogs(taskId) {
  if (currentInlineLogTaskId === taskId) {
    closeInlineTaskLogs();
  } else {
    openInlineTaskLogs(taskId);
  }
}

function closeInlineTaskLogs() {
  if (!currentInlineLogTaskId) return;
  const oldRow = document.getElementById(`inline-log-row-${currentInlineLogTaskId}`);
  if (oldRow) oldRow.remove();
  const oldBtn = document.getElementById(`btn-view-logs-${currentInlineLogTaskId}`);
  if (oldBtn) {
    oldBtn.textContent = 'View Logs';
    oldBtn.className = 'btn-outline';
    oldBtn.style.background = '';
    oldBtn.style.color = '';
    oldBtn.style.borderColor = '';
  }
  currentInlineLogTaskId = null;
}

async function openInlineTaskLogs(taskId, isLivePoll = false) {
  // If another task was open, close it first
  if (currentInlineLogTaskId && currentInlineLogTaskId !== taskId) {
    closeInlineTaskLogs();
  }

  currentInlineLogTaskId = taskId;
  const targetTr = document.getElementById(`task-row-${taskId}`);
  const btn = document.getElementById(`btn-view-logs-${taskId}`);
  if (btn) {
    btn.textContent = 'Hide Logs';
    btn.className = 'btn-primary';
    btn.style.background = '#0f5c4d';
    btn.style.borderColor = '#0f5c4d';
    btn.style.color = '#ffffff';
  }

  let logRow = document.getElementById(`inline-log-row-${taskId}`);
  if (!logRow && targetTr) {
    logRow = document.createElement('tr');
    logRow.id = `inline-log-row-${taskId}`;
    logRow.className = 'inline-task-log-row';
    logRow.innerHTML = `
      <td colspan="7" style="padding:0;background:#0d1117;border-bottom:2px solid #0f5c4d;">
        <div style="padding:0.85rem 1.15rem 1rem;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;color:#f8fafc;flex-wrap:wrap;gap:0.5rem;">
            <div style="display:flex;align-items:center;gap:0.55rem;flex-wrap:wrap;">
              <span style="font-weight:700;font-size:0.88rem;color:#6ee7b7;" id="inlineLogTitle-${taskId}">Task Execution Logs</span>
              <span style="background:rgba(255,255,255,0.12);padding:1px 6px;border-radius:4px;font-family:monospace;font-size:0.75rem;color:#94a3b8;">#${taskId}</span>
            </div>
            <div style="display:flex;align-items:center;gap:0.75rem;">
              <span style="font-size:0.78rem;color:#94a3b8;font-family:monospace;" id="inlineLogMeta-${taskId}">Loading logs...</span>
              <button type="button" class="btn-outline" style="padding:0.2rem 0.55rem;font-size:0.72rem;color:#ffffff;background:rgba(255,255,255,0.1);border-color:rgba(255,255,255,0.25);cursor:pointer;" onclick="closeInlineTaskLogs()">✕ Close</button>
            </div>
          </div>
          <pre class="console" id="inlineLogConsole-${taskId}" style="min-height:160px;max-height:380px;overflow-y:auto;background:#06090e;color:#4ade80;padding:0.85rem 1rem;border-radius:8px;font-family:monospace;font-size:0.78rem;line-height:1.55;white-space:pre-wrap;word-break:break-all;margin:0;border:1px solid rgba(255,255,255,0.12);">Loading execution logs...</pre>
        </div>
      </td>
    `;
    targetTr.after(logRow);
  }

  try {
    const res = await fetch(`/api/admin/tasks/${taskId}/logs`);
    if (!res.ok) return;
    const data = await res.json();
    const t = data.task;
    if (!t) return;

    const evDisp = ['wedding', 'default', 'brk-sir', 'brksir'].includes(t.event_slug) ? 'BRK Sir' : t.event_slug;
    const titleEl = $(`inlineLogTitle-${taskId}`);
    if (titleEl) {
      titleEl.textContent = `${t.task_type === 'gdrive_ingest' ? 'Google Drive Ingest' : 'AI Facial Index'} (${evDisp} > ${t.target_folder || 'All'})`;
    }
    const metaEl = $(`inlineLogMeta-${taskId}`);
    if (metaEl) {
      metaEl.textContent = `Started: ${formatToIST(t.started_at)} | Files: ${t.processed_files || 0} / ${t.total_files || '?'} | Saved: ${(t.storage_saved_mb || 0).toFixed(1)} MB | Status: ${t.status.toUpperCase()}`;
    }

    const consoleEl = $(`inlineLogConsole-${taskId}`);
    if (consoleEl) {
      const logs = t.log_text ? t.log_text.trim() : 'No log entries recorded for this task.';
      if (consoleEl.textContent !== logs) {
        const isScrolledToBottom = (consoleEl.scrollHeight - consoleEl.clientHeight) <= (consoleEl.scrollTop + 80);
        consoleEl.textContent = logs;
        if (isScrolledToBottom || !isLivePoll) {
          consoleEl.scrollTop = consoleEl.scrollHeight;
        }
      }
    }
  } catch (e) {
    console.error('Error fetching inline task logs:', e);
  }
}

function renderTaskRowInnerHtml(t) {
  const isExpanded = currentInlineLogTaskId === t.task_id;
  const isRunning = t.status === 'running';
  const isFailed = t.status === 'failed';
  const statusClass = isRunning ? 'running' : (isFailed ? 'failed' : 'completed');
  const timeStr = formatToIST(t.started_at);
  const isGdrive = t.task_type === 'gdrive_ingest';
  const typeLabel = isGdrive ? 'Google Drive Sync' : 'AI Facial Index';
  const driveUrl = t.source_url ? (t.source_url.length > 34 ? t.source_url.substring(0, 34) + '...' : t.source_url) : 'Direct Upload';
  const savedMb = t.storage_saved_mb ? `${t.storage_saved_mb.toFixed(1)} MB saved` : 'Optimized';

  return `
    <td style="font-family:monospace;font-size:0.8rem;white-space:nowrap;padding:0.75rem 1rem;">${timeStr}</td>
    <td style="padding:0.75rem 1rem;">
      <div style="font-weight:600;display:flex;align-items:center;gap:0.4rem;">
        <svg class="icon-sm" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        <span>${typeLabel}</span>
      </div>
    </td>
    <td style="padding:0.75rem 1rem;">
      <span style="font-weight:600;">${['wedding', 'default', 'brk-sir', 'brksir'].includes(t.event_slug) ? 'BRK Sir' : escapeHtml(t.event_slug)}</span>
      <span style="color:var(--muted);font-size:0.78rem;"> / ${escapeHtml(t.target_folder || 'All')}</span>
    </td>
    <td style="padding:0.75rem 1rem;">
      ${t.source_url ? `<a href="${t.source_url}" target="_blank" style="color:var(--primary);text-decoration:none;font-family:monospace;font-size:0.78rem;">${escapeHtml(driveUrl)} ↗</a>` : '<span style="color:var(--muted);font-size:0.78rem;">Manual / Direct</span>'}
    </td>
    <td style="padding:0.75rem 1rem;">
      <div id="task-files-${t.task_id}" style="font-weight:600;">${t.processed_files || 0} / ${t.total_files || '?'} files</div>
      <div id="task-saved-${t.task_id}" style="font-size:0.75rem;color:var(--emerald);font-weight:600;">${savedMb}</div>
    </td>
    <td style="padding:0.75rem 1rem;" id="task-status-cell-${t.task_id}">
      <span class="task-status-pill ${statusClass}" style="font-size:0.68rem;padding:0.15rem 0.5rem;">
        ${isRunning ? '<span class="status-pulse-dot"></span>' : ''}
        <span>${t.status.toUpperCase()}</span>
      </span>
    </td>
    <td style="padding:0.75rem 1rem;text-align:right;">
      <button id="btn-view-logs-${t.task_id}" class="${isExpanded ? 'btn-primary' : 'btn-outline'}" style="padding:0.25rem 0.6rem;font-size:0.75rem;${isExpanded ? 'background:#0f5c4d;border-color:#0f5c4d;color:#fff;' : ''}" onclick="toggleTaskLogs('${t.task_id}')">${isExpanded ? 'Hide Logs' : 'View Logs'}</button>
    </td>
  `;
}

async function loadTaskHistory(isLiveUpdate = false) {
  try {
    const res = await fetch('/api/admin/tasks/history?limit=30');
    if (!res.ok) return;
    const data = await res.json();
    const tasks = data.tasks || [];
    
    if ($('taskHistoryCountBadge')) {
      $('taskHistoryCountBadge').textContent = `${tasks.length} tasks`;
    }

    const tbody = $('taskHistoryTableBody');
    if (!tbody) return;

    if (tasks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="padding:1.5rem;text-align:center;color:var(--muted);">No background tasks recorded yet.</td></tr>';
      return;
    }

    // Remove empty placeholder row if tasks exist
    const emptyRow = tbody.querySelector('td[colspan="7"]');
    if (emptyRow && !emptyRow.closest('.inline-task-log-row')) tbody.innerHTML = '';

    const taskIds = new Set(tasks.map(t => t.task_id));

    // Update existing rows or insert new tasks in place (no DOM wiping = no flickering)
    for (let i = 0; i < tasks.length; i++) {
      const t = tasks[i];
      let tr = document.getElementById(`task-row-${t.task_id}`);
      const isRunning = t.status === 'running';
      const isFailed = t.status === 'failed';
      const statusClass = isRunning ? 'running' : (isFailed ? 'failed' : 'completed');
      const savedMb = t.storage_saved_mb ? `${t.storage_saved_mb.toFixed(1)} MB saved` : 'Optimized';

      if (tr) {
        // In-place updates for live changes
        const filesEl = document.getElementById(`task-files-${t.task_id}`);
        if (filesEl) filesEl.textContent = `${t.processed_files || 0} / ${t.total_files || '?'} files`;
        const savedEl = document.getElementById(`task-saved-${t.task_id}`);
        if (savedEl) savedEl.textContent = savedMb;

        const statusCell = document.getElementById(`task-status-cell-${t.task_id}`);
        if (statusCell) {
          statusCell.innerHTML = `
            <span class="task-status-pill ${statusClass}" style="font-size:0.68rem;padding:0.15rem 0.5rem;">
              ${isRunning ? '<span class="status-pulse-dot"></span>' : ''}
              <span>${t.status.toUpperCase()}</span>
            </span>
          `;
        }
      } else {
        // New task row: create element and insert in correct position
        tr = document.createElement('tr');
        tr.id = `task-row-${t.task_id}`;
        tr.innerHTML = renderTaskRowInnerHtml(t);
        if (i === 0) {
          tbody.prepend(tr);
        } else {
          const prevTask = tasks[i - 1];
          const prevRow = document.getElementById(`inline-log-row-${prevTask.task_id}`) || document.getElementById(`task-row-${prevTask.task_id}`);
          if (prevRow && prevRow.nextSibling) {
            tbody.insertBefore(tr, prevRow.nextSibling);
          } else {
            tbody.appendChild(tr);
          }
        }
      }
    }

    // Remove any stale task rows no longer present (excluding inline log rows)
    const allTrs = Array.from(tbody.querySelectorAll('tr[id^="task-row-"]'));
    for (const r of allTrs) {
      const tid = r.id.replace('task-row-', '');
      if (!taskIds.has(tid)) {
        const inlineLog = document.getElementById(`inline-log-row-${tid}`);
        if (inlineLog) inlineLog.remove();
        r.remove();
      }
    }

    // If an inline log row is currently open, seamlessly fetch newest logs in place
    if (currentInlineLogTaskId && taskIds.has(currentInlineLogTaskId)) {
      openInlineTaskLogs(currentInlineLogTaskId, true);
    }
  } catch (e) {
    console.error('Error loading task history:', e);
  }
}

async function loadLogs() {
  loadTaskHistory();
  try {
    const res = await fetch('/api/admin/logs');
    if (!res.ok) return;
    const j = await res.json();
    $('logsConsole').textContent = (j.logs || []).join('\n') || 'No global log activity recorded yet.';
    $('logsConsole').scrollTop = $('logsConsole').scrollHeight;
  } catch (e) {}
}

$('refreshLogsBtn')?.addEventListener('click', loadLogs);
$('toggleRawLogsBtn')?.addEventListener('click', () => {
  const blk = $('rawSystemLogsBlock');
  if (blk) {
    blk.hidden = !blk.hidden;
    if (!blk.hidden) loadLogs();
  }
});

// Date picker and auto-slug listeners for Album creation
$('albumDatePicker')?.addEventListener('change', () => {
  const val = $('albumDatePicker').value;
  if (!val) return;
  const [y, m, d] = val.split('-');
  const dateFormatted = `${d}-${m}-${y}`;
  $('albumNameInput').value = dateFormatted;
  $('albumSlugInput').value = dateFormatted;
});

$('btnTodayDate')?.addEventListener('click', () => {
  const today = new Date();
  const dateFormatted = formatDateDDMMYYYY(today);
  const pickerVal = formatDateYYYYMMDD(today);
  $('albumDatePicker').value = pickerVal;
  $('albumNameInput').value = dateFormatted;
  $('albumSlugInput').value = dateFormatted;
});

$('albumNameInput')?.addEventListener('input', () => {
  $('albumSlugInput').value = generateSlug($('albumNameInput').value);
});

// Create Album
$('createAlbumForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  const payload = {
    name: $('albumNameInput').value.trim(),
    slug: $('albumSlugInput').value.trim()
  };
  let res = await fetch('/api/admin/albums', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    res = await fetch(`/api/admin/events/${activeSlug}/albums`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  }
  if (res.ok) {
    closeModal('albumModal');
    await loadAlbums();
    await loadDashboard();
  } else {
    const j = await res.json().catch(() => ({}));
    alert(j.detail || 'Error creating album.');
  }
});

// Trash Bin Management
let activeTrashSubtab = 'photos';
let trashData = { photos: [], albums: [], events: [] };

function switchTrashTab(subtab) {
  activeTrashSubtab = subtab;
  document.querySelectorAll('.trash-subtab').forEach(btn => {
    btn.classList.toggle('active', btn.id === `trashTab${subtab.charAt(0).toUpperCase() + subtab.slice(1)}`);
  });
  renderTrashView();
}

async function loadTrash() {
  const view = $('trashListView');
  if (!view) return;
  view.innerHTML = '<div style="color:var(--muted);font-size:0.85rem;">Fetching Trash Bin contents...</div>';
  try {
    const res = await fetch('/api/admin/trash');
    if (!res.ok) {
      view.innerHTML = '<div style="color:var(--err);font-size:0.85rem;">Failed to load Trash Bin.</div>';
      return;
    }
    trashData = await res.json();
    $('trashCountPhotos').textContent = (trashData.photos || []).length;
    $('trashCountAlbums').textContent = (trashData.albums || []).length;
    $('trashCountEvents').textContent = (trashData.events || []).length;
    renderTrashView();
  } catch (e) {
    view.innerHTML = `<div style="color:var(--err);font-size:0.85rem;">Error loading trash: ${e}</div>`;
  }
}

function renderTrashView() {
  const view = $('trashListView');
  if (!view) return;
  view.replaceChildren();

  if (activeTrashSubtab === 'photos') {
    const photos = trashData.photos || [];
    if (photos.length === 0) {
      view.innerHTML = '<div style="color:var(--muted);padding:2.5rem;text-align:center;border:1px dashed var(--line);border-radius:12px;font-size:0.9rem;">Trash Bin has no deleted photos.</div>';
      return;
    }
    const grid = document.createElement('div');
    grid.className = 'photo-grid';
    for (const p of photos) {
      const item = document.createElement('div');
      item.className = 'photo-item';
      const enc = p.rel_path.split('/').map(encodeURIComponent).join('/');
      const safeFilename = (p.filename || '').replace(/'/g, "\\'");
      item.innerHTML = `
        <img src="/thumb/${enc}" loading="lazy" style="opacity:0.75;filter:grayscale(40%);">
        <div class="photo-meta">
          <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${p.filename}</div>
          <div style="color:var(--muted);font-size:0.75rem;">${p.event_name || p.event_slug} / ${p.album_name || 'Folder'}</div>
          <div style="display:flex;gap:0.4rem;margin-top:0.5rem;">
            <button class="btn-primary" style="flex:1;padding:0.25rem;font-size:0.75rem;justify-content:center;" onclick="restoreTrashItem('photo', ${p.id})">Restore</button>
            <button class="btn-outline" style="padding:0.25rem 0.5rem;font-size:0.75rem;color:var(--err);border-color:var(--err);" onclick="permanentDeleteTrashItem('photo', ${p.id}, '${safeFilename}')">Purge</button>
          </div>
        </div>
      `;
      grid.append(item);
    }
    view.append(grid);
  } else if (activeTrashSubtab === 'albums') {
    const albums = trashData.albums || [];
    if (albums.length === 0) {
      view.innerHTML = '<div style="color:var(--muted);padding:2.5rem;text-align:center;border:1px dashed var(--line);border-radius:12px;font-size:0.9rem;">Trash Bin has no deleted folders or albums.</div>';
      return;
    }
    const list = document.createElement('div');
    list.style.display = 'grid';
    list.style.gridTemplateColumns = 'repeat(auto-fill, minmax(320px, 1fr))';
    list.style.gap = '1rem';
    for (const a of albums) {
      const card = document.createElement('div');
      card.className = 'person-card';
      const safeSlug = (a.slug || '').replace(/'/g, "\\'");
      card.innerHTML = `
        <div style="width:48px;height:48px;border-radius:10px;background:#fee2e2;color:#ef4444;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
          <svg class="icon" viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        </div>
        <div style="flex:1;min-width:0;">
          <h4 style="font-size:1rem;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${a.name}</h4>
          <div style="font-size:0.78rem;color:var(--muted);font-family:monospace;">slug: ${a.slug} | event: ${a.event_name || a.event_slug}</div>
          <div style="font-size:0.8rem;color:var(--err);font-weight:600;margin-top:0.2rem;">${a.photo_count || 0} photos in folder</div>
          <div style="display:flex;gap:0.4rem;margin-top:0.5rem;flex-wrap:wrap;">
            <button class="btn-primary" style="padding:0.25rem 0.6rem;font-size:0.75rem;" onclick="restoreTrashItem('album', ${a.id})">Restore Folder</button>
            <button class="btn-outline" style="padding:0.25rem 0.6rem;font-size:0.75rem;color:var(--err);border-color:var(--err);" onclick="permanentDeleteTrashItem('album', ${a.id}, '${safeSlug}')">Purge from Disk</button>
          </div>
        </div>
      `;
      list.append(card);
    }
    view.append(list);
  } else if (activeTrashSubtab === 'events') {
    const events = trashData.events || [];
    if (events.length === 0) {
      view.innerHTML = '<div style="color:var(--muted);padding:2.5rem;text-align:center;border:1px dashed var(--line);border-radius:12px;font-size:0.9rem;">Trash Bin has no archived events.</div>';
      return;
    }
    const list = document.createElement('div');
    list.style.display = 'grid';
    list.style.gridTemplateColumns = 'repeat(auto-fill, minmax(320px, 1fr))';
    list.style.gap = '1rem';
    for (const ev of events) {
      const card = document.createElement('div');
      card.className = 'person-card';
      const safeSlug = (ev.slug || '').replace(/'/g, "\\'");
      card.innerHTML = `
        <div style="width:48px;height:48px;border-radius:10px;background:#fee2e2;color:#ef4444;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
          <svg class="icon" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        </div>
        <div style="flex:1;min-width:0;">
          <h4 style="font-size:1rem;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${ev.name}</h4>
          <div style="font-size:0.78rem;color:var(--muted);font-family:monospace;">slug: ${ev.slug}</div>
          <div style="display:flex;gap:0.4rem;margin-top:0.5rem;flex-wrap:wrap;">
            <button class="btn-primary" style="padding:0.25rem 0.6rem;font-size:0.75rem;" onclick="restoreTrashItem('event', ${ev.id})">Restore Event</button>
            <button class="btn-outline" style="padding:0.25rem 0.6rem;font-size:0.75rem;color:var(--err);border-color:var(--err);" onclick="permanentDeleteTrashItem('event', ${ev.id}, '${safeSlug}')">Purge Disk & DB</button>
          </div>
        </div>
      `;
      list.append(card);
    }
    view.append(list);
  }
}

async function restoreTrashItem(type, id) {
  try {
    const res = await fetch('/api/admin/trash/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, id })
    });
    if (res.ok) {
      await loadTrash();
      await checkAuth();
    } else {
      const j = await res.json();
      alert(j.detail || 'Failed to restore item');
    }
  } catch (e) {
    alert('Error restoring item: ' + e);
  }
}

function permanentDeleteTrashItem(type, id, confirmName) {
  openDeleteConfirmModal(
    `Permanently Purge ${type.toUpperCase()}?`,
    `This will <strong>permanently purge this ${type} from the VPS disk</strong> and erase all database records. This action cannot be reversed!`,
    'DELETE',
    async () => {
      const res = await fetch('/api/admin/trash/permanent-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, id })
      });
      if (res.ok) {
        await loadTrash();
        await checkAuth();
      } else {
        const j = await res.json();
        alert(j.detail || 'Failed to purge item');
      }
    },
    'Purge Permanently'
  );
}

function confirmEmptyTrash() {
  openDeleteConfirmModal(
    'Empty Entire Trash Bin?',
    'This will <strong>permanently remove all trashed events, folders, and photos from the VPS disk</strong>. All files will be deleted forever!',
    'EMPTY TRASH',
    async () => {
      const res = await fetch('/api/admin/trash/permanent-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'all' })
      });
      if (res.ok) {
        await loadTrash();
        await checkAuth();
      } else {
        const j = await res.json();
        alert(j.detail || 'Failed to empty trash');
      }
    },
    'Empty Trash Forever'
  );
}

// ===========================================================================
// SUB-VIEW SWITCHERS & AI MATCHING MODULES
// ===========================================================================

function switchPhotoSubView(view) {
  const isGrid = view === 'grid';
  const btnGrid = $('btnSubPhotosGrid');
  const btnDups = $('btnSubDuplicates');
  const toolbar = $('photoGridToolbar');
  const grid = $('photoLibraryGrid');
  const dupSec = $('duplicateReviewSection');

  if (btnGrid) btnGrid.classList.toggle('active', isGrid);
  if (btnDups) btnDups.classList.toggle('active', !isGrid);
  if (toolbar) toolbar.hidden = !isGrid;
  if (grid) grid.hidden = !isGrid;
  if (dupSec) dupSec.hidden = isGrid;

  if (!isGrid) {
    loadDuplicates();
  }
}

async function loadDuplicatesBadge() {
  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/duplicates?status=pending`);
    if (!res.ok) return;
    const j = await res.json();
    const count = j.count || 0;
    const badge = $('dupBadgeCount');
    if (badge) badge.textContent = count;
  } catch (e) {}
}

async function loadDuplicates() {
  const list = $('duplicateGroupsList');
  if (!list) return;
  list.innerHTML = '<div style="color:var(--muted);padding:1rem;">Loading duplicate analysis...</div>';

  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/duplicates?status=pending`);
    if (!res.ok) throw new Error('Failed to fetch duplicates');
    const j = await res.json();
    const groups = j.groups || [];

    const badge = $('dupBadgeCount');
    if (badge) badge.textContent = groups.length;

    list.replaceChildren();

    if (groups.length === 0) {
      list.innerHTML = `
        <div style="text-align:center;padding:2.5rem 1rem;background:#fff;border-radius:12px;border:1px solid var(--line);">
          <div style="font-size:2rem;margin-bottom:0.5rem;">🎉</div>
          <h4 style="font-size:1.1rem;font-weight:700;margin-bottom:0.35rem;">No Duplicate Photos Detected</h4>
          <p style="color:var(--muted);font-size:0.85rem;max-width:440px;margin:0 auto 1.25rem;">
            All indexed photographs are unique. You can trigger a fresh audit scan anytime.
          </p>
          <button class="btn-primary" onclick="triggerDuplicateScan()" style="padding:0.45rem 1rem;">Run Duplicate Scan Now</button>
        </div>
      `;
      return;
    }

    groups.forEach((g, gIdx) => {
      const card = document.createElement('div');
      card.className = 'dup-group-card';
      const isExact = g.match_type === 'exact';
      const typeLabel = isExact ? 'Exact Match (SHA-256)' : `Visually Identical (dHash Match: ${(g.similarity_score * 100).toFixed(0)}%)`;
      const typeBadgeClass = isExact ? 'badge-published' : 'badge-syncing';

      card.innerHTML = `
        <div class="dup-group-header">
          <div>
            <span class="badge ${typeBadgeClass}" style="margin-right:0.5rem;font-size:0.75rem;">${typeLabel}</span>
            <span style="font-weight:700;font-size:0.92rem;">Group #${gIdx + 1} (${g.photos.length} photos)</span>
          </div>
          <div style="display:flex;gap:0.5rem;align-items:center;">
            <button class="btn-outline" style="padding:0.35rem 0.65rem;font-size:0.78rem;" onclick="ignoreDuplicateGroup('${g.group_id}')">Keep Both / Ignore</button>
            <button class="btn-primary" style="padding:0.35rem 0.75rem;font-size:0.78rem;background:var(--err);border-color:var(--err);" onclick="resolveSelectedDuplicates('${g.group_id}')">🗑 Move Selected to Trash</button>
          </div>
        </div>
        <div class="dup-photos-row" id="dupRow_${g.group_id}"></div>
      `;

      const photosRow = card.querySelector(`#dupRow_${g.group_id}`);
      g.photos.forEach(p => {
        const pItem = document.createElement('div');
        pItem.className = `dup-photo-item ${p.is_recommended ? 'is-primary' : ''}`;
        const enc = p.rel_path.split('/').map(encodeURIComponent).join('/');
        const fSizeMb = p.file_size_bytes ? (p.file_size_bytes / (1024 * 1024)).toFixed(2) + ' MB' : '--';
        const resText = (p.width && p.height) ? `${p.width} × ${p.height} px` : '--';

        pItem.innerHTML = `
          ${p.is_recommended ? '<span class="dup-rec-badge">★ Recommended Original</span>' : ''}
          <img src="/thumb/${enc}" class="dup-thumb-img" alt="${escapeHtml(p.filename)}" loading="lazy">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.25rem;">
            <span style="font-weight:700;font-size:0.82rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px;" title="${escapeHtml(p.filename)}">${escapeHtml(p.filename)}</span>
            ${!p.is_recommended ? `<label style="font-size:0.75rem;display:flex;align-items:center;gap:3px;cursor:pointer;"><input type="checkbox" class="dup-select-chk" data-group="${g.group_id}" data-id="${p.id}" checked> Delete</label>` : ''}
          </div>
          <div class="dup-meta-line"><span>Resolution:</span> <strong>${resText}</strong></div>
          <div class="dup-meta-line"><span>File Size:</span> <strong>${fSizeMb}</strong></div>
          <div class="dup-meta-line"><span>Folder:</span> <strong>${escapeHtml(p.album_name || 'Root')}</strong></div>
          <div class="dup-meta-line"><span>Quality Grade:</span> <strong style="text-transform:capitalize;">${p.quality_grade || 'Good'}</strong></div>
        `;
        photosRow.appendChild(pItem);
      });

      list.appendChild(card);
    });

  } catch (err) {
    list.innerHTML = `<div style="color:var(--err);padding:1rem;">Failed to load duplicates: ${err.message}</div>`;
  }
}

async function triggerDuplicateScan() {
  try {
    const btn = document.activeElement;
    if (btn) btn.disabled = true;
    const res = await fetch(`/api/admin/events/${activeSlug}/duplicates/scan`, { method: 'POST' });
    if (!res.ok) throw new Error('Scan request failed');
    const j = await res.json();
    const sum = j.summary || {};
    alert(`Duplicate audit complete: ${sum.exact_groups || 0} exact groups and ${sum.perceptual_groups || 0} perceptual duplicate groups found.`);
    await loadDuplicates();
  } catch (e) {
    alert('Duplicate scan failed: ' + e.message);
  } finally {
    const btn = document.activeElement;
    if (btn) btn.disabled = false;
  }
}

async function ignoreDuplicateGroup(groupId) {
  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/duplicates/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: groupId, action: 'ignore' })
    });
    if (!res.ok) throw new Error('Failed to ignore group');
    loadDuplicates();
  } catch (e) {
    alert(e.message);
  }
}

async function resolveSelectedDuplicates(groupId) {
  const checkboxes = document.querySelectorAll(`input.dup-select-chk[data-group="${groupId}"]:checked`);
  const photoIds = Array.from(checkboxes).map(cb => parseInt(cb.getAttribute('data-id'), 10));
  if (photoIds.length === 0) {
    alert('Please check at least one duplicate photo to delete.');
    return;
  }

  if (!confirm(`Are you sure you want to move ${photoIds.length} duplicate photo(s) to Trash? The recommended original will be preserved.`)) {
    return;
  }

  try {
    const res = await fetch(`/api/admin/events/${activeSlug}/duplicates/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: groupId, action: 'delete_selected', photo_ids: photoIds })
    });
    if (!res.ok) throw new Error('Failed to resolve duplicates');
    const j = await res.json();
    loadDuplicates();
  } catch (e) {
    alert(e.message);
  }
}

// ===========================================================================
// CONFIDENCE REVIEW & HUMAN FEEDBACK
// ===========================================================================

function switchPeopleSubView(view) {
  const isGrid = view === 'grid';
  const btnGrid = $('btnSubPeopleGrid');
  const btnRev = $('btnSubReviews');
  const grid = $('peopleGrid');
  const revSec = $('confidenceReviewSection');

  if (btnGrid) btnGrid.classList.toggle('active', isGrid);
  if (btnRev) btnRev.classList.toggle('active', !isGrid);
  if (grid) grid.hidden = !isGrid;
  if (revSec) revSec.hidden = isGrid;

  if (!isGrid) {
    loadMatchReviews();
  }
}

async function loadMatchReviews() {
  const list = $('matchReviewsList');
  if (!list) return;
  list.innerHTML = '<div style="color:var(--muted);padding:1rem;">Loading confidence reviews...</div>';

  const tier = $('reviewTierSelect') ? $('reviewTierSelect').value : 'all';
  const status = $('reviewStatusSelect') ? $('reviewStatusSelect').value : 'pending';

  try {
    const res = await fetch(`/api/admin/matching/reviews?event_slug=${activeSlug}&status=${status}&tier=${tier}`);
    if (!res.ok) throw new Error('Failed to fetch reviews');
    const j = await res.json();
    const reviews = j.reviews || [];

    const badge = $('reviewBadgeCount');
    if (badge) badge.textContent = reviews.length;

    list.replaceChildren();

    if (reviews.length === 0) {
      list.innerHTML = `
        <div style="text-align:center;padding:2.5rem 1rem;background:#fff;border-radius:12px;border:1px solid var(--line);">
          <div style="font-size:2rem;margin-bottom:0.5rem;">👍</div>
          <h4 style="font-size:1.1rem;font-weight:700;margin-bottom:0.35rem;">No Pending Reviews in this Filter</h4>
          <p style="color:var(--muted);font-size:0.85rem;">All searches have high confidence or have already been reviewed.</p>
        </div>
      `;
      return;
    }

    reviews.forEach(mr => {
      const card = document.createElement('div');
      card.className = 'review-pair-card';

      const tierBadge = mr.confidence_tier === 'high'
        ? '<span class="tier-badge tier-high">High Confidence</span>'
        : (mr.confidence_tier === 'medium'
          ? '<span class="tier-badge tier-medium">Medium Confidence</span>'
          : '<span class="tier-badge tier-low">Low Confidence</span>');

      const statusBadge = mr.feedback === 'correct'
        ? '<span class="badge badge-published">✓ Verified Correct</span>'
        : (mr.feedback === 'incorrect'
          ? '<span class="badge badge-trashed">✕ Marked Incorrect</span>'
          : '<span class="badge" style="background:#f1f5f9;color:#64748b;">Pending Review</span>');

      const encMatch = (mr.matched_rel_path || '').split('/').map(encodeURIComponent).join('/');
      const queryCropUrl = `/thumb/${mr.query_crop_path}`;
      const matchThumbUrl = `/thumb/${encMatch}`;

      card.innerHTML = `
        <div class="review-crop-box">
          <img src="${queryCropUrl}" class="review-crop-img" alt="Query Face" onerror="this.src='/favicon.ico'">
          <span style="font-size:0.7rem;font-weight:700;color:var(--muted);">Query Face</span>
        </div>
        <div style="font-size:1.4rem;color:var(--muted);font-weight:300;">⇄</div>
        <div class="review-crop-box">
          <img src="${matchThumbUrl}" class="review-crop-img" alt="Matched Library Photo" onerror="this.src='/favicon.ico'">
          <span style="font-size:0.7rem;font-weight:700;color:var(--muted);">Matched Photo</span>
        </div>
        <div class="review-meta-box">
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.35rem;flex-wrap:wrap;">
            ${tierBadge}
            ${statusBadge}
          </div>
          <div style="font-weight:700;font-size:0.92rem;margin-bottom:0.25rem;">
            ArcFace Cosine Similarity: <code style="font-size:0.95rem;color:var(--ink);background:#f1f5f9;padding:1px 6px;border-radius:4px;">${mr.similarity_score.toFixed(4)}</code>
          </div>
          <div style="font-size:0.8rem;color:var(--muted);margin-bottom:0.15rem;">
            Matched File: <strong>${escapeHtml(mr.filename || mr.matched_rel_path)}</strong> (${escapeHtml(mr.album_name || 'Folder')})
          </div>
          <div style="font-size:0.75rem;color:var(--muted);">
            Search ID: <span style="font-family:monospace;">${escapeHtml(mr.search_id || '--')}</span> • ${mr.created_at || ''}
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:0.4rem;">
          <button class="btn-primary" style="padding:0.4rem 0.85rem;font-size:0.8rem;background:#059669;border-color:#059669;" onclick="submitMatchFeedback(${mr.id}, 'correct')">✓ Correct Match</button>
          <button class="btn-outline" style="padding:0.4rem 0.85rem;font-size:0.8rem;color:var(--err);border-color:var(--err);" onclick="submitMatchFeedback(${mr.id}, 'incorrect')">✕ Incorrect Match</button>
        </div>
      `;

      list.appendChild(card);
    });

  } catch (err) {
    list.innerHTML = `<div style="color:var(--err);padding:1rem;">Failed to load reviews: ${err.message}</div>`;
  }
}

async function submitMatchFeedback(reviewId, feedback) {
  try {
    const res = await fetch('/api/admin/matching/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ review_id: reviewId, feedback: feedback })
    });
    if (!res.ok) throw new Error('Feedback submission failed');
    loadMatchReviews();
  } catch (e) {
    alert(e.message);
  }
}

// ===========================================================================
// SETTINGS & CONFIGURATION
// ===========================================================================

async function loadSystemSettings() {
  try {
    const res = await fetch('/api/admin/settings');
    if (!res.ok) return;
    const j = await res.json();
    const s = j.settings || {};

    if (s.match_threshold_high && $('threshHighRange')) {
      $('threshHighRange').value = s.match_threshold_high;
      $('threshHighVal').textContent = s.match_threshold_high;
    }
    if (s.match_threshold_medium && $('threshMedRange')) {
      $('threshMedRange').value = s.match_threshold_medium;
      $('threshMedVal').textContent = s.match_threshold_medium;
    }
    if (s.match_threshold_min && $('threshMinRange')) {
      $('threshMinRange').value = s.match_threshold_min;
      $('threshMinVal').textContent = s.match_threshold_min;
    }
    if (s.dedup_dhash_threshold && $('threshDhashRange')) {
      $('threshDhashRange').value = s.dedup_dhash_threshold;
      $('threshDhashVal').textContent = s.dedup_dhash_threshold;
    }
    if (s.default_event_slug && $('defaultSlugInput')) {
      $('defaultSlugInput').value = s.default_event_slug;
    }
  } catch (e) {}

  // Also load Security & Multi-Factor settings and audit logs
  loadSecuritySettings();
  loadSecurityLogs();
}

async function saveSystemSettings() {
  const payload = {
    match_threshold_high: $('threshHighRange') ? $('threshHighRange').value : '0.60',
    match_threshold_medium: $('threshMedRange') ? $('threshMedRange').value : '0.48',
    match_threshold_min: $('threshMinRange') ? $('threshMinRange').value : '0.40',
    dedup_dhash_threshold: $('threshDhashRange') ? $('threshDhashRange').value : '4',
    default_event_slug: $('defaultSlugInput') ? $('defaultSlugInput').value.trim() : 'brk-sir'
  };

  try {
    const res = await fetch('/api/admin/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error('Failed to update settings');
    alert('Settings and AI face-matching thresholds saved successfully.');
  } catch (e) {
    alert('Error saving settings: ' + e.message);
  }
}

// ===========================================================================
// ADMIN SECURITY & MULTI-FACTOR AUTHENTICATION CONTROLLER
// ===========================================================================

let cachedTotpSetupData = null;

async function loadSecuritySettings() {
  try {
    const { ok, data } = await safeFetchJson('/api/admin/security/status');
    if (!ok || !data) return;

    // 1. Two-Step Verification (TOTP)
    const badge = $('totpStatusBadge');
    const enableBtn = $('enable2faBtn');
    const disableBtn = $('disable2faBtn');
    const recoverySec = $('recoveryCodesSection');
    const recoveryCount = $('recoveryCodesCount');

    if (badge) {
      if (data.totp_enabled) {
        badge.textContent = 'Active (2FA Protected)';
        badge.style.background = '#dcfce7';
        badge.style.color = '#15803d';
        if (enableBtn) enableBtn.hidden = true;
        if (disableBtn) disableBtn.hidden = false;
        if (recoverySec) recoverySec.hidden = false;
      } else {
        badge.textContent = 'Disabled (Password Only)';
        badge.style.background = '#fef9c3';
        badge.style.color = '#854d0e';
        if (enableBtn) enableBtn.hidden = false;
        if (disableBtn) disableBtn.hidden = true;
        if (recoverySec) recoverySec.hidden = true;
      }
    }

    if (recoveryCount && data.recovery_codes) {
      const unused = data.recovery_codes.unused_count || 0;
      const total = data.recovery_codes.total_count || 0;
      recoveryCount.textContent = `${unused} of ${total} available`;
      recoveryCount.style.color = unused > 2 ? '#0f5c4d' : '#dc2626';
    }

    // 2. Passkeys List
    renderPasskeysList(data.passkeys || []);

    // 3. Active Sessions List
    renderSessionsList(data.active_sessions || []);

  } catch (err) {
    console.error('Failed to load security settings:', err);
  }
}

function renderPasskeysList(passkeys) {
  const container = $('passkeysListContainer');
  if (!container) return;

  if (passkeys.length === 0) {
    container.innerHTML = `
      <div style="padding:1rem;background:#f9fafb;border-radius:8px;border:1px dashed var(--line);text-align:center;color:var(--muted);font-size:0.85rem;">
        No passkeys registered yet. Click <strong>"Register New Passkey"</strong> to enable instant biometric login with Face ID, Touch ID, or Windows Hello.
      </div>
    `;
    return;
  }

  let html = `
    <div style="border:1px solid var(--line);border-radius:8px;overflow:hidden;">
      <table style="width:100%;font-size:0.82rem;border-collapse:collapse;">
        <thead>
          <tr style="text-align:left;background:#f9fafb;border-bottom:1px solid var(--line);color:var(--muted);">
            <th style="padding:0.6rem 0.75rem;">Device Name</th>
            <th style="padding:0.6rem 0.75rem;">Registered On</th>
            <th style="padding:0.6rem 0.75rem;">Last Used</th>
            <th style="padding:0.6rem 0.75rem;text-align:right;">Actions</th>
          </tr>
        </thead>
        <tbody>
  `;

  passkeys.forEach(p => {
    const devNameEscaped = escapeHtml(p.device_name || 'Passkey Device');
    const safeJsName = JSON.stringify(p.device_name || 'Passkey Device').replace(/"/g, '&quot;');
    html += `
      <tr style="border-bottom:1px solid var(--line);">
        <td style="padding:0.65rem 0.75rem;font-weight:600;">
          <div style="display:flex;align-items:center;gap:0.5rem;">
            <svg class="icon" style="width:16px;height:16px;color:#0f5c4d;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            ${devNameEscaped}
          </div>
        </td>
        <td style="padding:0.65rem 0.75rem;color:var(--muted);">${escapeHtml(p.created_at || 'Recently')}</td>
        <td style="padding:0.65rem 0.75rem;color:var(--muted);">${escapeHtml(p.last_used_at || 'Never')}</td>
        <td style="padding:0.65rem 0.75rem;text-align:right;">
          <button class="btn-outline" onclick="renamePasskey(${p.id}, ${safeJsName})" style="font-size:0.75rem;padding:0.25rem 0.5rem;margin-right:0.35rem;">Rename</button>
          <button class="btn-outline" onclick="deletePasskey(${p.id}, ${safeJsName})" style="font-size:0.75rem;padding:0.25rem 0.5rem;color:var(--err);border-color:var(--err);">Revoke</button>
        </td>
      </tr>
    `;
  });

  html += `</tbody></table></div>`;
  container.innerHTML = html;
}

function renderSessionsList(sessions) {
  const tbody = $('sessionsTableBody');
  if (!tbody) return;

  if (sessions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="padding:1rem;text-align:center;color:var(--muted);">No active sessions found.</td></tr>';
    return;
  }

  tbody.replaceChildren();
  sessions.forEach(s => {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid var(--line)';

    const currentBadge = s.is_current ? `<span class="badge" style="background:#dcfce7;color:#15803d;font-size:0.7rem;padding:0.15rem 0.45rem;border-radius:4px;margin-left:0.4rem;">Current Session</span>` : '';
    const deviceStr = escapeHtml(s.user_agent ? (s.user_agent.length > 50 ? s.user_agent.substring(0, 50) + '...' : s.user_agent) : 'Browser');

    tr.innerHTML = `
      <td style="padding:0.65rem 0.75rem;font-weight:600;">
        ${deviceStr}${currentBadge}
      </td>
      <td style="padding:0.65rem 0.75rem;font-family:monospace;font-size:0.8rem;">${escapeHtml(s.ip_address || 'Unknown')}</td>
      <td style="padding:0.65rem 0.75rem;color:var(--muted);">${escapeHtml(s.created_at || '')}</td>
      <td style="padding:0.65rem 0.75rem;color:var(--muted);">${escapeHtml(s.last_active_at || '')}</td>
      <td style="padding:0.65rem 0.75rem;text-align:right;">
        ${s.is_current ? '<span style="color:var(--muted);font-size:0.75rem;">Active Now</span>' : `<button class="btn-outline" onclick="revokeSession(${s.id})" style="font-size:0.75rem;padding:0.25rem 0.5rem;color:var(--err);border-color:var(--err);">Revoke</button>`}
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function handleAdminChangePassword() {
  const currentPwd = $('currentPwdInput')?.value;
  const newPwd = $('newPwdInput')?.value;
  const confirmPwd = $('confirmPwdInput')?.value;
  const statusMsg = $('pwdStatusMsg');
  const btn = $('savePwdBtn');

  if (!currentPwd || !newPwd) return;
  if (newPwd !== confirmPwd) {
    if (statusMsg) {
      statusMsg.textContent = 'New passwords do not match.';
      statusMsg.style.color = 'var(--err)';
      statusMsg.hidden = false;
    }
    return;
  }
  if (newPwd.length < 8) {
    if (statusMsg) {
      statusMsg.textContent = 'Password must be at least 8 characters long.';
      statusMsg.style.color = 'var(--err)';
      statusMsg.hidden = false;
    }
    return;
  }

  if (btn) btn.disabled = true;
  if (statusMsg) statusMsg.hidden = true;

  try {
    const { ok, status, data: j } = await safeFetchJson('/api/admin/security/password/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPwd, new_password: newPwd })
    });
    if (ok) {
      if (statusMsg) {
        statusMsg.textContent = 'Password updated successfully!';
        statusMsg.style.color = '#15803d';
        statusMsg.hidden = false;
      }
      $('changePasswordForm')?.reset();
      loadSecurityLogs();
    } else {
      if (statusMsg) {
        statusMsg.textContent = j?.detail || j?.message || 'Password update failed.';
        statusMsg.style.color = 'var(--err)';
        statusMsg.hidden = false;
      }
    }
  } catch (err) {
    if (statusMsg) {
      statusMsg.textContent = 'Error: ' + err.message;
      statusMsg.style.color = 'var(--err)';
      statusMsg.hidden = false;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function openTotpSetupModal() {
  try {
    const { ok, status, data: j } = await safeFetchJson('/api/admin/security/totp/setup', { method: 'POST' });
    if (!ok) {
      alert(j?.detail || j?.message || 'Failed to start 2FA setup.');
      return;
    }

    cachedTotpSetupData = j;
    if ($('totpQrCodeImg')) {
      $('totpQrCodeImg').innerHTML = `<img src="${j.qr_svg}" alt="2FA QR Code" style="width:160px;height:160px;display:block;">`;
    }
    if ($('totpSecretText')) {
      $('totpSecretText').textContent = j.secret;
    }

    const codesContainer = $('totpModalRecoveryCodes');
    if (codesContainer && j.recovery_codes) {
      codesContainer.innerHTML = j.recovery_codes.map(c => `<div>${escapeHtml(c)}</div>`).join('');
    }

    if ($('confirmTotpCodeInput')) $('confirmTotpCodeInput').value = '';
    if ($('confirmTotpAlert')) $('confirmTotpAlert').hidden = true;

    openModal('totpSetupModal');
  } catch (err) {
    alert('Error setting up 2FA: ' + (err.detail || err.message));
  }
}

function copyRecoveryCodesFromModal() {
  if (!cachedTotpSetupData || !cachedTotpSetupData.recovery_codes) return;
  const text = "BRK Photos Admin - Emergency Recovery Codes\n" +
    "Generated on: " + new Date().toISOString() + "\n\n" +
    cachedTotpSetupData.recovery_codes.join('\n') +
    "\n\nEach code can only be used once for emergency account access.";
  navigator.clipboard.writeText(text);
  alert('Recovery codes copied to clipboard! Save them in a secure password manager.');
}

function downloadRecoveryCodesFromModal() {
  if (!cachedTotpSetupData || !cachedTotpSetupData.recovery_codes) return;
  const text = "BRK Photos Admin - Emergency Recovery Codes\n" +
    "Generated on: " + new Date().toISOString() + "\n\n" +
    cachedTotpSetupData.recovery_codes.join('\n') +
    "\n\nKeep these codes safe. Each code can only be used once.";
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `brk_recovery_codes_${new Date().toISOString().slice(0, 10)}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function handleConfirmTotpSetup() {
  const code = $('confirmTotpCodeInput')?.value.trim();
  const alertEl = $('confirmTotpAlert');
  const btn = $('confirmTotpBtn');

  if (!code || !cachedTotpSetupData) return;
  if (btn) btn.disabled = true;
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, status, data: j } = await safeFetchJson('/api/admin/security/totp/enable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        secret: cachedTotpSetupData.secret,
        code: code,
        recovery_codes: cachedTotpSetupData.recovery_codes
      })
    });
    if (ok) {
      closeModal('totpSetupModal');
      alert('Two-step verification has been activated successfully! Your account is now protected with 2FA.');
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      if (alertEl) {
        alertEl.textContent = j?.detail || j?.message || 'Verification code incorrect. Please verify device clock and try again.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Connection error: ' + (err.detail || err.message);
      alertEl.hidden = false;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openDisableTotpModal() {
  if ($('disableTotpPwdInput')) $('disableTotpPwdInput').value = '';
  if ($('disableTotpAlert')) $('disableTotpAlert').hidden = true;
  openModal('disableTotpModal');
}

async function handleConfirmDisableTotp() {
  const pwd = $('disableTotpPwdInput')?.value;
  const alertEl = $('disableTotpAlert');
  const btn = $('confirmDisableTotpBtn');

  if (!pwd) return;
  if (btn) btn.disabled = true;
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/security/totp/disable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd })
    });
    if (ok) {
      closeModal('disableTotpModal');
      alert('Two-step verification has been disabled.');
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      if (alertEl) {
        alertEl.textContent = j?.detail || j?.message || 'Incorrect password.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Error: ' + err.message;
      alertEl.hidden = false;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openRegenerateRecoveryModal() {
  if ($('regenRecoveryPwdInput')) $('regenRecoveryPwdInput').value = '';
  if ($('regenRecoveryAlert')) $('regenRecoveryAlert').hidden = true;
  openModal('regenRecoveryModal');
}

async function handleConfirmRegenRecovery() {
  const pwd = $('regenRecoveryPwdInput')?.value;
  const alertEl = $('regenRecoveryAlert');
  const btn = $('confirmRegenRecoveryBtn');

  if (!pwd) return;
  if (btn) btn.disabled = true;
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/security/recovery-codes/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd })
    });
    if (ok) {
      closeModal('regenRecoveryModal');
      const codes = j?.recovery_codes || [];
      const text = "BRK Photos Admin - Newly Regenerated Emergency Recovery Codes\n" +
        "Generated on: " + new Date().toISOString() + "\n\n" +
        codes.join('\n') +
        "\n\nPrevious recovery codes are now invalid. Keep these safe.";
      navigator.clipboard.writeText(text);
      alert('10 new recovery codes generated and copied to clipboard! Save them safely.');
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      if (alertEl) {
        alertEl.textContent = j?.detail || j?.message || 'Incorrect password.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Error: ' + err.message;
      alertEl.hidden = false;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function registerNewPasskey() {
  if (!window.PublicKeyCredential) {
    alert('WebAuthn Passkeys are not supported on this browser or platform.');
    return;
  }

  const deviceName = prompt('Enter a friendly name for this passkey device:', (navigator.platform || 'My Device') + ' Passkey');
  if (!deviceName) return;

  try {
    // 1. Request registration options from server
    const { ok: chalOk, status: chalStatus, data: chalData } = await safeFetchJson('/api/admin/security/passkey/register-challenge', { method: 'POST' });
    if (!chalOk) {
      alert(chalData?.detail || chalData?.message || 'Failed to start passkey registration.');
      return;
    }

    // 2. Prepare options
    const publicKeyOptions = {
      challenge: base64urlToBuffer(chalData.challenge),
      rp: chalData.rp,
      user: {
        id: base64urlToBuffer(chalData.user.id),
        name: chalData.user.name,
        displayName: chalData.user.displayName
      },
      pubKeyCredParams: chalData.pubKeyCredParams,
      authenticatorSelection: chalData.authenticatorSelection,
      timeout: chalData.timeout || 60000,
      excludeCredentials: (chalData.excludeCredentials || []).map(c => ({
        type: c.type,
        id: base64urlToBuffer(c.id)
      }))
    };

    // 3. Create credential via browser WebAuthn API
    const credential = await navigator.credentials.create({ publicKey: publicKeyOptions });
    if (!credential) return;

    // 4. Verify on server
    const clientDataJSON = bufferToBase64url(credential.response.clientDataJSON);
    const attestationObject = bufferToBase64url(credential.response.attestationObject);
    const credentialId = credential.id;

    const { ok: verifyOk, status: verifyStatus, data: verifyData } = await safeFetchJson('/api/admin/security/passkey/register-verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: chalData.challenge_id,
        credential_id: credentialId,
        public_key: attestationObject,
        device_name: deviceName,
        client_data_json: clientDataJSON
      })
    });

    if (verifyOk) {
      alert(`Passkey '${deviceName}' registered successfully! You can now use Face ID / Touch ID to sign in.`);
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      alert(verifyData?.detail || verifyData?.message || 'Failed to verify passkey with server.');
    }
  } catch (err) {
    if (err.name !== 'NotAllowedError') {
      alert('Passkey registration error: ' + (err.detail || err.message));
    }
  }
}

async function renamePasskey(passkeyId, currentName) {
  const newName = prompt('Enter new device name for this passkey:', currentName || '');
  if (!newName || !newName.trim() || newName === currentName) return;

  try {
    const { ok, data } = await safeFetchJson(`/api/admin/security/passkeys/${passkeyId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_name: newName.trim() })
    });
    if (ok) {
      loadSecuritySettings();
    } else {
      alert(data?.detail || data?.message || 'Failed to rename passkey.');
    }
  } catch (err) {
    alert('Error renaming passkey: ' + (err.detail || err.message));
  }
}

async function deletePasskey(passkeyId, deviceName) {
  const nameLabel = deviceName ? `'${deviceName}'` : 'this device';
  if (!confirm(`Are you sure you want to revoke passkey ${nameLabel}? It will no longer be able to log into this system.`)) return;

  try {
    const { ok, data } = await safeFetchJson(`/api/admin/security/passkeys/${passkeyId}`, { method: 'DELETE' });
    if (ok) {
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      alert(data?.detail || data?.message || 'Failed to revoke passkey.');
    }
  } catch (err) {
    alert('Error revoking passkey: ' + (err.detail || err.message));
  }
}

async function revokeSession(sessionId) {
  if (!confirm('Revoke this session? The device will be signed out immediately.')) return;
  try {
    const { ok, data } = await safeFetchJson('/api/admin/security/sessions/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId })
    });
    if (ok) {
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      alert(data?.detail || data?.message || 'Failed to revoke session.');
    }
  } catch (e) {
    alert('Error revoking session: ' + (e.detail || e.message));
  }
}

async function revokeOtherSessions() {
  if (!confirm('Revoke all other active sessions across other devices and browsers?')) return;
  try {
    const { ok, data } = await safeFetchJson('/api/admin/security/sessions/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revoke_all_others: true })
    });
    if (ok) {
      alert('All other sessions have been revoked.');
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      alert(data?.detail || data?.message || 'Failed to revoke other sessions.');
    }
  } catch (e) {
    alert('Error revoking sessions: ' + (e.detail || e.message));
  }
}

async function loadSecurityLogs() {
  const tbody = $('securityLogsTableBody');
  if (!tbody) return;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/security/logs?limit=50');
    if (!ok || !j) return;
    const logs = j.events || [];

    tbody.replaceChildren();
    if (logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="padding:1rem;text-align:center;color:var(--muted);">No security events recorded yet.</td></tr>';
      return;
    }

    logs.forEach(log => {
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid var(--line)';

      let badgeColor = '#6b7280';
      let badgeBg = '#f3f4f6';
      if (log.status === 'success') {
        badgeColor = '#15803d';
        badgeBg = '#dcfce7';
      } else if (log.status === 'failed' || log.status === 'blocked') {
        badgeColor = '#dc2626';
        badgeBg = '#fee2e2';
      }

      tr.innerHTML = `
        <td style="padding:0.6rem 0.75rem;white-space:nowrap;color:var(--muted);">${escapeHtml(log.created_at || '')}</td>
        <td style="padding:0.6rem 0.75rem;font-weight:600;">${escapeHtml(log.event_type || '')}</td>
        <td style="padding:0.6rem 0.75rem;color:var(--text);">${escapeHtml(log.details || '')}</td>
        <td style="padding:0.6rem 0.75rem;font-family:monospace;font-size:0.78rem;">${escapeHtml(log.ip_address || '—')}</td>
        <td style="padding:0.6rem 0.75rem;">
          <span style="background:${badgeBg};color:${badgeColor};font-size:0.72rem;font-weight:700;padding:0.15rem 0.45rem;border-radius:4px;">
            ${escapeHtml(log.status || 'info')}
          </span>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error('Failed to load security logs:', err);
  }
}

// ===========================================================================
// INDEXING DIAGNOSTICS & ERROR LOGGING
// ===========================================================================

async function loadIndexingErrors() {
  const tbody = $('indexingErrorsTableBody');
  if (!tbody) return;

  try {
    const res = await fetch(`/api/admin/indexing/errors?event_slug=${activeSlug}`);
    if (!res.ok) return;
    const j = await res.json();
    const errors = j.errors || [];

    tbody.replaceChildren();

    if (errors.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" style="padding:1rem;text-align:center;color:var(--muted);">No indexing errors recorded. All library files processed successfully.</td></tr>';
      return;
    }

    errors.forEach(err => {
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid var(--line)';
      tr.innerHTML = `
        <td style="padding:0.65rem 1rem;font-size:0.8rem;white-space:nowrap;color:var(--muted);">${escapeHtml(err.created_at || '')}</td>
        <td style="padding:0.65rem 1rem;font-weight:600;font-size:0.82rem;">${escapeHtml(err.photo_rel_path)}</td>
        <td style="padding:0.65rem 1rem;"><span class="badge" style="background:#fee2e2;color:#dc2626;">${escapeHtml(err.error_stage)}</span></td>
        <td style="padding:0.65rem 1rem;font-size:0.8rem;color:var(--err);">${escapeHtml(err.error_message)}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {}
}

// ===========================================================================
// SHARED ALBUMS ("My Event Album") MANAGEMENT
// ===========================================================================

let currentSharedAlbumsList = [];
let currentSharedAlbumsFilter = 'all';

async function loadAdminSharedAlbums(page = 1) {
  const tbody = $('sharedAlbumsTableBody');
  if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:2rem;color:var(--text-muted);">Loading shared albums...</td></tr>';

  try {
    const statusParam = currentSharedAlbumsFilter === 'all' ? '' : `&status=${encodeURIComponent(currentSharedAlbumsFilter)}`;
    const { ok, data } = await safeFetchJson(`/api/admin/shared-albums?page=${page}&page_size=50${statusParam}`);
    if (!ok || !data) {
      if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:2rem;color:#ef4444;">Failed to load shared albums.</td></tr>';
      return;
    }

    currentSharedAlbumsList = data.albums || [];

    // Update metrics cards
    if (data.metrics) {
      if ($('sharedMetricTotal')) $('sharedMetricTotal').textContent = data.metrics.total_albums || 0;
      if ($('sharedMetricActive')) $('sharedMetricActive').textContent = data.metrics.active_albums || 0;
      if ($('sharedMetricExpired')) $('sharedMetricExpired').textContent = data.metrics.expired_albums || 0;
      if ($('sharedMetricRevoked')) $('sharedMetricRevoked').textContent = data.metrics.revoked_albums || 0;
      if ($('sidebarSharedAlbumsCount')) $('sidebarSharedAlbumsCount').textContent = data.metrics.active_albums || 0;
    }

    // Load max photos limit setting
    const { ok: setOk, data: setData } = await safeFetchJson('/api/admin/settings');
    if (setOk && setData && setData.settings && setData.settings.max_shared_album_photos) {
      if ($('adminMaxPhotosLimitInput')) $('adminMaxPhotosLimitInput').value = setData.settings.max_shared_album_photos;
    }

    renderSharedAlbumsTable(currentSharedAlbumsList);
  } catch (err) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:2rem;color:#ef4444;">Error: ${err.message}</td></tr>`;
  }
}

function setAdminSharedFilter(filter) {
  currentSharedAlbumsFilter = filter;
  ['all', 'active', 'expired', 'revoked'].forEach(f => {
    const btn = $(`btnFilterShared${f.charAt(0).toUpperCase() + f.slice(1)}`);
    if (btn) btn.classList.toggle('active', f === filter);
  });
  loadAdminSharedAlbums(1);
}

function filterSharedAlbumsLocally() {
  const q = ($('sharedAlbumSearchInput')?.value || '').toLowerCase().trim();
  if (!q) {
    renderSharedAlbumsTable(currentSharedAlbumsList);
    return;
  }
  const filtered = currentSharedAlbumsList.filter(a =>
    (a.title || '').toLowerCase().includes(q) ||
    (a.event_name || '').toLowerCase().includes(q) ||
    String(a.id).includes(q)
  );
  renderSharedAlbumsTable(filtered);
}

function renderSharedAlbumsTable(albums) {
  const tbody = $('sharedAlbumsTableBody');
  if (!tbody) return;
  tbody.replaceChildren();

  if (!albums || albums.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:2.5rem;color:var(--text-muted);">No shared albums match the filter.</td></tr>';
    return;
  }

  albums.forEach(album => {
    const tr = document.createElement('tr');
    tr.style.cssText = 'border-bottom:1px solid var(--border-light);';

    const status = album.computed_status || (album.revoked_at ? 'revoked' : 'active');
    const statusBg = status === 'active' ? 'rgba(16,185,129,0.12)' : (status === 'expired' ? 'rgba(245,158,11,0.12)' : 'rgba(239,68,68,0.12)');
    const statusColor = status === 'active' ? '#10b981' : (status === 'expired' ? '#f59e0b' : '#ef4444');

    const createdStr = album.created_at ? new Date(album.created_at).toLocaleDateString() : '—';
    const expiresStr = album.expires_at ? new Date(album.expires_at).toLocaleDateString() : '—';

    tr.innerHTML = `
      <td style="padding:0.75rem 1rem;font-weight:700;color:var(--text-muted);">#${album.id}</td>
      <td style="padding:0.75rem 1rem;">
        <div style="font-weight:700;color:var(--text-main);">${escapeHtml(album.title || 'Untitled Album')}</div>
        <div style="font-size:0.75rem;color:var(--text-muted);margin-top:2px;">Event: ${escapeHtml(album.event_name || album.event_slug || 'Event')}</div>
      </td>
      <td style="padding:0.75rem 1rem;font-weight:600;">${album.photo_count || 0}</td>
      <td style="padding:0.75rem 1rem;">
        <span style="color:var(--text-main);font-weight:600;">${album.view_count || 0}</span> views
        <span style="color:var(--text-muted);font-size:0.75rem;margin-left:4px;">(${album.download_count || 0} dl)</span>
      </td>
      <td style="padding:0.75rem 1rem;font-size:0.8rem;color:var(--text-muted);">${createdStr}</td>
      <td style="padding:0.75rem 1rem;font-size:0.8rem;color:var(--text-muted);">${expiresStr}</td>
      <td style="padding:0.75rem 1rem;">
        <span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:0.72rem;font-weight:700;background:${statusBg};color:${statusColor};text-transform:uppercase;">
          ${status}
        </span>
      </td>
      <td style="padding:0.75rem 1rem;text-align:right;">
        <div style="display:inline-flex;gap:0.4rem;align-items:center;">
          <button class="btn-outline" style="padding:3px 8px;font-size:0.75rem;" onclick="adminInspectSharedAlbumPhotos(${album.id}, '${escapeHtml(album.title || '')}', ${album.photo_count || 0})" title="Inspect Photos">
            Photos
          </button>
          <button class="btn-outline" style="padding:3px 8px;font-size:0.75rem;" onclick="adminAdjustSharedAlbumExpiry(${album.id})" title="Adjust Expiry">
            Expiry
          </button>
          ${status === 'active' ? `
            <button class="btn-outline" style="padding:3px 8px;font-size:0.75rem;color:#ef4444;border-color:rgba(239,68,68,0.3);" onclick="adminRevokeSharedAlbum(${album.id})" title="Revoke Link">
              Revoke
            </button>
          ` : ''}
          <button class="btn-outline" style="padding:3px 8px;font-size:0.75rem;color:#ef4444;" onclick="adminDeleteSharedAlbum(${album.id})" title="Delete Record">
            Delete
          </button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function adminInspectSharedAlbumPhotos(albumId, title, photoCount) {
  if ($('sharedAlbumModalTitle')) $('sharedAlbumModalTitle').textContent = `Album: ${title || '#' + albumId}`;
  if ($('sharedAlbumModalSubtitle')) $('sharedAlbumModalSubtitle').textContent = `${photoCount} photographs in this album`;
  const grid = $('sharedAlbumPhotosGrid');
  if (grid) grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:2rem;color:var(--text-muted);">Loading photos...</div>';
  openModal('sharedAlbumPhotosModal');

  try {
    const { ok, data } = await safeFetchJson(`/api/admin/shared-albums/${albumId}/photos`);
    if (!ok || !data || !data.photos) {
      if (grid) grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:2rem;color:#ef4444;">Failed to load photos.</div>';
      return;
    }
    grid.replaceChildren();
    data.photos.forEach(p => {
      const item = document.createElement('div');
      item.style.cssText = 'background:var(--bg-card);border:1px solid var(--border-light);border-radius:6px;overflow:hidden;text-align:center;';
      const enc = (p.rel_path || '').split('/').map(encodeURIComponent).join('/');
      item.innerHTML = `
        <img src="/thumb/${enc}" loading="lazy" style="width:100%;height:110px;object-fit:cover;display:block;">
        <div style="padding:0.4rem;font-size:0.72rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
          ${escapeHtml(p.filename || '')}
        </div>
      `;
      grid.appendChild(item);
    });
  } catch (err) {
    if (grid) grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:2rem;color:#ef4444;">Error: ${err.message}</div>`;
  }
}

async function adminAdjustSharedAlbumExpiry(albumId) {
  const daysStr = prompt('Enter new duration in days from album creation (1 to 90 days):', '30');
  if (!daysStr) return;
  const days = parseInt(daysStr, 10);
  if (isNaN(days) || days < 1 || days > 90) {
    alert('Please enter a valid number of days between 1 and 90.');
    return;
  }

  const { ok, status, data } = await safeFetchJson(`/api/admin/shared-albums/${albumId}/expiry`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_expiry_days: days })
  });

  if (!ok) {
    alert('Failed to update expiry: ' + (data?.detail || status));
    return;
  }
  loadAdminSharedAlbums(1);
}

async function adminRevokeSharedAlbum(albumId) {
  if (!confirm(`Are you sure you want to revoke Shared Album #${albumId}? Anyone holding the active link will immediately lose access.`)) {
    return;
  }

  const { ok, status, data } = await safeFetchJson(`/api/admin/shared-albums/${albumId}/revoke`, {
    method: 'POST'
  });

  if (!ok) {
    alert('Failed to revoke album: ' + (data?.detail || status));
    return;
  }
  loadAdminSharedAlbums(1);
}

async function adminDeleteSharedAlbum(albumId) {
  if (!confirm(`Are you sure you want to delete Shared Album #${albumId}? This deletes the album reference and links, while strictly preserving original event photos.`)) {
    return;
  }

  const { ok, status, data } = await safeFetchJson(`/api/admin/shared-albums/${albumId}`, {
    method: 'DELETE'
  });

  if (!ok) {
    alert('Failed to delete album: ' + (data?.detail || status));
    return;
  }
  loadAdminSharedAlbums(1);
}

async function saveAdminMaxPhotosLimit() {
  const input = $('adminMaxPhotosLimitInput');
  if (!input) return;
  const val = parseInt(input.value, 10);
  if (isNaN(val) || val < 1 || val > 500) {
    alert('Max photos limit must be between 1 and 500.');
    return;
  }

  const { ok, data } = await safeFetchJson('/api/admin/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_shared_album_photos: val })
  });

  if (ok) {
    alert(`Maximum photos limit updated to ${val}.`);
  } else {
    alert('Failed to update limit: ' + (data?.detail || 'Unknown error'));
  }
}

checkAuth();


// Explicit window attachments for inline HTML handlers & external tools
if (typeof switchTab !== "undefined") window.switchTab = switchTab;
if (typeof handleAdminLogin !== "undefined") window.handleAdminLogin = handleAdminLogin;
if (typeof handle2faVerify !== "undefined") window.handle2faVerify = handle2faVerify;
if (typeof showRecoveryCodeStep !== "undefined") window.showRecoveryCodeStep = showRecoveryCodeStep;
if (typeof resetLoginSteps !== "undefined") window.resetLoginSteps = resetLoginSteps;
if (typeof handleRecoveryCodeVerify !== "undefined") window.handleRecoveryCodeVerify = handleRecoveryCodeVerify;
if (typeof show2faStep !== "undefined") window.show2faStep = show2faStep;
if (typeof closeMobileSidebar !== "undefined") window.closeMobileSidebar = closeMobileSidebar;
if (typeof openNewAlbumModal !== "undefined") window.openNewAlbumModal = openNewAlbumModal;
if (typeof toggleMobileSidebar !== "undefined") window.toggleMobileSidebar = toggleMobileSidebar;
if (typeof stopActiveTask !== "undefined") window.stopActiveTask = stopActiveTask;
if (typeof viewRunningTaskLogsFromBanner !== "undefined") window.viewRunningTaskLogsFromBanner = viewRunningTaskLogsFromBanner;
if (typeof dismissTaskCard !== "undefined") window.dismissTaskCard = dismissTaskCard;
if (typeof loadSystemMetrics !== "undefined") window.loadSystemMetrics = loadSystemMetrics;
if (typeof openAllPhotos !== "undefined") window.openAllPhotos = openAllPhotos;
if (typeof saveAdminMaxPhotosLimit !== "undefined") window.saveAdminMaxPhotosLimit = saveAdminMaxPhotosLimit;
if (typeof loadAdminSharedAlbums !== "undefined") window.loadAdminSharedAlbums = loadAdminSharedAlbums;
if (typeof setAdminSharedFilter !== "undefined") window.setAdminSharedFilter = setAdminSharedFilter;
if (typeof filterSharedAlbumsLocally !== "undefined") window.filterSharedAlbumsLocally = filterSharedAlbumsLocally;
if (typeof switchPhotoSubView !== "undefined") window.switchPhotoSubView = switchPhotoSubView;
if (typeof loadPhotos !== "undefined") window.loadPhotos = loadPhotos;
if (typeof ajaxRefreshPhotos !== "undefined") window.ajaxRefreshPhotos = ajaxRefreshPhotos;
if (typeof triggerDuplicateScan !== "undefined") window.triggerDuplicateScan = triggerDuplicateScan;
if (typeof switchPeopleSubView !== "undefined") window.switchPeopleSubView = switchPeopleSubView;
if (typeof loadMatchReviews !== "undefined") window.loadMatchReviews = loadMatchReviews;
if (typeof openCreateAutomationModal !== "undefined") window.openCreateAutomationModal = openCreateAutomationModal;
if (typeof loadAutomations !== "undefined") window.loadAutomations = loadAutomations;
if (typeof triggerCsvExport !== "undefined") window.triggerCsvExport = triggerCsvExport;
if (typeof loadIndexingErrors !== "undefined") window.loadIndexingErrors = loadIndexingErrors;
if (typeof confirmEmptyTrash !== "undefined") window.confirmEmptyTrash = confirmEmptyTrash;
if (typeof switchTrashTab !== "undefined") window.switchTrashTab = switchTrashTab;
if (typeof saveSystemSettings !== "undefined") window.saveSystemSettings = saveSystemSettings;
if (typeof loadSecuritySettings !== "undefined") window.loadSecuritySettings = loadSecuritySettings;
if (typeof loadSecurityLogs !== "undefined") window.loadSecurityLogs = loadSecurityLogs;
if (typeof handleAdminChangePassword !== "undefined") window.handleAdminChangePassword = handleAdminChangePassword;
if (typeof openTotpSetupModal !== "undefined") window.openTotpSetupModal = openTotpSetupModal;
if (typeof openDisableTotpModal !== "undefined") window.openDisableTotpModal = openDisableTotpModal;
if (typeof openRegenerateRecoveryModal !== "undefined") window.openRegenerateRecoveryModal = openRegenerateRecoveryModal;
if (typeof registerNewPasskey !== "undefined") window.registerNewPasskey = registerNewPasskey;
if (typeof revokeOtherSessions !== "undefined") window.revokeOtherSessions = revokeOtherSessions;
if (typeof closeModal !== "undefined") window.closeModal = closeModal;
if (typeof copyRecoveryCodesFromModal !== "undefined") window.copyRecoveryCodesFromModal = copyRecoveryCodesFromModal;
if (typeof downloadRecoveryCodesFromModal !== "undefined") window.downloadRecoveryCodesFromModal = downloadRecoveryCodesFromModal;
if (typeof handleConfirmTotpSetup !== "undefined") window.handleConfirmTotpSetup = handleConfirmTotpSetup;
if (typeof handleConfirmDisableTotp !== "undefined") window.handleConfirmDisableTotp = handleConfirmDisableTotp;
if (typeof handleConfirmRegenRecovery !== "undefined") window.handleConfirmRegenRecovery = handleConfirmRegenRecovery;
if (typeof submitAutomationForm !== "undefined") window.submitAutomationForm = submitAutomationForm;
if (typeof openAlbumPhotos !== "undefined") window.openAlbumPhotos = openAlbumPhotos;
if (typeof openEditAlbumModal !== "undefined") window.openEditAlbumModal = openEditAlbumModal;
if (typeof promptDeleteAlbum !== "undefined") window.promptDeleteAlbum = promptDeleteAlbum;
if (typeof openHdLightbox !== "undefined") window.openHdLightbox = openHdLightbox;
if (typeof toggleFeaturedPhoto !== "undefined") window.toggleFeaturedPhoto = toggleFeaturedPhoto;
if (typeof setAsCover !== "undefined") window.setAsCover = setAsCover;
if (typeof openRenamePersonModal !== "undefined") window.openRenamePersonModal = openRenamePersonModal;
if (typeof viewPersonPhotos !== "undefined") window.viewPersonPhotos = viewPersonPhotos;
if (typeof runAutomationNow !== "undefined") window.runAutomationNow = runAutomationNow;
if (typeof toggleAutomationStatus !== "undefined") window.toggleAutomationStatus = toggleAutomationStatus;
if (typeof openEditAutomationModal !== "undefined") window.openEditAutomationModal = openEditAutomationModal;
if (typeof viewAutomationHistoryModal !== "undefined") window.viewAutomationHistoryModal = viewAutomationHistoryModal;
if (typeof deleteAutomationAction !== "undefined") window.deleteAutomationAction = deleteAutomationAction;
if (typeof loadUploadQueue !== "undefined") window.loadUploadQueue = loadUploadQueue;
if (typeof viewQueueTaskDetails !== "undefined") window.viewQueueTaskDetails = viewQueueTaskDetails;
if (typeof cancelQueueTaskAction !== "undefined") window.cancelQueueTaskAction = cancelQueueTaskAction;
if (typeof retryQueueTaskAction !== "undefined") window.retryQueueTaskAction = retryQueueTaskAction;
if (typeof closeInlineTaskLogs !== "undefined") window.closeInlineTaskLogs = closeInlineTaskLogs;
if (typeof toggleTaskLogs !== "undefined") window.toggleTaskLogs = toggleTaskLogs;
if (typeof restoreTrashItem !== "undefined") window.restoreTrashItem = restoreTrashItem;
if (typeof permanentDeleteTrashItem !== "undefined") window.permanentDeleteTrashItem = permanentDeleteTrashItem;
if (typeof ignoreDuplicateGroup !== "undefined") window.ignoreDuplicateGroup = ignoreDuplicateGroup;
if (typeof resolveSelectedDuplicates !== "undefined") window.resolveSelectedDuplicates = resolveSelectedDuplicates;
if (typeof submitMatchFeedback !== "undefined") window.submitMatchFeedback = submitMatchFeedback;
if (typeof renamePasskey !== "undefined") window.renamePasskey = renamePasskey;
if (typeof deletePasskey !== "undefined") window.deletePasskey = deletePasskey;
if (typeof revokeSession !== "undefined") window.revokeSession = revokeSession;
if (typeof adminInspectSharedAlbumPhotos !== "undefined") window.adminInspectSharedAlbumPhotos = adminInspectSharedAlbumPhotos;
if (typeof adminAdjustSharedAlbumExpiry !== "undefined") window.adminAdjustSharedAlbumExpiry = adminAdjustSharedAlbumExpiry;
if (typeof adminRevokeSharedAlbum !== "undefined") window.adminRevokeSharedAlbum = adminRevokeSharedAlbum;
if (typeof adminDeleteSharedAlbum !== "undefined") window.adminDeleteSharedAlbum = adminDeleteSharedAlbum;
if (typeof checkAuth !== "undefined") window.checkAuth = checkAuth;
if (typeof openModal !== "undefined") window.openModal = openModal;
