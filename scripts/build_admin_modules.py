#!/usr/bin/env python3
import os
import re
import subprocess

orig_code = subprocess.check_output(['git', 'show', 'HEAD:static/js/admin/main.js']).decode('utf-8')
lines = orig_code.split('\n')

def get_lines(start_1indexed, end_1indexed):
    return '\n'.join(lines[start_1indexed - 1 : end_1indexed])

# 1. state.js
state_js = f"""// BRK Photos Admin - State & Utility Store (ES Module)
export const adminState = {{
  get storedSlug() {{
    let s = localStorage.getItem('admin_active_event_slug');
    if (!s || ['wedding', 'anilkumar-vengamamba', 'default'].includes(s)) {{
      s = 'brk-sir';
      localStorage.setItem('admin_active_event_slug', 'brk-sir');
    }}
    return s;
  }},
  set storedSlug(v) {{
    localStorage.setItem('admin_active_event_slug', v);
  }},
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
}};

// Initialize activeSlug from stored
adminState.activeSlug = adminState.storedSlug;

// Shared state reactive binding across ES modules
const sharedKeys = [
  'activeSlug', 'allAlbums', 'allEvents', 'currentEvent',
  'selectedPhotoIds', 'activeFilterAlbumId', 'activeUploadAlbumSlug',
  'lastHistoryRefreshTime', 'lastSidebarAutoSyncTime', 'lastQueueRefreshTime',
  'lastAutoPhotoRefreshTime', 'hasActiveQueueTasks'
];

sharedKeys.forEach(key => {{
  Object.defineProperty(globalThis, key, {{
    get: () => adminState[key],
    set: (v) => {{ adminState[key] = v; }},
    configurable: true
  }});
}});

export const $ = id => document.getElementById(id);
export const $$ = sel => document.querySelectorAll(sel);

{get_lines(161, 185)}

{get_lines(783, 862)}

{get_lines(1088, 1100)}

{get_lines(2233, 2276)}

export {{
  escapeHtml,
  formatDateDDMMYYYY,
  formatDateYYYYMMDD,
  parseAlbumDate,
  parseDateUtcOrLocal,
  formatToIST,
  formatTimeShort,
  generateSlug,
  MONTH_NAMES
}};
"""

# 2. api.js
api_js = f"""// BRK Photos Admin - API & Modal Helpers (ES Module)
import {{ $ }} from './state.js?v=2.2.0';
import {{ closeMobileSidebar }} from './dashboard.js?v=2.2.0';

{get_lines(4, 34)}

{get_lines(155, 160)}

{get_lines(200, 255)}

{get_lines(375, 431)}

export {{
  originalFetch,
  safeFetchJson,
  openModal,
  closeModal,
  openDeleteConfirmModal
}};
"""

# 3. auth.js
auth_js = f"""// BRK Photos Admin - Authentication & Security Controller (ES Module)
import {{ adminState, $, escapeHtml, parseDateUtcOrLocal, formatToIST }} from './state.js?v=2.2.0';
import {{ safeFetchJson, openModal, closeModal }} from './api.js?v=2.2.0';
import {{ populateEventSelector, pollTaskStatus, initSidebarCollapsibles, setEventWorkspace }} from './dashboard.js?v=2.2.0';
import {{ setupAlbumSearch }} from './events-photos.js?v=2.2.0';

{get_lines(282, 327)}

{get_lines(328, 374)}

{get_lines(430, 715)}

async function handleLogout() {{
  localStorage.removeItem('admin_token');
  localStorage.removeItem('admin_active_tab');
  try {{
    await fetch('/api/admin/logout', {{ method: 'POST' }});
  }} catch (e) {{}}
  $('appShell').hidden = true;
  $('authView').hidden = false;
  window.location.href = '/admin';
}}

{get_lines(4128, 4705)}

export {{
  bufferToBase64url,
  base64urlToBuffer,
  resetLoginSteps,
  show2faStep,
  showRecoveryCodeStep,
  handleAdminLogin,
  handle2faVerify,
  handleRecoveryCodeVerify,
  handlePasskeyLogin,
  checkAuth,
  handleLogout,
  loadSecuritySettings,
  renderPasskeysList,
  renderSessionsList,
  handleAdminChangePassword,
  openTotpSetupModal,
  copyRecoveryCodesFromModal,
  downloadRecoveryCodesFromModal,
  handleConfirmTotpSetup,
  openDisableTotpModal,
  handleConfirmDisableTotp,
  openRegenerateRecoveryModal,
  handleConfirmRegenRecovery,
  registerNewPasskey,
  renamePasskey,
  deletePasskey,
  revokeSession,
  revokeOtherSessions,
  loadSecurityLogs
}};
"""

