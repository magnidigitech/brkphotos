// BRK Photos Admin - Events, Photos, Duplicates, People & Shared Albums (ES Module)
import { adminState, $, escapeHtml, formatDateDDMMYYYY, formatDateYYYYMMDD, parseAlbumDate, parseDateUtcOrLocal, formatToIST, formatTimeShort, generateSlug } from './state.js?v=2.2.0';
import { safeFetchJson, openModal, closeModal, openDeleteConfirmModal } from './api.js?v=2.2.0';
import { checkAuth } from './auth.js?v=2.2.0';
import { closeMobileSidebar, pollTaskStatus, renderSidebarSubfolders, setEventWorkspace } from './dashboard.js?v=2.2.0';
import { loadTaskHistory } from './uploads.js?v=2.2.0';
import { switchTab } from './main.js?v=2.2.0';

function openAllPhotos() {
  closeMobileSidebar();
  activeFilterAlbumId = '';
  const filterSelect = $('filterAlbumSelect');
  if (filterSelect) filterSelect.value = '';
  switchTab('photos');
  loadPhotos('', false);
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

function openAlbumPhotos(albumId, albumName) {
  activeFilterAlbumId = albumId;
  switchTab('photos');
  
  // Highlight active subfolder in sidebar
  document.querySelectorAll('.subfolder-item').forEach(el => el.classList.remove('active'));
  const activeLi = $(`subfolder-item-${albumId}`);
  if (activeLi) activeLi.classList.add('active');
}


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

export {
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
};
