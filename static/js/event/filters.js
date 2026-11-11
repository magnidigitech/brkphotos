// Filter sheet, calendar date picker, album picker, and sort ordering
import { $, state, MONTH_NAMES, parsePhotoDate } from './state.js';

export const FULL_MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
];

let _renderGridCallback = null;
let _updateProgressCallback = null;

export function setFilterCallbacks({ renderGrid, updateProgress }) {
  if (renderGrid) _renderGridCallback = renderGrid;
  if (updateProgress) _updateProgressCallback = updateProgress;
}

export function updateCalendarAndFilters() {
  const toolbar = $('discoveryToolbar');
  if (!toolbar) return;

  if (!state.rawPhotosList || state.rawPhotosList.length === 0) {
    toolbar.hidden = true;
    return;
  }
  toolbar.hidden = false;

  state.datePhotoCounts.clear();
  state.otherAlbumCounts.clear();

  if (state.serverDateSummary && state.serverDateSummary.date_counts) {
    // Hydrate from complete server-side aggregation across all matches
    for (const [dk, count] of Object.entries(state.serverDateSummary.date_counts)) {
      state.datePhotoCounts.set(dk, Number(count) || 0);
    }
    if (state.serverDateSummary.other_albums) {
      for (const [alb, count] of Object.entries(state.serverDateSummary.other_albums)) {
        state.otherAlbumCounts.set(alb, Number(count) || 0);
      }
    }
    state.newestCalendarDate = state.serverDateSummary.newest_date || null;
  } else {
    // Fallback to client-side iteration over current loaded items
    let latestDateKey = null;
    let latestSortTime = -1;

    for (const item of state.rawPhotosList) {
      const parsed = parsePhotoDate(item);
      if (parsed.isDate && parsed.dateKey) {
        const count = (state.datePhotoCounts.get(parsed.dateKey) || 0) + 1;
        state.datePhotoCounts.set(parsed.dateKey, count);
        if (parsed.sortTime > latestSortTime) {
          latestSortTime = parsed.sortTime;
          latestDateKey = parsed.dateKey;
        }
      } else {
        const count = (state.otherAlbumCounts.get(parsed.rawAlbum) || 0) + 1;
        state.otherAlbumCounts.set(parsed.rawAlbum, count);
      }
    }
    state.newestCalendarDate = latestDateKey;
  }

  if (!state.hasUserNavigatedSheetMonth && state.newestCalendarDate) {
    const parts = state.newestCalendarDate.split('-');
    state.sheetViewYear = parseInt(parts[0], 10);
    state.sheetViewMonth = parseInt(parts[1], 10) - 1;
  }

  updateMainFilterControls();

  // If bottom sheet is currently open, refresh its content
  const backdrop = $('filterSheetBackdrop');
  if (backdrop && !backdrop.hidden && backdrop.classList.contains('active')) {
    renderSheetContent();
  }
}

