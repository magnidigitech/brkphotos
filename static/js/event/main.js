// Main initialization entry point for the public event gallery
import { $, state, emitAnalyticsEvent, showToast } from './state.js';
import { updateMobileNavigationState, openMoreSheet, closeMoreSheet } from './navigation.js';
import { toggleSelectionMode, togglePhotoSelect, selectAllPhotos, clearSelection } from './selection.js';
import { downloadSelectedZip } from './downloads.js';
import {
  openFilterSheet, closeFilterSheet, renderSheetContent,
  clearSheetDraftFilters, applySheetFilters, applyPhotoFiltersAndSort,
  toggleSortOrder, updateMainFilterControls
} from './filters.js';
import {
  loadEvent, renderPhotos, openGalleryOverlay, closeGalleryOverlay,
  setGalleryLightboxCallbacks, renderNextGridBatch, fetchNextSearchPage
} from './gallery.js';
import {
  openPhotoLightbox, closeLightbox, goToNextPhoto, goToPrevPhoto,
  jumpToPhoto, resetZoom, setZoom, toggleZoomBar, initLightboxSlider,
  updateLightboxHeader, renderFilmstripWindow, updateSlideSlots, getPhotoEnc
} from './viewer.js';
import {
  isCameraSupported, triggerCamera, captureWebcamPhoto, retakeWebcamPhoto,
  useCapturedPhoto, toggleCameraFlip, stopWebcam, viewSavedPhotos,
  clearSavedProfile, checkSavedProfile, syncLatestProfileCount,
  hideMultiFacePicker, processAndSearchSelfie
} from './camera-search.js';
import {
  updateMyAlbumsBadge, openCreateAlbumModal, closeCreateAlbumModal,
  confirmCreateAlbum, openMyAlbumsModal, closeMyAlbumsModal,
  copyFullSuccessLink, closeSuccessModal
} from './shared-albums.js';

// Connect lightbox callbacks to gallery
setGalleryLightboxCallbacks({
  openLightbox: openPhotoLightbox,
  updateLightbox: () => {
    if (!$('photoLightboxModal')?.hidden) {
      renderFilmstripWindow(state.currentPhotoIndex);
      updateLightboxHeader(state.currentPhotoIndex);
      updateSlideSlots(state.currentPhotoIndex);
    }
  }
});

