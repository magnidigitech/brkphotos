// Navigation controls: mobile bottom navigation bar, more sheet, and UI visibility synchronization
import { $, state } from './state.js';

export function updateMobileNavigationState() {
  const galleryOverlay = $('photosSection');
  const isGalleryOpen = !!(galleryOverlay && !galleryOverlay.hidden && window.getComputedStyle(galleryOverlay).display !== 'none');
  const isMobile = window.innerWidth <= 767;
  const nav = $('mobileBottomNav');
  const bar = $('bulkSelectionBar');

  if (!isGalleryOpen) {
    // Gallery is closed (homepage, selfie capture, tips, etc.)
    if (nav) {
      nav.hidden = true;
      nav.style.display = 'none';
    }
    if (bar) {
      bar.hidden = true;
      bar.style.display = 'none';
      bar.classList.remove('active');
    }
    return;
  }

  // Gallery is open
  if (isMobile) {
    if (state.isSelectionMode) {
      // Contextual Selection Panel replaces Bottom Nav on Mobile
      if (nav) {
        nav.hidden = true;
        nav.style.display = 'none';
      }
      if (bar) {
        bar.hidden = false;
        bar.style.display = 'flex';
        bar.classList.add('active');
      }
    } else {
      // Normal Mobile Gallery Browsing Mode
      if (nav) {
        nav.hidden = false;
        nav.style.display = 'flex';
      }
      if (bar) {
        bar.classList.remove('active');
        bar.style.display = 'none';
        bar.hidden = true;
      }
    }
  } else {
    // Desktop gallery mode: mobile bottom nav is ALWAYS hidden
    if (nav) {
      nav.hidden = true;
      nav.style.display = 'none';
    }
    if (state.isSelectionMode) {
      if (bar) {
        bar.hidden = false;
        bar.style.display = 'flex';
        bar.classList.add('active');
      }
    } else {
      if (bar) {
        bar.classList.remove('active');
        bar.style.display = 'none';
        bar.hidden = true;
      }
    }
  }
}

export function openMoreSheet() {
  const backdrop = $('moreSheetBackdrop');
  if (!backdrop) return;
  backdrop.hidden = false;
  requestAnimationFrame(() => {
    backdrop.classList.add('active');
  });
  document.body.style.overflow = 'hidden';
}

export function closeMoreSheet() {
  const backdrop = $('moreSheetBackdrop');
  if (!backdrop) return;
  backdrop.classList.remove('active');
  setTimeout(() => {
    backdrop.hidden = true;
    document.body.style.overflow = '';
  }, 250);
}