export function updateMainFilterControls() {
  const allCount = (state.totalMatchingPhotosCount > 0) ? state.totalMatchingPhotosCount : state.rawPhotosList.length;
  const allChip = $('allPhotosChip');
  const allChipCount = $('allPhotosChipCount');
  if (allChipCount) allChipCount.textContent = `(${allCount})`;
  if (allChip) allChip.classList.toggle('active', !state.selectedCalendarDate && !state.selectedOtherAlbum);

  const filterBtn = $('openFilterSheetBtn');
  const badge = $('filterActiveBadge');
  const pill = $('activeFilterPill');
  const pillText = $('activeFilterPillText');

  const isFiltered = !!(state.selectedCalendarDate || state.selectedOtherAlbum);
  if (filterBtn) filterBtn.classList.toggle('has-filter', isFiltered);
  if (badge) badge.hidden = !isFiltered;

  const navFilterBadge = $('navFilterBadge');
  if (navFilterBadge) navFilterBadge.style.display = isFiltered ? 'inline-block' : 'none';

  const mobPill = $('mobileActiveFilterPill');
  const mobPillText = $('mobileActiveFilterPillText');
  if (mobPill && mobPillText) {
    if (state.selectedCalendarDate) {
      mobPill.hidden = false;
      const parts = state.selectedCalendarDate.split('-');
      const m = parseInt(parts[1], 10) - 1;
      const d = parseInt(parts[2], 10);
      const count = state.datePhotoCounts.get(state.selectedCalendarDate) || 0;
      mobPillText.textContent = `${String(d).padStart(2, '0')} ${MONTH_NAMES[m]} (${count})`;
    } else if (state.selectedOtherAlbum) {
      mobPill.hidden = false;
      const count = state.otherAlbumCounts.get(state.selectedOtherAlbum) || 0;
      mobPillText.textContent = `${state.selectedOtherAlbum} (${count})`;
    } else {
      mobPill.hidden = true;
    }
  }

  if (pill && pillText) {
    if (state.selectedCalendarDate) {
      pill.hidden = false;
      const parts = state.selectedCalendarDate.split('-');
      const y = parseInt(parts[0], 10);
      const m = parseInt(parts[1], 10) - 1;
      const d = parseInt(parts[2], 10);
      const count = state.datePhotoCounts.get(state.selectedCalendarDate) || 0;
      pillText.textContent = `${String(d).padStart(2, '0')} ${MONTH_NAMES[m]} (${count})`;
      pill.title = `Filtered by date: ${String(d).padStart(2, '0')} ${MONTH_NAMES[m]} ${y}`;
    } else if (state.selectedOtherAlbum) {
      pill.hidden = false;
      const count = state.otherAlbumCounts.get(state.selectedOtherAlbum) || 0;
      pillText.textContent = `${state.selectedOtherAlbum} (${count})`;
      pill.title = `Filtered by album: ${state.selectedOtherAlbum}`;
    } else {
      pill.hidden = true;
    }
  }

  if (_updateProgressCallback) _updateProgressCallback();
}

export function openFilterSheet() {
  state.draftCalendarDate = state.selectedCalendarDate;
  state.draftOtherAlbum = state.selectedOtherAlbum;
  state.draftSortOrder = state.sortOrder;

  if (state.selectedCalendarDate) {
    const parts = state.selectedCalendarDate.split('-');
    state.sheetViewYear = parseInt(parts[0], 10);
    state.sheetViewMonth = parseInt(parts[1], 10) - 1;
  } else if (!state.hasUserNavigatedSheetMonth && state.newestCalendarDate) {
    const parts = state.newestCalendarDate.split('-');
    state.sheetViewYear = parseInt(parts[0], 10);
    state.sheetViewMonth = parseInt(parts[1], 10) - 1;
  }

  renderSheetContent();

  const backdrop = $('filterSheetBackdrop');
  const container = $('filterSheetContainer');
  if (!backdrop || !container) return;

  container.style.transform = '';
  backdrop.hidden = false;
  requestAnimationFrame(() => {
    backdrop.classList.add('active');
  });
  document.body.style.overflow = 'hidden';
}

export function closeFilterSheet() {
  const backdrop = $('filterSheetBackdrop');
  if (!backdrop || backdrop.hidden) return;

  backdrop.classList.remove('active');
  setTimeout(() => {
    backdrop.hidden = true;
  }, 230);

  const galleryOverlay = $('photosSection');
  if (!galleryOverlay || galleryOverlay.hidden) {
    document.body.style.overflow = '';
  }
}