// --- Event Listeners Setup ---
function initEventBindings() {
  // Mobile Bottom Navigation Actions
  $('navBtnPhotos')?.addEventListener('click', () => {
    const scrollEl = $('galleryScrollBody');
    if (scrollEl) scrollEl.scrollTo({ top: 0, behavior: 'smooth' });
    document.querySelectorAll('.bottom-nav-item').forEach(btn => btn.classList.remove('active'));
    $('navBtnPhotos')?.classList.add('active');
  });
  $('navBtnFilter')?.addEventListener('click', openFilterSheet);
  $('navBtnAlbums')?.addEventListener('click', openMyAlbumsModal);
  $('navBtnMore')?.addEventListener('click', openMoreSheet);

  // Gallery Overlay Buttons
  $('galleryDismissBtn')?.addEventListener('click', () => closeGalleryOverlay(false));
  $('searchViewPhotosBtn')?.addEventListener('click', () => openGalleryOverlay());
  $('closePhotosBtn')?.addEventListener('click', () => {
    closeGalleryOverlay(false);
    clearSelection();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // More Sheet Listeners
  $('closeMoreSheetBtn')?.addEventListener('click', closeMoreSheet);
  $('moreSheetDragHandle')?.addEventListener('click', closeMoreSheet);
  $('moreSheetBackdrop')?.addEventListener('click', (e) => {
    if (e.target === $('moreSheetBackdrop')) closeMoreSheet();
  });
  $('sheetSelectPhotosBtn')?.addEventListener('click', () => {
    closeMoreSheet();
    toggleSelectionMode(true);
  });
  $('sheetNewSearchBtn')?.addEventListener('click', () => {
    closeMoreSheet();
    $('closePhotosBtn')?.click();
  });
  $('mobileRemoveActiveFilterBtn')?.addEventListener('click', () => {
    state.selectedCalendarDate = null;
    state.selectedOtherAlbum = null;
    applyPhotoFiltersAndSort();
    updateMainFilterControls();
  });

  // Discovery & Sort
  $('sortOrderBtn')?.addEventListener('click', toggleSortOrder);

  // Main Page Filter Actions
  $('openFilterSheetBtn')?.addEventListener('click', openFilterSheet);
  $('allPhotosChip')?.addEventListener('click', () => {
    state.selectedCalendarDate = null;
    state.selectedOtherAlbum = null;
    applyPhotoFiltersAndSort();
    updateMainFilterControls();
  });
  $('removeActiveFilterBtn')?.addEventListener('click', () => {
    state.selectedCalendarDate = null;
    state.selectedOtherAlbum = null;
    applyPhotoFiltersAndSort();
    updateMainFilterControls();
  });

  // Filter Bottom Sheet Actions
  $('sheetSortNewestBtn')?.addEventListener('click', () => {
    state.draftSortOrder = 'newest';
    renderSheetContent();
  });
  $('sheetSortOldestBtn')?.addEventListener('click', () => {
    state.draftSortOrder = 'oldest';
    renderSheetContent();
  });
  $('closeFilterSheetBtn')?.addEventListener('click', closeFilterSheet);
  $('filterSheetBackdrop')?.addEventListener('click', (e) => {
    if (e.target === $('filterSheetBackdrop')) closeFilterSheet();
  });
  $('sheetClearSelectionBtn')?.addEventListener('click', clearSheetDraftFilters);
  $('sheetClearAllBtn')?.addEventListener('click', clearSheetDraftFilters);
  $('sheetApplyBtn')?.addEventListener('click', applySheetFilters);
  $('sheetOtherAlbumsToggle')?.addEventListener('click', () => {
    const drawer = $('sheetOtherAlbumsDrawer');
    const btn = $('sheetOtherAlbumsToggle');
    if (drawer && btn) {
      drawer.hidden = !drawer.hidden;
      btn.classList.toggle('open', !drawer.hidden);
    }
  });

  // Calendar Navigation inside Filter Sheet
  $('calPrevMonthBtn')?.addEventListener('click', () => {
    state.hasUserNavigatedSheetMonth = true;
    state.sheetViewMonth--;
    if (state.sheetViewMonth < 0) {
      state.sheetViewMonth = 11;
      state.sheetViewYear--;
    }
    renderSheetContent();
  });
  $('calNextMonthBtn')?.addEventListener('click', () => {
    state.hasUserNavigatedSheetMonth = true;
    state.sheetViewMonth++;
    if (state.sheetViewMonth > 11) {
      state.sheetViewMonth = 0;
      state.sheetViewYear++;
    }
    renderSheetContent();
  });

  // Camera & File Upload Controls
  $('cameraBtn')?.addEventListener('click', triggerCamera);
  $('uploadFileBtn')?.addEventListener('click', () => $('selfieFileInput')?.click());

  $('selfieFileInput')?.addEventListener('change', () => {
    const file = $('selfieFileInput')?.files?.[0];
    if (file) {
      processAndSearchSelfie(file);
    }
  });

  $('snapPhotoBtn')?.addEventListener('click', captureWebcamPhoto);
  $('retakePhotoBtn')?.addEventListener('click', retakeWebcamPhoto);
  $('usePhotoBtn')?.addEventListener('click', useCapturedPhoto);
  $('flipCameraBtn')?.addEventListener('click', toggleCameraFlip);
  $('cancelWebcamBtn')?.addEventListener('click', stopWebcam);
  $('closeWebcamModalBtn')?.addEventListener('click', stopWebcam);

  // Saved Profile Listeners
  $('viewSavedPhotosBtn')?.addEventListener('click', viewSavedPhotos);
  $('savedAvatarClickWrap')?.addEventListener('click', viewSavedPhotos);
  $('savedInfoClickWrap')?.addEventListener('click', viewSavedPhotos);
  $('clearSavedBtn')?.addEventListener('click', clearSavedProfile);

  // New Photos Alert Banner Listeners
  $('viewNewPhotosBannerBtn')?.addEventListener('click', viewSavedPhotos);
  $('closeNewPhotosBannerBtn')?.addEventListener('click', () => {
    const banner = $('newPhotosAlertBanner');
    if (banner) banner.hidden = true;
  });

  // Empty Results State Action Listeners
  $('emptyRetryBtn')?.addEventListener('click', triggerCamera);
  $('emptyUploadBtn')?.addEventListener('click', () => $('selfieFileInput')?.click());

  // Selection & Bulk Download Listeners
  $('toggleSelectBtn')?.addEventListener('click', () => toggleSelectionMode());
  $('bulkSelectAllBtn')?.addEventListener('click', selectAllPhotos);
  $('bulkDownloadZipBtn')?.addEventListener('click', downloadSelectedZip);
  $('bulkCancelBtn')?.addEventListener('click', clearSelection);

  // My Event Album Listeners
  $('bulkCreateAlbumBtn')?.addEventListener('click', openCreateAlbumModal);
  $('openMyAlbumsBtn')?.addEventListener('click', openMyAlbumsModal);
  $('btnCloseCreateAlbumModal')?.addEventListener('click', closeCreateAlbumModal);
  $('btnCancelCreateAlbum')?.addEventListener('click', closeCreateAlbumModal);
  $('btnConfirmCreateAlbum')?.addEventListener('click', confirmCreateAlbum);
  $('btnCloseSuccessModal')?.addEventListener('click', closeSuccessModal);
  $('btnCloseMyAlbumsModal')?.addEventListener('click', closeMyAlbumsModal);

  $('btnCopySuccessLink')?.addEventListener('click', copyFullSuccessLink);
  $('compactShareLinkDisplay')?.addEventListener('click', copyFullSuccessLink);
  $('compactShareLinkDisplay')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      copyFullSuccessLink();
    }
  });

  // Dismiss modals on backdrop click
  $('createAlbumModalBackdrop')?.addEventListener('click', (e) => {
    if (e.target === $('createAlbumModalBackdrop')) closeCreateAlbumModal();
  });
  $('albumSuccessModalBackdrop')?.addEventListener('click', (e) => {
    if (e.target === $('albumSuccessModalBackdrop')) closeSuccessModal();
  });
  $('myAlbumsModalBackdrop')?.addEventListener('click', (e) => {
    if (e.target === $('myAlbumsModalBackdrop')) closeMyAlbumsModal();
  });

  // Lightbox Controls
  $('closeLightboxBtn')?.addEventListener('click', () => closeLightbox(false));

  $('zoomInBtn')?.addEventListener('click', e => {
    e.stopPropagation();
    setZoom(1.5);
  });
  $('zoomOutBtn')?.addEventListener('click', e => {
    e.stopPropagation();
    setZoom(1.0);
  });
  $('zoomResetBtn')?.addEventListener('click', e => {
    e.stopPropagation();
    resetZoom();
  });
  $('closeZoomBarBtn')?.addEventListener('click', e => {
    e.stopPropagation();
    toggleZoomBar(false);
  });
  $('lightboxZoomToggleBtn')?.addEventListener('click', e => {
    e.stopPropagation();
    toggleZoomBar();
  });

  $('lightboxShareBtn')?.addEventListener('click', () => {
    const curItem = state.currentPhotosList[state.currentPhotoIndex];
    if (!curItem) return;
    const enc = getPhotoEnc(curItem);
    emitAnalyticsEvent('share_clicked', { photo_path: enc });
    const downloadUrl = `${window.location.origin}/download/hd/${enc}`;
    if (navigator.share) {
      navigator.share({
        title: 'Photo with BRK Sir',
        text: 'Check out my photograph with Bhashyam Ramakrishna Sir!',
        url: downloadUrl
      }).catch(() => {});
    } else if (navigator.clipboard) {
      navigator.clipboard.writeText(downloadUrl).then(() => {
        showToast('Photo download link copied to clipboard!');
      }).catch(() => {
        showToast('Photo link: ' + downloadUrl);
      });
    } else {
      showToast('Photo link: ' + downloadUrl);
    }
  });

  // Multi-Face Cancel / Reset
  $('cancelMultiFaceBtn')?.addEventListener('click', () => {
    hideMultiFacePicker();
    const statusEl = $('searchStatus');
    if (statusEl) statusEl.innerHTML = '';
    $('selfieFileInput')?.click();
  });

  // Keyboard Navigation
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      const moreSheet = $('moreSheetBackdrop');
      if (moreSheet && !moreSheet.hidden && moreSheet.classList.contains('active')) {
        closeMoreSheet();
        return;
      }
      const filterSheet = $('filterSheetBackdrop');
      if (filterSheet && !filterSheet.hidden && filterSheet.classList.contains('active')) {
        closeFilterSheet();
        return;
      }
    }
    if (!$('photoLightboxModal')?.hidden) {
      if (e.key === 'ArrowRight') goToNextPhoto();
      if (e.key === 'ArrowLeft') goToPrevPhoto();
      if (e.key === 'Escape') closeLightbox(false);
    }
  });

  // History popstate navigation
  window.addEventListener('popstate', (e) => {
    if (state.isClosingLightboxViaCode) {
      state.isClosingLightboxViaCode = false;
      const galleryBody = $('galleryScrollBody');
      if (galleryBody && state.lastGalleryScrollTop > 0) {
        galleryBody.scrollTop = state.lastGalleryScrollTop;
      }
      return;
    }

    const filterSheet = $('filterSheetBackdrop');
    if (filterSheet && !filterSheet.hidden && filterSheet.classList.contains('active')) {
      closeFilterSheet();
      return;
    }

    const lightbox = $('photoLightboxModal');
    if (lightbox && (!lightbox.hidden || lightbox.classList.contains('active'))) {
      closeLightbox(true);
      return;
    }

    if (e.state && e.state.brkGalleryOpen) {
      return;
    }

    const gallery = $('photosSection');
    if (gallery && !gallery.hidden) {
      closeGalleryOverlay(true);
      return;
    }
  });

  // Container-level long-press & drag suppression on gallery photos & lightbox
  const photosGridEl = $('photosGrid');
  if (photosGridEl) {
    photosGridEl.addEventListener('contextmenu', (e) => {
      if (e.target.closest('.photo-card, .photo-img-wrap, img, a')) {
        e.preventDefault();
      }
    });
    photosGridEl.addEventListener('dragstart', (e) => {
      if (e.target.closest('.photo-card, .photo-img-wrap, img, a')) {
        e.preventDefault();
      }
    });
  }

  const lightboxModalEl = $('photoLightboxModal');
  if (lightboxModalEl) {
    lightboxModalEl.addEventListener('contextmenu', (e) => {
      if (e.target.closest('.lightbox-slide-slot, .lightbox-img-wrap, .filmstrip-thumb, img, a')) {
        e.preventDefault();
      }
    });
    lightboxModalEl.addEventListener('dragstart', (e) => {
      if (e.target.closest('.lightbox-slide-slot, .lightbox-img-wrap, .filmstrip-thumb, img, a')) {
        e.preventDefault();
      }
    });
  }

  // Floating Action Button
  const fab = $('fabToOverview');
  if (fab) {
    window.addEventListener('scroll', () => {
      fab.classList.toggle('visible', window.scrollY > 350);
    }, { passive: true });
    fab.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  window.addEventListener('resize', updateMobileNavigationState);
}

