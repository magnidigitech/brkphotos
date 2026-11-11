// BRK Photos Admin - Dashboard, Sidebar & Task Monitor (ES Module)
import { adminState, $, escapeHtml, formatToIST, formatTimeShort, parseAlbumDate, formatDateDDMMYYYY, formatDateYYYYMMDD, generateSlug } from './state.js?v=2.2.0';
import { safeFetchJson, openModal, closeModal } from './api.js?v=2.2.0';
import { loadAlbums, loadPhotos, openAlbumPhotos, openNewAlbumModal } from './events-photos.js?v=2.2.0';
import { loadTaskHistory, loadUploadQueue, selectUploadTargetAlbum, setupUploadTab } from './uploads.js?v=2.2.0';
import { switchTab } from './main.js?v=2.2.0';

// Sidebar Mobile Off-canvas Navigation Controls
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


// Dashboard & System Metrics
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


// Storage Metrics & Logs Viewers
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


export {
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
};