export function renderSheetContent() {
  const titleEl = $('calMonthYearTitle');
  const gridEl = $('calendarDaysGrid');
  if (!titleEl || !gridEl) return;

  titleEl.textContent = `${FULL_MONTH_NAMES[state.sheetViewMonth]} ${state.sheetViewYear}`;
  gridEl.replaceChildren();

  const firstDayOfWeek = new Date(state.sheetViewYear, state.sheetViewMonth, 1).getDay();
  const daysInMonth = new Date(state.sheetViewYear, state.sheetViewMonth + 1, 0).getDate();

  for (let i = 0; i < firstDayOfWeek; i++) {
    const emptyCell = document.createElement('div');
    emptyCell.className = 'calendar-day-cell empty';
    gridEl.appendChild(emptyCell);
  }

  const monthPad = String(state.sheetViewMonth + 1).padStart(2, '0');

  for (let day = 1; day <= daysInMonth; day++) {
    const dayPad = String(day).padStart(2, '0');
    const dateKey = `${state.sheetViewYear}-${monthPad}-${dayPad}`;
    const count = state.datePhotoCounts.get(dateKey) || 0;
    const hasPhotos = count > 0;
    const isSelected = state.draftCalendarDate === dateKey;

    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'calendar-day-cell' + (hasPhotos ? ' has-photos' : ' muted') + (isSelected ? ' selected' : '');
    cell.innerHTML = `<span>${day}</span>${hasPhotos ? '<span class="calendar-day-dot"></span>' : ''}`;

    if (hasPhotos) {
      cell.title = `${dayPad} ${MONTH_NAMES[state.sheetViewMonth]} ${state.sheetViewYear} (${count} ${count === 1 ? 'photo' : 'photos'})`;
      cell.addEventListener('click', () => {
        toggleDraftCalendarDate(dateKey);
      });
    }

    gridEl.appendChild(cell);
  }

  const totalRendered = firstDayOfWeek + daysInMonth;
  const trailingEmpty = Math.max(0, 42 - totalRendered);
  for (let i = 0; i < trailingEmpty; i++) {
    const emptyCell = document.createElement('div');
    emptyCell.className = 'calendar-day-cell empty';
    gridEl.appendChild(emptyCell);
  }

  const otherSection = $('sheetOtherAlbumsSection');
  const otherBadge = $('sheetOtherAlbumsCountBadge');
  const otherContainer = $('otherAlbumsChipsContainer');

  if (otherSection && otherBadge && otherContainer) {
    if (state.otherAlbumCounts.size > 0) {
      otherSection.hidden = false;
      otherBadge.textContent = `(${state.otherAlbumCounts.size})`;
      otherContainer.replaceChildren();

      const sortedAlbums = Array.from(state.otherAlbumCounts.entries()).sort((a, b) => {
        return b[1] - a[1] || a[0].localeCompare(b[0]);
      });

      for (const [alb, count] of sortedAlbums) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'filter-chip' + (state.draftOtherAlbum === alb ? ' active' : '');
        chip.innerHTML = `<span>${alb}</span> <span>(${count})</span>`;
        chip.addEventListener('click', () => {
          toggleDraftOtherAlbum(alb);
        });
        otherContainer.appendChild(chip);
      }
    } else {
      otherSection.hidden = true;
    }
  }

  const banner = $('sheetActiveFilterBanner');
  const label = $('sheetActiveFilterLabel');
  if (banner && label) {
    if (state.draftCalendarDate) {
      banner.hidden = false;
      const parts = state.draftCalendarDate.split('-');
      const y = parseInt(parts[0], 10);
      const m = parseInt(parts[1], 10) - 1;
      const d = parseInt(parts[2], 10);
      const count = state.datePhotoCounts.get(state.draftCalendarDate) || 0;
      const formatted = `${String(d).padStart(2, '0')} ${MONTH_NAMES[m]} ${y}`;
      label.innerHTML = `Selected: <strong>${formatted}</strong> (${count} ${count === 1 ? 'photo' : 'photos'})`;
    } else if (state.draftOtherAlbum) {
      banner.hidden = false;
      const count = state.otherAlbumCounts.get(state.draftOtherAlbum) || 0;
      label.innerHTML = `Selected: <strong>${state.draftOtherAlbum}</strong> (${count} ${count === 1 ? 'photo' : 'photos'})`;
    } else {
      banner.hidden = true;
    }
  }

  const newestBtn = $('sheetSortNewestBtn');
  const oldestBtn = $('sheetSortOldestBtn');
  if (newestBtn && oldestBtn) {
    newestBtn.classList.toggle('active', state.draftSortOrder === 'newest');
    oldestBtn.classList.toggle('active', state.draftSortOrder === 'oldest');
  }

  const applyBtnText = $('sheetApplyBtnText');
  if (applyBtnText) {
    if (state.draftCalendarDate) {
      const count = state.datePhotoCounts.get(state.draftCalendarDate) || 0;
      applyBtnText.textContent = `Show ${count} ${count === 1 ? 'Photo' : 'Photos'}`;
    } else if (state.draftOtherAlbum) {
      const count = state.otherAlbumCounts.get(state.draftOtherAlbum) || 0;
      applyBtnText.textContent = `Show ${count} ${count === 1 ? 'Photo' : 'Photos'}`;
    } else {
      const total = (state.totalMatchingPhotosCount > 0) ? state.totalMatchingPhotosCount : state.rawPhotosList.length;
      applyBtnText.textContent = `Show All ${total} Photos`;
    }
  }
}

