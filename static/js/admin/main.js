// BRK Photos Admin - Main Entry Point & Router (ES Module)
import { adminState, $, $$, escapeHtml, formatDateDDMMYYYY, formatDateYYYYMMDD, parseAlbumDate, parseDateUtcOrLocal, formatToIST, formatTimeShort, generateSlug, MONTH_NAMES } from './state.js?v=2.2.0';
import { originalFetch, safeFetchJson, openModal, closeModal, openDeleteConfirmModal } from './api.js?v=2.2.0';
import {
  checkAuth, handleAdminLogin, handle2faVerify, handleRecoveryCodeVerify, handlePasskeyLogin,
  resetLoginSteps, show2faStep, showRecoveryCodeStep, handleLogout,
  loadSecuritySettings, renderPasskeysList, renderSessionsList, handleAdminChangePassword,
  openTotpSetupModal, copyRecoveryCodesFromModal, downloadRecoveryCodesFromModal, handleConfirmTotpSetup,
  openDisableTotpModal, handleConfirmDisableTotp, openRegenerateRecoveryModal, handleConfirmRegenRecovery,
  registerNewPasskey, renamePasskey, deletePasskey, revokeSession, revokeOtherSessions, loadSecurityLogs
} from './auth.js?v=2.2.0';
import {
  openMobileSidebar, closeMobileSidebar, toggleMobileSidebar,
  populateEventSelector, updateActiveEventButtons, setEventWorkspace,
  renderSidebarSubfolders, renderUploadTargets, initSidebarCollapsibles,
  loadDashboard, loadSystemMetrics, loadStorage, loadLogs,
  dismissTaskCard, pollTaskStatus, stopActiveTask, viewRunningTaskLogsFromBanner,
  toggleTaskLogs, closeInlineTaskLogs, openInlineTaskLogs, renderTaskRowInnerHtml
} from './dashboard.js?v=2.2.0';
import {
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
} from './events-photos.js?v=2.2.0';
import {
  selectUploadTargetAlbum, setupUploadTab, submitGdriveTask,
  loadAutomations, renderAutomationsTable, openCreateAutomationModal,
  openEditAutomationModal, submitAutomationForm, runAutomationNow,
  toggleAutomationStatus, deleteAutomationAction, viewAutomationHistoryModal,
  loadUploadQueue, renderUploadQueueUI, viewQueueTaskDetails,
  cancelQueueTaskAction, retryQueueTaskAction, loadTaskHistory
} from './uploads.js?v=2.2.0';
import {
  initAnalyticsEventListeners, triggerCsvExport, loadAnalytics,
  populateDashboardMetrics, populateSystemMetrics, renderCurrentSubTabCharts,
  renderDailyTrafficChart, renderSearchOutcomeChart, renderHourlyActivityChart
} from './analytics.js?v=2.2.0';
import {
  loadSystemSettings, saveSystemSettings, loadIndexingErrors
} from './settings.js?v=2.2.0';

// Tab Router
export function switchTab(tabName) {
  closeMobileSidebar();
  const sidebarTab = tabName === 'photos' ? 'albums' : tabName;
  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.getAttribute('data-tab') === sidebarTab);
  });

  document.querySelectorAll('main.workspace-canvas section').forEach(sec => sec.hidden = true);
  const target = $(`panel-${tabName}`);
  if (target) target.hidden = false;

  localStorage.setItem('admin_active_tab', tabName);
  try {
    const url = new URL(window.location);
    url.searchParams.set('event', adminState.activeSlug);
    url.searchParams.set('tab', tabName);
    window.history.replaceState({}, '', url);
  } catch(e) {}

  if (tabName === 'dashboard') loadDashboard();
  if (tabName === 'albums') loadAlbums();
  if (tabName === 'photos') {
    loadPhotos(adminState.activeFilterAlbumId);
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

// Setup Event Listeners & UI Bindings
function initEventListeners() {
  document.querySelectorAll('.sidebar-nav-list > .nav-item').forEach(el => {
    el.addEventListener('click', () => {
      closeMobileSidebar();
      const tab = el.getAttribute('data-tab');
      if (tab === 'photos') adminState.activeFilterAlbumId = '';
      switchTab(tab);
    });
  });

  // Logout listener
  $('logoutBtn')?.addEventListener('click', handleLogout);

  // Keyboard shortcut for escaping sidebar / modals
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      closeMobileSidebar();
    }
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth > 992) {
      closeMobileSidebar();
    }
  });
}

initEventListeners();
checkAuth();

// Window compatibility bridge for inline HTML handlers & automated test harnesses
Object.assign(window, {
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
});