# 4. dashboard.js
dashboard_js = f"""// BRK Photos Admin - Dashboard, Sidebar & Task Monitor (ES Module)
import {{ adminState, $, escapeHtml, formatToIST, formatTimeShort, parseAlbumDate, formatDateDDMMYYYY, formatDateYYYYMMDD, generateSlug }} from './state.js?v=2.2.0';
import {{ safeFetchJson, openModal, closeModal }} from './api.js?v=2.2.0';
import {{ loadAlbums, loadPhotos, openAlbumPhotos, openNewAlbumModal }} from './events-photos.js?v=2.2.0';
import {{ loadTaskHistory, loadUploadQueue, selectUploadTargetAlbum, setupUploadTab }} from './uploads.js?v=2.2.0';
import {{ switchTab }} from './main.js?v=2.2.0';

// Sidebar Mobile Off-canvas Navigation Controls
{get_lines(54, 95)}

// Event Selector & Workspace Management
{get_lines(730, 782)}

{get_lines(863, 988)}

// Dashboard & System Metrics
{get_lines(1040, 1087)}

// Storage Metrics & Logs Viewers
{get_lines(2532, 2549)}

{get_lines(3054, 3281)}

{get_lines(3282, 3413)}

{get_lines(3503, 3578)}

export {{
  openMobileSidebar,
  closeMobileSidebar,
  toggleMobileSidebar,
  populateEventSelector,
  updateActiveEventButtons,
  setEventWorkspace,
  renderSidebarSubfolders,
  renderUploadTargets,
  initSidebarCollapsibles,
  loadDashboard,
  loadSystemMetrics,
  loadStorage,
  loadLogs,
  dismissTaskCard,
  pollTaskStatus,
  stopActiveTask,
  viewRunningTaskLogsFromBanner,
  toggleTaskLogs,
  closeInlineTaskLogs,
  openInlineTaskLogs,
  renderTaskRowInnerHtml
}};
"""

# 5. events-photos.js
events_photos_js = f"""// BRK Photos Admin - Events, Photos, Duplicates, People & Shared Albums (ES Module)
import {{ adminState, $, escapeHtml, formatDateDDMMYYYY, formatDateYYYYMMDD, parseAlbumDate, parseDateUtcOrLocal, formatToIST, formatTimeShort, generateSlug }} from './state.js?v=2.2.0';
import {{ safeFetchJson, openModal, closeModal, openDeleteConfirmModal }} from './api.js?v=2.2.0';
import {{ checkAuth }} from './auth.js?v=2.2.0';
import {{ closeMobileSidebar, pollTaskStatus, renderSidebarSubfolders, setEventWorkspace }} from './dashboard.js?v=2.2.0';
import {{ loadTaskHistory }} from './uploads.js?v=2.2.0';
import {{ switchTab }} from './main.js?v=2.2.0';

{get_lines(137, 144)}

{get_lines(186, 199)}

{get_lines(255, 281)}

{get_lines(989, 998)}

{get_lines(1099, 1410)}

{get_lines(1411, 1831)}

{get_lines(3579, 3771)}

{get_lines(3772, 3945)}

{get_lines(3946, 4069)}

{get_lines(4740, 4969)}

export {{
  setupAlbumSearch,
  renderAlbumSearchResults,
  loadAlbums,
  openNewAlbumModal,
  openEditAlbumModal,
  promptDeleteAlbum,
  loadPhotos,
  ajaxRefreshPhotos,
  openAllPhotos,
  openAlbumPhotos,
  openHdLightbox,
  toggleFeaturedPhoto,
  updateBulkToolbar,
  runBulkAction,
  setAsCover,
  loadPeople,
  openRenamePersonModal,
  viewPersonPhotos,
  switchPeopleSubView,
  loadMatchReviews,
  submitMatchFeedback,
  switchPhotoSubView,
  loadDuplicatesBadge,
  loadDuplicates,
  triggerDuplicateScan,
  ignoreDuplicateGroup,
  resolveSelectedDuplicates,
  switchTrashTab,
  loadTrash,
  renderTrashView,
  restoreTrashItem,
  permanentDeleteTrashItem,
  confirmEmptyTrash,
  loadAdminSharedAlbums,
  setAdminSharedFilter,
  filterSharedAlbumsLocally,
  renderSharedAlbumsTable,
  adminInspectSharedAlbumPhotos,
  adminAdjustSharedAlbumExpiry,
  adminRevokeSharedAlbum,
  adminDeleteSharedAlbum,
  saveAdminMaxPhotosLimit
}};
"""