export function toggleDraftCalendarDate(dateKey) {
  if (state.draftCalendarDate === dateKey) {
    state.draftCalendarDate = null;
  } else {
    state.draftCalendarDate = dateKey;
    state.draftOtherAlbum = null;
  }
  renderSheetContent();
}

export function toggleDraftOtherAlbum(albumName) {
  if (state.draftOtherAlbum === albumName) {
    state.draftOtherAlbum = null;
  } else {
    state.draftOtherAlbum = albumName;
    state.draftCalendarDate = null;
  }
  renderSheetContent();
}

export function clearSheetDraftFilters() {
  state.draftCalendarDate = null;
  state.draftOtherAlbum = null;
  state.draftSortOrder = 'newest';
  renderSheetContent();
}

export function applySheetFilters() {
  state.sortOrder = state.draftSortOrder;
  state.selectedCalendarDate = state.draftCalendarDate;
  state.selectedOtherAlbum = state.draftOtherAlbum;
  closeFilterSheet();
  applyPhotoFiltersAndSort();
  updateMainFilterControls();
}

export function applyPhotoFiltersAndSort() {
  let filtered = [...state.rawPhotosList];

  if (state.selectedCalendarDate) {
    filtered = filtered.filter(item => {
      const parsed = parsePhotoDate(item);
      return parsed.isDate && parsed.dateKey === state.selectedCalendarDate;
    });
  } else if (state.selectedOtherAlbum) {
    filtered = filtered.filter(item => {
      const parsed = parsePhotoDate(item);
      return !parsed.isDate && parsed.rawAlbum === state.selectedOtherAlbum;
    });
  }

  filtered.sort((a, b) => {
    const featA = (typeof a === 'object' && a.is_featured) ? 1 : 0;
    const featB = (typeof b === 'object' && b.is_featured) ? 1 : 0;
    if (featA !== featB) return featB - featA;

    const dateA = parsePhotoDate(a);
    const dateB = parsePhotoDate(b);

    if (dateA.sortTime && dateB.sortTime && dateA.sortTime !== dateB.sortTime) {
      return state.sortOrder === 'newest' ? dateB.sortTime - dateA.sortTime : dateA.sortTime - dateB.sortTime;
    }

    const nameA = (typeof a === 'string' ? a.split('/').pop() : (a.filename || a.rel_path.split('/').pop())).toLowerCase();
    const nameB = (typeof b === 'string' ? b.split('/').pop() : (b.filename || b.rel_path.split('/').pop())).toLowerCase();
    const comp = nameA.localeCompare(nameB, undefined, { numeric: true, sensitivity: 'base' });
    return state.sortOrder === 'newest' ? comp : -comp;
  });

  if (_renderGridCallback) {
    _renderGridCallback(filtered);
  }
}

export function toggleSortOrder() {
  state.sortOrder = state.sortOrder === 'newest' ? 'oldest' : 'newest';
  const text = $('sortOrderText');
  if (text) text.textContent = state.sortOrder === 'newest' ? 'Newest First' : 'Oldest First';
  applyPhotoFiltersAndSort();
}
