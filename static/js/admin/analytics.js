// BRK Photos Admin - Analytics & Native SVG Charts (ES Module)
import { adminState, $, escapeHtml, formatToIST, formatTimeShort } from './state.js?v=2.2.0';
import { safeFetchJson } from './api.js?v=2.2.0';

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


export {
  initAnalyticsEventListeners,
  triggerCsvExport,
  loadAnalytics,
  populateDashboardMetrics,
  populateSystemMetrics,
  renderCurrentSubTabCharts,
  renderDailyTrafficChart,
  renderSearchOutcomeChart,
  renderHourlyActivityChart
};
