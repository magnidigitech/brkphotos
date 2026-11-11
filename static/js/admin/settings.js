// BRK Photos Admin - System Settings & Diagnostics (ES Module)
import { adminState, $, escapeHtml, formatToIST } from './state.js?v=2.2.0';
import { safeFetchJson } from './api.js?v=2.2.0';
import { loadSecurityLogs, loadSecuritySettings } from './auth.js?v=2.2.0';

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


export {
  loadSystemSettings,
  saveSystemSettings,
  loadIndexingErrors
};