// Initial bootstrap
initEventBindings();
updateMobileNavigationState();
updateMyAlbumsBadge();
checkSavedProfile();
syncLatestProfileCount();
loadEvent();

// PWA Service Worker Registration
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').then(reg => {
      console.log('BRK Photos PWA ServiceWorker registered with scope:', reg.scope);
    }).catch(err => {
      console.warn('BRK Photos PWA ServiceWorker registration failed:', err);
    });
  });
}

// Background sync on visibility change
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    syncLatestProfileCount();
  }
});
setInterval(() => {
  if (document.visibilityState === 'visible') {
    syncLatestProfileCount();
  }
}, 45000);

// Minimal test bridge hooks preserved on window for Selenium regression suites
window.viewSavedPhotos = viewSavedPhotos;
window.closeGalleryOverlay = closeGalleryOverlay;
window.openGalleryOverlay = openGalleryOverlay;
window.toggleSelectionMode = toggleSelectionMode;
window.togglePhotoSelect = togglePhotoSelect;
window.confirmCreateAlbum = confirmCreateAlbum;
window.openCreateAlbumModal = openCreateAlbumModal;
window.closeCreateAlbumModal = closeCreateAlbumModal;
window.openMyAlbumsModal = openMyAlbumsModal;
window.closeMyAlbumsModal = closeMyAlbumsModal;
window.selectAllPhotos = selectAllPhotos;
window.clearSelection = clearSelection;
window.renderPhotos = renderPhotos;
window.applyPhotoFiltersAndSort = applyPhotoFiltersAndSort;
window.openPhotoLightbox = openPhotoLightbox;
window.closeLightbox = closeLightbox;
window.triggerCamera = triggerCamera;
