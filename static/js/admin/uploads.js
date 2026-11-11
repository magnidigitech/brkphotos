// BRK Photos Admin - Uploads, Google Drive & Automations (ES Module)
import { adminState, $, escapeHtml, formatToIST, formatTimeShort, parseDateUtcOrLocal } from './state.js?v=2.2.0';
import { safeFetchJson, openModal, closeModal } from './api.js?v=2.2.0';
import { pollTaskStatus, openInlineTaskLogs, renderTaskRowInnerHtml } from './dashboard.js?v=2.2.0';

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


export {
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
};