# 6. uploads.js
uploads_js = f"""// BRK Photos Admin - Uploads, Google Drive & Automations (ES Module)
import {{ adminState, $, escapeHtml, formatToIST, formatTimeShort, parseDateUtcOrLocal }} from './state.js?v=2.2.0';
import {{ safeFetchJson, openModal, closeModal }} from './api.js?v=2.2.0';
import {{ pollTaskStatus, openInlineTaskLogs, renderTaskRowInnerHtml }} from './dashboard.js?v=2.2.0';

{get_lines(999, 1039)}

{get_lines(1832, 2232)}

{get_lines(2274, 2531)}

{get_lines(3414, 3502)}

export {{
  selectUploadTargetAlbum,
  setupUploadTab,
  submitGdriveTask,
  loadAutomations,
  renderAutomationsTable,
  openCreateAutomationModal,
  openEditAutomationModal,
  submitAutomationForm,
  runAutomationNow,
  toggleAutomationStatus,
  deleteAutomationAction,
  viewAutomationHistoryModal,
  loadUploadQueue,
  renderUploadQueueUI,
  viewQueueTaskDetails,
  cancelQueueTaskAction,
  retryQueueTaskAction,
  loadTaskHistory
}};
"""

# 7. analytics.js
analytics_js = f"""// BRK Photos Admin - Analytics & Native SVG Charts (ES Module)
import {{ adminState, $, escapeHtml, formatToIST, formatTimeShort }} from './state.js?v=2.2.0';
import {{ safeFetchJson }} from './api.js?v=2.2.0';

{get_lines(2541, 3057)}

export {{
  initAnalyticsEventListeners,
  triggerCsvExport,
  loadAnalytics,
  populateDashboardMetrics,
  populateSystemMetrics,
  renderCurrentSubTabCharts,
  renderDailyTrafficChart,
  renderSearchOutcomeChart,
  renderHourlyActivityChart
}};
"""

# 8. settings.js
settings_js = f"""// BRK Photos Admin - System Settings & Diagnostics (ES Module)
import {{ adminState, $, escapeHtml, formatToIST }} from './state.js?v=2.2.0';
import {{ safeFetchJson }} from './api.js?v=2.2.0';
import {{ loadSecurityLogs, loadSecuritySettings }} from './auth.js?v=2.2.0';

{get_lines(4070, 4130)}

{get_lines(4706, 4743)}

export {{
  loadSystemSettings,
  saveSystemSettings,
  loadIndexingErrors
}};
"""

