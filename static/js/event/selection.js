// Selection mode, multi-select toggling, bulk bar updates, and long-press handlers
import { $, state } from './state.js';
import { updateMobileNavigationState } from './navigation.js';

export function toggleSelectionMode(force) {
  state.isSelectionMode = typeof force === 'boolean' ? force : !state.isSelectionMode;
  const toggleBtn = $('toggleSelectBtn');
  const toggleText = $('toggleSelectText');

  if (toggleText) {
    toggleText.textContent = state.isSelectionMode ? 'Done' : 'Select';
  }
  if (toggleBtn) {
    toggleBtn.classList.toggle('active-mode', state.isSelectionMode);
    toggleBtn.style.background = '';
    toggleBtn.style.color = '';
  }

  document.querySelectorAll('.photo-card').forEach(c => {
    c.classList.toggle('selection-active', state.isSelectionMode);
    const chk = c.querySelector('.photo-select-checkbox');
    if (chk) chk.style.display = state.isSelectionMode ? 'flex' : 'none';
  });

  updateBulkBar();
  updateMobileNavigationState();
}

export function togglePhotoSelect(path, cardEl) {
  if (state.selectedPhotos.has(path)) {
    state.selectedPhotos.delete(path);
    if (cardEl) cardEl.classList.remove('selected');
  } else {
    state.selectedPhotos.add(path);
    if (cardEl) cardEl.classList.add('selected');
  }
  updateBulkBar();
}

export function updateBulkBar() {
  const countEl = $('bulkSelectedCount');
  const zipBtn = $('bulkDownloadZipBtn');
  const albumBtn = $('bulkCreateAlbumBtn');
  const selectAllBtn = $('bulkSelectAllBtn');
  const count = state.selectedPhotos.size;

  if (countEl) {
    countEl.textContent = `${count} selected`;
  }
  if (zipBtn) {
    zipBtn.disabled = count === 0;
    zipBtn.style.opacity = count === 0 ? '0.5' : '1';
    zipBtn.style.pointerEvents = count === 0 ? 'none' : 'auto';
  }
  if (albumBtn) {
    albumBtn.disabled = count === 0;
    albumBtn.style.opacity = count === 0 ? '0.5' : '1';
    albumBtn.style.pointerEvents = count === 0 ? 'none' : 'auto';
  }
  if (selectAllBtn) {
    const allPaths = state.currentPhotosList.map(item => typeof item === 'string' ? item : item.rel_path);
    const isAllSelected = allPaths.length > 0 && allPaths.every(p => state.selectedPhotos.has(p));
    selectAllBtn.textContent = isAllSelected ? 'Deselect All' : 'Select All';
  }
}

export function selectAllPhotos() {
  const allPaths = state.currentPhotosList.map(item => typeof item === 'string' ? item : item.rel_path);
  const isAllSelected = allPaths.length > 0 && allPaths.every(p => state.selectedPhotos.has(p));

  if (isAllSelected) {
    allPaths.forEach(p => state.selectedPhotos.delete(p));
    document.querySelectorAll('.photo-card').forEach(c => c.classList.remove('selected'));
  } else {
    allPaths.forEach(p => state.selectedPhotos.add(p));
    document.querySelectorAll('.photo-card').forEach(c => c.classList.add('selected'));
  }
  updateBulkBar();
}

export function clearSelection() {
  state.selectedPhotos.clear();
  document.querySelectorAll('.photo-card').forEach(c => {
    c.classList.remove('selected', 'selection-active');
    const chk = c.querySelector('.photo-select-checkbox');
    if (chk) chk.style.display = 'none';
  });
  toggleSelectionMode(false);
}
