// Gallery grid rendering, date section groups, lazy batching, and infinite pagination
import { $, state, normalizeBaseFilename, parsePhotoDate, getSessionId, getVisitorId } from './state.js';
import { updateMobileNavigationState } from './navigation.js';
import { toggleSelectionMode, togglePhotoSelect, clearSelection, updateBulkBar } from './selection.js';
import { updateCalendarAndFilters, applyPhotoFiltersAndSort, setFilterCallbacks } from './filters.js';

let _openLightboxCallback = null;
let _updateLightboxCallback = null;

export function setGalleryLightboxCallbacks({ openLightbox, updateLightbox }) {
  if (openLightbox) _openLightboxCallback = openLightbox;
  if (updateLightbox) _updateLightboxCallback = updateLightbox;
}

// Wire filter callbacks so filters can re-render grid
setFilterCallbacks({
  renderGrid: renderPhotosGridData,
  updateProgress: updateProgressText
});

export async function loadEvent() {
  try {
    let res = await fetch('/api/public/gallery');
    if (!res.ok) res = await fetch(`/api/public/events/${state.slug}`);
    if (res.ok) {
      state.eventData = await res.json();
      if (state.eventData && state.eventData.event && state.eventData.event.name) {
        document.title = `${state.eventData.event.name} | Bhashyam Ramakrishna Garu, MP Rajya Sabha`;
      }
    }
  } catch (err) {
    document.title = 'Photo with BRK Sir | Bhashyam Ramakrishna Garu, MP Rajya Sabha';
  }
}

export function updateProgressText() {
  const el = $('photosProgressText');
  const mobileEl = $('mobilePhotosProgressText');
  const total = (state.totalMatchingPhotosCount > 0) ? state.totalMatchingPhotosCount : state.rawPhotosList.length;
  const loaded = Math.min(state.gridQueueCursor, state.currentPhotosList.length);
  const text = `Showing ${loaded} of ${total} photos`;
  if (el) el.textContent = text;
  if (mobileEl) mobileEl.textContent = text;
}

export function createPhotoCard(item, idx) {
  const p = typeof item === 'string' ? item : item.rel_path;
  const enc = p.split('/').map(encodeURIComponent).join('/');
  const filename = p.split('/').pop();
  const isFeat = typeof item === 'object' && item.is_featured;

  const card = document.createElement('div');
  card.className = 'photo-card' + (state.isSelectionMode ? ' selection-active' : '') + (state.selectedPhotos.has(p) ? ' selected' : '');
  card.dataset.photoPath = p;
  card.setAttribute('draggable', 'false');
  card.draggable = false;
  card.addEventListener('contextmenu', (e) => e.preventDefault());
  card.addEventListener('dragstart', (e) => e.preventDefault());

  // Checkbox indicator for selection mode
  const chk = document.createElement('div');
  chk.className = 'photo-select-checkbox';
  chk.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
  chk.style.display = state.isSelectionMode ? 'flex' : 'none';
  card.appendChild(chk);

  if (isFeat) {
    const badge = document.createElement('span');
    badge.className = 'photo-featured-badge';
    badge.textContent = '★ Featured';
    card.appendChild(badge);
  }

  const wrap = document.createElement('div');
  wrap.className = 'photo-img-wrap';

  const img = document.createElement('img');
  img.className = 'photo-img';
  img.alt = filename;
  img.loading = idx < 12 ? 'eager' : 'lazy';
  img.decoding = 'async';
  img.setAttribute('draggable', 'false');
  img.draggable = false;
  img.addEventListener('contextmenu', (e) => e.preventDefault());
  img.addEventListener('dragstart', (e) => e.preventDefault());

  const onLoad = () => {
    wrap.classList.add('loaded');
    img.classList.add('loaded');
  };

  const onError = () => {
    wrap.classList.add('loaded');
    wrap.classList.add('error');
    wrap.style.display = 'flex';
    wrap.style.alignItems = 'center';
    wrap.style.justifyContent = 'center';
    wrap.style.minHeight = '180px';
    wrap.style.background = 'var(--card)';
    wrap.innerHTML = `<div style="color:var(--muted);font-size:0.75rem;text-align:center;padding:1rem;">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin:0 auto 6px;display:block;opacity:0.5;"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>
      <span>Image unavailable</span>
    </div>`;
  };

  img.addEventListener('load', onLoad, { once: true });
  img.addEventListener('error', onError, { once: true });
  img.src = `/thumb/${enc}`;

  if (img.complete && img.naturalWidth > 0) {
    onLoad();
  }

  wrap.appendChild(img);
  card.appendChild(wrap);

  // Long press detection on mobile to trigger selection mode:
  let pressTimer = null;
  card.addEventListener('touchstart', (e) => {
    pressTimer = setTimeout(() => {
      if (!state.isSelectionMode) {
        toggleSelectionMode(true);
      }
      togglePhotoSelect(p, card);
    }, 550);
  }, { passive: true });
  card.addEventListener('touchend', () => { if (pressTimer) clearTimeout(pressTimer); });
  card.addEventListener('touchmove', () => { if (pressTimer) clearTimeout(pressTimer); });

  card.addEventListener('click', (e) => {
    if (state.isSelectionMode) {
      e.preventDefault();
      togglePhotoSelect(p, card);
    } else if (_openLightboxCallback) {
      _openLightboxCallback(idx);
    }
  });
  return card;
}