# 9. main.js
main_js = f"""// BRK Photos Admin - Main Entry Point & Router (ES Module)
import {{ adminState, $, $$, escapeHtml, formatDateDDMMYYYY, formatDateYYYYMMDD, parseAlbumDate, parseDateUtcOrLocal, formatToIST, formatTimeShort, generateSlug, MONTH_NAMES }} from './state.js?v=2.2.0';
import {{ originalFetch, safeFetchJson, openModal, closeModal, openDeleteConfirmModal }} from './api.js?v=2.2.0';
import {{
  checkAuth, handleAdminLogin, handle2faVerify, handleRecoveryCodeVerify, handlePasskeyLogin,
  resetLoginSteps, show2faStep, showRecoveryCodeStep, handleLogout,
  loadSecuritySettings, renderPasskeysList, renderSessionsList, handleAdminChangePassword,
  openTotpSetupModal, copyRecoveryCodesFromModal, downloadRecoveryCodesFromModal, handleConfirmTotpSetup,
  openDisableTotpModal, handleConfirmDisableTotp, openRegenerateRecoveryModal, handleConfirmRegenRecovery,
  registerNewPasskey, renamePasskey, deletePasskey, revokeSession, revokeOtherSessions, loadSecurityLogs
}} from './auth.js?v=2.2.0';
import {{
  openMobileSidebar, closeMobileSidebar, toggleMobileSidebar,
  populateEventSelector, updateActiveEventButtons, setEventWorkspace,
  renderSidebarSubfolders, renderUploadTargets, initSidebarCollapsibles,
  loadDashboard, loadSystemMetrics, loadStorage, loadLogs,
  dismissTaskCard, pollTaskStatus, stopActiveTask, viewRunningTaskLogsFromBanner,
  toggleTaskLogs, closeInlineTaskLogs, openInlineTaskLogs, renderTaskRowInnerHtml
}} from './dashboard.js?v=2.2.0';
import {{
  setupAlbumSearch, renderAlbumSearchResults, loadAlbums, openNewAlbumModal,
  openEditAlbumModal, promptDeleteAlbum, loadPhotos, ajaxRefreshPhotos, openAllPhotos,
  openAlbumPhotos, openHdLightbox, toggleFeaturedPhoto, updateBulkToolbar, runBulkAction,
  setAsCover, loadPeople, openRenamePersonModal, viewPersonPhotos, switchPeopleSubView,
  loadMatchReviews, submitMatchFeedback, switchPhotoSubView, loadDuplicatesBadge,
  loadDuplicates, triggerDuplicateScan, ignoreDuplicateGroup, resolveSelectedDuplicates,
  switchTrashTab, loadTrash, renderTrashView, restoreTrashItem, permanentDeleteTrashItem,
  confirmEmptyTrash, loadAdminSharedAlbums, setAdminSharedFilter, filterSharedAlbumsLocally,
  renderSharedAlbumsTable, adminInspectSharedAlbumPhotos, adminAdjustSharedAlbumExpiry,
  adminRevokeSharedAlbum, adminDeleteSharedAlbum, saveAdminMaxPhotosLimit
}} from './events-photos.js?v=2.2.0';
import {{
  selectUploadTargetAlbum, setupUploadTab, submitGdriveTask,
  loadAutomations, renderAutomationsTable, openCreateAutomationModal,
  openEditAutomationModal, submitAutomationForm, runAutomationNow,
  toggleAutomationStatus, deleteAutomationAction, viewAutomationHistoryModal,
  loadUploadQueue, renderUploadQueueUI, viewQueueTaskDetails,
  cancelQueueTaskAction, retryQueueTaskAction, loadTaskHistory
}} from './uploads.js?v=2.2.0';
import {{
  initAnalyticsEventListeners, triggerCsvExport, loadAnalytics,
  populateDashboardMetrics, populateSystemMetrics, renderCurrentSubTabCharts,
  renderDailyTrafficChart, renderSearchOutcomeChart, renderHourlyActivityChart
}} from './analytics.js?v=2.2.0';
import {{
  loadSystemSettings, saveSystemSettings, loadIndexingErrors
}} from './settings.js?v=2.2.0';

// Tab Router
export function switchTab(tabName) {{
  closeMobileSidebar();
  const sidebarTab = tabName === 'photos' ? 'albums' : tabName;
  document.querySelectorAll('.nav-item').forEach(item => {{
    item.classList.toggle('active', item.getAttribute('data-tab') === sidebarTab);
  }});

  document.querySelectorAll('main.workspace-canvas section').forEach(sec => sec.hidden = true);
  const target = $(`panel-${{tabName}}`);
  if (target) target.hidden = false;

  localStorage.setItem('admin_active_tab', tabName);
  try {{
    const url = new URL(window.location);
    url.searchParams.set('event', adminState.activeSlug);
    url.searchParams.set('tab', tabName);
    window.history.replaceState({{}}, '', url);
  }} catch(e) {{}}

  if (tabName === 'dashboard') loadDashboard();
  if (tabName === 'albums') loadAlbums();
  if (tabName === 'photos') {{
    loadPhotos(adminState.activeFilterAlbumId);
    loadDuplicatesBadge();
  }}
  if (tabName === 'people') loadPeople();
  if (tabName === 'upload') setupUploadTab();
  if (tabName === 'storage') loadStorage();
  if (tabName === 'analytics') loadAnalytics();
  if (tabName === 'logs') {{
    loadLogs();
    loadIndexingErrors();
  }}
  if (tabName === 'trash') loadTrash();
  if (tabName === 'settings') loadSystemSettings();
  if (tabName === 'shared-albums') loadAdminSharedAlbums();
}}

// Setup Event Listeners & UI Bindings
function initEventListeners() {{
  document.querySelectorAll('.sidebar-nav-list > .nav-item').forEach(el => {{
    el.addEventListener('click', () => {{
      closeMobileSidebar();
      const tab = el.getAttribute('data-tab');
      if (tab === 'photos') adminState.activeFilterAlbumId = '';
      switchTab(tab);
    }});
  }});

  // Logout listener
  $('logoutBtn')?.addEventListener('click', handleLogout);

  // Keyboard shortcut for escaping sidebar / modals
  document.addEventListener('keydown', e => {{
    if (e.key === 'Escape') {{
      closeMobileSidebar();
    }}
  }});

  window.addEventListener('resize', () => {{
    if (window.innerWidth > 992) {{
      closeMobileSidebar();
    }}
  }});
}}

initEventListeners();
checkAuth();

// Window compatibility bridge for inline HTML handlers & automated test harnesses
Object.assign(window, {{
  switchTab,
  handleAdminLogin,
  handle2faVerify,
  showRecoveryCodeStep,
  resetLoginSteps,
  handleRecoveryCodeVerify,
  show2faStep,
  closeMobileSidebar,
  openNewAlbumModal,
  toggleMobileSidebar,
  stopActiveTask,
  viewRunningTaskLogsFromBanner,
  dismissTaskCard,
  loadSystemMetrics,
  openAllPhotos,
  saveAdminMaxPhotosLimit,
  loadAdminSharedAlbums,
  setAdminSharedFilter,
  filterSharedAlbumsLocally,
  switchPhotoSubView,
  loadPhotos,
  ajaxRefreshPhotos,
  triggerDuplicateScan,
  switchPeopleSubView,
  loadMatchReviews,
  openCreateAutomationModal,
  loadAutomations,
  triggerCsvExport,
  loadIndexingErrors,
  confirmEmptyTrash,
  switchTrashTab,
  saveSystemSettings,
  loadSecuritySettings,
  loadSecurityLogs,
  handleAdminChangePassword,
  openTotpSetupModal,
  openDisableTotpModal,
  openRegenerateRecoveryModal,
  registerNewPasskey,
  revokeOtherSessions,
  closeModal,
  copyRecoveryCodesFromModal,
  downloadRecoveryCodesFromModal,
  handleConfirmTotpSetup,
  handleConfirmDisableTotp,
  handleConfirmRegenRecovery,
  submitAutomationForm,
  openAlbumPhotos,
  openEditAlbumModal,
  promptDeleteAlbum,
  openHdLightbox,
  toggleFeaturedPhoto,
  setAsCover,
  openRenamePersonModal,
  viewPersonPhotos,
  runAutomationNow,
  toggleAutomationStatus,
  openEditAutomationModal,
  viewAutomationHistoryModal,
  deleteAutomationAction,
  loadUploadQueue,
  viewQueueTaskDetails,
  cancelQueueTaskAction,
  retryQueueTaskAction,
  closeInlineTaskLogs,
  toggleTaskLogs,
  restoreTrashItem,
  permanentDeleteTrashItem,
  ignoreDuplicateGroup,
  resolveSelectedDuplicates,
  submitMatchFeedback,
  renamePasskey,
  deletePasskey,
  revokeSession,
  adminInspectSharedAlbumPhotos,
  adminAdjustSharedAlbumExpiry,
  adminRevokeSharedAlbum,
  adminDeleteSharedAlbum,
  checkAuth,
  openModal
}});
"""

files = {
    'static/js/admin/state.js': state_js,
    'static/js/admin/api.js': api_js,
    'static/js/admin/auth.js': auth_js,
    'static/js/admin/dashboard.js': dashboard_js,
    'static/js/admin/events-photos.js': events_photos_js,
    'static/js/admin/uploads.js': uploads_js,
    'static/js/admin/analytics.js': analytics_js,
    'static/js/admin/settings.js': settings_js,
    'static/js/admin/main.js': main_js,
}

for path, content in files.items():
    with open(path, 'w') as out_f:
        out_f.write(content)
    print(f"Wrote {path} ({len(content.splitlines())} lines)")

print("Admin JavaScript modularization completed successfully!")