export async function fetchNextSearchPage() {
  if (state.isFetchingNextSearchPage || !state.hasMoreSearchPages) return;
  if (!state.currentSearchFaceThumb && !state.currentSearchEmbedding && !state.currentSearchFile && !state.currentSearchId) return;
  state.isFetchingNextSearchPage = true;

  try {
    const payload = {
      search_id: state.currentSearchId,
      session_id: getSessionId(),
      visitor_id: getVisitorId(),
      embedding: state.currentSearchEmbedding,
      face_thumb: state.currentSearchFaceThumb,
      page: state.currentSearchPage + 1,
      page_size: 50
    };
    let res = await fetch('/api/check-profile-matches', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      res = await fetch(`/api/events/${state.slug}/check-profile-matches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    }

    let data = null;
    if (res.ok) {
      data = await res.json();
    }

    // Resilient fallback to /api/search if check-profile-matches returned no photos
    if ((!data || data.status === 'error' || !data.photos || data.photos.length === 0) && state.currentSearchFile) {
      try {
        const fd = new FormData();
        fd.append('selfie', state.currentSearchFile);
        if (state.currentSelectedFaceIndex !== null && state.currentSelectedFaceIndex !== undefined) {
          fd.append('face_index', state.currentSelectedFaceIndex);
        }
        fd.append('page', state.currentSearchPage + 1);
        fd.append('page_size', 50);
        let sRes = await fetch('/api/search', { method: 'POST', body: fd });
        if (!sRes.ok) {
          sRes = await fetch(`/api/${state.slug}/search`, { method: 'POST', body: fd });
        }
        if (sRes.ok) {
          const sData = await sRes.json();
          if (sData && sData.photos && sData.photos.length > 0) {
            data = sData;
          }
        }
      } catch (eFallback) {
        console.warn('Fallback search pagination failed:', eFallback);
      }
    }

    if (data && data.photos && data.photos.length > 0) {
      if (data.search_id) {
        state.currentSearchId = data.search_id;
        try { localStorage.setItem('brk_current_search_id', data.search_id); } catch (e) {}
      }
      if (data.creation_token) {
        state.currentCreationToken = data.creation_token;
        try { localStorage.setItem('brk_current_creation_token', data.creation_token); } catch (e) {}
      }
      if (data.embedding && !state.currentSearchEmbedding) {
        state.currentSearchEmbedding = data.embedding;
        try { localStorage.setItem('brk_saved_embedding', JSON.stringify(data.embedding)); } catch (e) {}
      }
      if (data.date_summary) {
        state.serverDateSummary = data.date_summary;
        try {
          localStorage.setItem('brk_saved_date_summary', JSON.stringify(data.date_summary));
        } catch (e) {}
      }
      state.currentSearchPage = data.page || (state.currentSearchPage + 1);
      state.hasMoreSearchPages = !!data.has_more;
      if (typeof data.total_count === 'number' && data.total_count > 0) {
        state.totalMatchingPhotosCount = data.total_count;
      }
      appendPhotosToGrid(data.photos);

      if (state.hasMoreSearchPages) {
        setTimeout(() => {
          if (state.hasMoreSearchPages && !state.isFetchingNextSearchPage) {
            fetchNextSearchPage();
          }
        }, 200);
      }
    } else {
      state.hasMoreSearchPages = false;
    }
  } catch (err) {
    console.warn('Error loading next batch of photos:', err);
  } finally {
    state.isFetchingNextSearchPage = false;
  }
}

export function appendPhotosToGrid(newPhotos) {
  if (!newPhotos || newPhotos.length === 0) return;
  const seenFnames = new Set(state.rawPhotosList.map(item => {
    return (typeof item === 'string' ? item.split('/').pop() : (item.filename || item.rel_path.split('/').pop())).toLowerCase();
  }));
  const seenStems = new Set(state.rawPhotosList.map(item => {
    const fn = (typeof item === 'string' ? item.split('/').pop() : (item.filename || item.rel_path.split('/').pop())).toLowerCase();
    return normalizeBaseFilename(fn);
  }).filter(Boolean));
  const seenSha = new Set(state.rawPhotosList.map(item => (typeof item === 'object' && item && item.sha256) ? item.sha256 : null).filter(Boolean));

  const added = [];
  for (const item of newPhotos) {
    const fn = (typeof item === 'string' ? item.split('/').pop() : (item.filename || item.rel_path.split('/').pop())).toLowerCase();
    const stem = normalizeBaseFilename(fn);
    const sha = (typeof item === 'object' && item && item.sha256) ? item.sha256 : null;

    if (seenFnames.has(fn)) continue;
    if (sha && seenSha.has(sha)) continue;
    if (stem && seenStems.has(stem)) continue;

    seenFnames.add(fn);
    if (stem) seenStems.add(stem);
    if (sha) seenSha.add(sha);
    added.push(item);
    state.rawPhotosList.push(item);
  }
  if (added.length === 0) return;

  for (let i = 0; i < added.length; i++) {
    const item = added[i];
    const parsed = parsePhotoDate(item);
    const matchesFilter = (!state.selectedCalendarDate && !state.selectedOtherAlbum) ||
      (state.selectedCalendarDate && parsed.isDate && parsed.dateKey === state.selectedCalendarDate) ||
      (state.selectedOtherAlbum && !parsed.isDate && parsed.rawAlbum === state.selectedOtherAlbum);

    if (matchesFilter) {
      const idx = state.currentPhotosList.length;
      state.currentPhotosList.push(item);
      state.gridRenderQueue.push({
        type: 'photo',
        groupKey: parsed.key,
        item,
        idx
      });
    }
  }

  try {
    localStorage.setItem('brk_saved_photos', JSON.stringify(state.rawPhotosList));
  } catch (e) {}

  state.totalMatchingPhotosCount = Math.max(state.totalMatchingPhotosCount, state.rawPhotosList.length);
  renderNextGridBatch();
  updateCalendarAndFilters();
  if (_updateLightboxCallback) {
    _updateLightboxCallback();
  }
}

export function renderNextGridBatch() {
  if (state.isRenderingBatch) return;
  state.isRenderingBatch = true;

  const grid = $('photosGrid');
  const sentinel = $('gridScrollSentinel');
  const end = Math.min(state.gridQueueCursor + state.GRID_BATCH_SIZE, state.gridRenderQueue.length);

  for (let i = state.gridQueueCursor; i < end; i++) {
    const entry = state.gridRenderQueue[i];
    if (entry.type === 'section_header') {
      let section = grid.querySelector(`.gallery-date-section[data-group-key="${CSS.escape(entry.groupKey)}"]`);
      if (!section) {
        section = document.createElement('section');
        section.className = 'gallery-date-section';
        section.dataset.groupKey = entry.groupKey;
        section.innerHTML = `
          <div class="gallery-date-header">
            <div class="gallery-date-title-wrap">
              <h3 class="gallery-date-heading">${entry.title}</h3>
              <span class="gallery-date-count">${entry.count} ${entry.count === 1 ? 'photo' : 'photos'}</span>
            </div>
          </div>
          <div class="gallery-date-grid"></div>
        `;
        if (sentinel && sentinel.parentNode === grid) {
          grid.insertBefore(section, sentinel);
        } else {
          grid.appendChild(section);
        }
      } else {
        const countEl = section.querySelector('.gallery-date-count');
        if (countEl) countEl.textContent = `${entry.count} ${entry.count === 1 ? 'photo' : 'photos'}`;
      }
    } else if (entry.type === 'photo') {
      let section = grid.querySelector(`.gallery-date-section[data-group-key="${CSS.escape(entry.groupKey)}"]`);
      if (!section) {
        const parsed = parsePhotoDate(entry.item);
        section = document.createElement('section');
        section.className = 'gallery-date-section';
        section.dataset.groupKey = entry.groupKey;
        section.innerHTML = `
          <div class="gallery-date-header">
            <div class="gallery-date-title-wrap">
              <h3 class="gallery-date-heading">${parsed.title}</h3>
              <span class="gallery-date-count">1 photo</span>
            </div>
          </div>
          <div class="gallery-date-grid"></div>
        `;
        if (sentinel && sentinel.parentNode === grid) {
          grid.insertBefore(section, sentinel);
        } else {
          grid.appendChild(section);
        }
      }
      const dateGrid = section.querySelector('.gallery-date-grid');
      if (dateGrid) {
        const card = createPhotoCard(entry.item, entry.idx);
        dateGrid.appendChild(card);
        const countEl = section.querySelector('.gallery-date-count');
        if (countEl) {
          const c = dateGrid.children.length;
          countEl.textContent = `${c} ${c === 1 ? 'photo' : 'photos'}`;
        }
      }
    }
  }

  state.gridQueueCursor = end;
  state.isRenderingBatch = false;
  updateProgressText();

  if (state.gridQueueCursor >= state.gridRenderQueue.length - 16 && state.hasMoreSearchPages && !state.isFetchingNextSearchPage) {
    fetchNextSearchPage();
  }

  if (state.gridQueueCursor >= state.gridRenderQueue.length && !state.hasMoreSearchPages) {
    if (state.gridScrollObserver && sentinel) {
      state.gridScrollObserver.unobserve(sentinel);
    }
    if (sentinel) sentinel.remove();
  }
}

export function renderPhotos(photosList) {
  const seenFnames = new Set();
  const seenStems = new Set();
  const seenSha = new Set();
  const uniquePhotos = [];
  for (const item of (photosList || [])) {
    const fn = (typeof item === 'string' ? item.split('/').pop() : (item.filename || item.rel_path.split('/').pop())).toLowerCase();
    const stem = normalizeBaseFilename(fn);
    const sha = (typeof item === 'object' && item && item.sha256) ? item.sha256 : null;

    if (seenFnames.has(fn)) continue;
    if (sha && seenSha.has(sha)) continue;
    if (stem && seenStems.has(stem)) continue;

    seenFnames.add(fn);
    if (stem) seenStems.add(stem);
    if (sha) seenSha.add(sha);
    uniquePhotos.push(item);
  }

  state.rawPhotosList = uniquePhotos;
  state.selectedCalendarDate = null;
  state.selectedOtherAlbum = null;
  state.hasUserNavigatedSheetMonth = false;
  state.selectedPhotos.clear();
  updateBulkBar();

  if ($('emptyResultsCard')) {
    $('emptyResultsCard').hidden = state.rawPhotosList.length > 0;
  }

  applyPhotoFiltersAndSort();
  updateCalendarAndFilters();
}

export function renderPhotosGridData(list) {
  state.currentPhotosList = list;
  const grid = $('photosGrid');
  grid.replaceChildren();

  if (state.gridScrollObserver) {
    state.gridScrollObserver.disconnect();
  }

  if (state.currentPhotosList.length === 0) {
    grid.innerHTML = '<div style="color:#64748b;text-align:center;padding:3rem 1rem;font-size:0.95rem;">No photos match the selected filter.</div>';
    updateProgressText();
    return;
  }

  const groupMap = new Map();
  for (let idx = 0; idx < state.currentPhotosList.length; idx++) {
    const item = state.currentPhotosList[idx];
    const parsed = parsePhotoDate(item);
    if (!groupMap.has(parsed.key)) {
      groupMap.set(parsed.key, {
        key: parsed.key,
        title: parsed.title,
        sortTime: parsed.sortTime,
        items: []
      });
    }
    groupMap.get(parsed.key).items.push({ item, idx });
  }

  const groups = Array.from(groupMap.values());
  groups.sort((a, b) => {
    if (a.sortTime && b.sortTime && a.sortTime !== b.sortTime) {
      return state.sortOrder === 'newest' ? b.sortTime - a.sortTime : a.sortTime - b.sortTime;
    }
    if (a.sortTime) return -1;
    if (b.sortTime) return 1;
    return a.key.localeCompare(b.key);
  });

  state.gridRenderQueue = [];
  state.gridQueueCursor = 0;

  for (const g of groups) {
    state.gridRenderQueue.push({
      type: 'section_header',
      groupKey: g.key,
      title: g.title,
      count: g.items.length
    });
    for (const entry of g.items) {
      state.gridRenderQueue.push({
        type: 'photo',
        groupKey: g.key,
        item: entry.item,
        idx: entry.idx
      });
    }
  }

  let sentinel = $('gridScrollSentinel');
  if (!sentinel) {
    sentinel = document.createElement('div');
    sentinel.id = 'gridScrollSentinel';
    sentinel.style.height = '30px';
    sentinel.style.width = '100%';
    sentinel.style.display = 'block';
  }
  grid.appendChild(sentinel);

  if (state.gridRenderQueue.length > state.GRID_BATCH_SIZE || state.hasMoreSearchPages) {
    state.gridScrollObserver = new IntersectionObserver((entries) => {
      if (entries[0] && entries[0].isIntersecting) {
        if (state.gridQueueCursor < state.gridRenderQueue.length) {
          renderNextGridBatch();
        } else if (state.hasMoreSearchPages && !state.isFetchingNextSearchPage) {
          fetchNextSearchPage();
        }
      }
    }, {
      root: $('galleryScrollBody') || null,
      rootMargin: '1000px 0px',
      threshold: 0
    });
    state.gridScrollObserver.observe(sentinel);
  }

  renderNextGridBatch();
  updateProgressText();
}

export function openGalleryOverlay() {
  const overlay = $('photosSection');
  if (!overlay) return;
  overlay.hidden = false;
  document.body.style.overflow = 'hidden';

  const viewAction = $('searchViewPhotosAction');
  if (viewAction) viewAction.hidden = false;

  updateMobileNavigationState();

  try {
    history.pushState({ brkGalleryOpen: true }, '');
  } catch (e) {}
}

export function closeGalleryOverlay(isFromPopState = false) {
  const overlay = $('photosSection');
  if (!overlay || overlay.hidden) return;

  overlay.hidden = true;
  document.body.style.overflow = '';

  if (state.isSelectionMode || state.selectedPhotos.size > 0) {
    clearSelection();
  } else {
    updateMobileNavigationState();
  }

  if (!isFromPopState && window.history.state && window.history.state.brkGalleryOpen) {
    try {
      window.history.back();
    } catch (e) {}
  }
}
