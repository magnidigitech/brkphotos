// Lightbox viewer: 3-panel smooth carousel, pinch-to-zoom, filmstrip, and swipe physics
import { $, state, trackGenuinePhotoView } from './state.js';
import { fetchNextSearchPage } from './gallery.js';

let sliderTrack = null;
let imgPrev = null;
let imgCurr = null;
let imgNext = null;
let spinnerPrev = null;
let spinnerCurr = null;
let spinnerNext = null;
let isTransitioning = false;
let zoomScale = 1;
let panX = 0;
let panY = 0;
let isPanning = false;

export function initLightboxSlider() {
  sliderTrack = $('lightboxSliderTrack');
  imgPrev = $('imgPrev');
  imgCurr = $('imgCurr');
  imgNext = $('imgNext');
  spinnerPrev = $('spinnerPrev');
  spinnerCurr = $('spinnerCurr');
  spinnerNext = $('spinnerNext');
  [imgPrev, imgCurr, imgNext].forEach(img => {
    if (img) {
      img.setAttribute('draggable', 'false');
      img.draggable = false;
      img.addEventListener('contextmenu', (e) => e.preventDefault());
      img.addEventListener('dragstart', (e) => e.preventDefault());
    }
  });
}

// Web Audio API Swipe Sound & Mute Preference
let audioCtx = null;
let isSoundMuted = localStorage.getItem('magni_gallery_muted') === 'true';

export function updateSoundButton() {
  const onIcon = $('soundIconOn');
  const offIcon = $('soundIconOff');
  const btn = $('lightboxSoundBtn');
  if (onIcon && offIcon) {
    onIcon.style.display = isSoundMuted ? 'none' : 'block';
    offIcon.style.display = isSoundMuted ? 'block' : 'none';
  }
  if (btn) {
    btn.title = isSoundMuted ? 'Unmute Swipe Sound' : 'Mute Swipe Sound';
  }
}

export function playSwipeSound() {
  if (isSoundMuted) return;
  try {
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) audioCtx = new AC();
    }
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
    if (!audioCtx) return;
    const now = audioCtx.currentTime;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();

    osc.type = 'sine';
    osc.frequency.setValueAtTime(450, now);
    osc.frequency.exponentialRampToValueAtTime(150, now + 0.045);

    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.045);

    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(now);
    osc.stop(now + 0.045);
  } catch (e) { }
}

export function toggleZoomBar(forceState) {
  const bar = $('lightboxZoomBar');
  if (!bar) return;
  if (forceState !== undefined) {
    bar.hidden = !forceState;
  } else {
    bar.hidden = !bar.hidden;
  }
}

export function setZoom(scale) {
  zoomScale = Math.min(Math.max(scale, 1), 3.5);
  if (zoomScale <= 1.02) {
    resetZoom();
  } else {
    updateImgTransform();
  }
}

export function updateImgTransform() {
  initLightboxSlider();
  if (!imgCurr) return;
  const zoomText = $('zoomLevelText');
  if (zoomText) zoomText.textContent = `${Math.round(zoomScale * 100)}%`;

  if (zoomScale <= 1.02) {
    zoomScale = 1;
    panX = 0;
    panY = 0;
    isPanning = false;
    imgCurr.style.transform = 'none';
    imgCurr.style.cursor = 'grab';
  } else {
    const maxPanX = (window.innerWidth * (zoomScale - 1)) / 1.8;
    const maxPanY = (window.innerHeight * (zoomScale - 1)) / 1.8;
    panX = Math.max(Math.min(panX, maxPanX), -maxPanX);
    panY = Math.max(Math.min(panY, maxPanY), -maxPanY);

    imgCurr.style.transform = `scale(${zoomScale}) translate3d(${panX / zoomScale}px, ${panY / zoomScale}px, 0)`;
    imgCurr.style.cursor = isPanning ? 'grabbing' : 'grab';
  }
}

export function resetZoom() {
  zoomScale = 1;
  panX = 0;
  panY = 0;
  isPanning = false;
  initLightboxSlider();
  if (imgCurr) {
    imgCurr.style.transform = 'none';
    imgCurr.style.cursor = 'grab';
  }
  const zoomText = $('zoomLevelText');
  if (zoomText) zoomText.textContent = '100%';
}

export function getPhotoEnc(item) {
  if (!item) return '';
  const p = typeof item === 'string' ? item : item.rel_path;
  return p.split('/').map(encodeURIComponent).join('/');
}

export function getPhotoFilename(item) {
  if (!item) return '';
  const p = typeof item === 'string' ? item : item.rel_path;
  return p.split('/').pop();
}

export function getTotalPhotosCount() {
  if (state.selectedCalendarDate) {
    const c = state.datePhotoCounts.get(state.selectedCalendarDate);
    return (c && c > 0) ? Math.max(c, state.currentPhotosList.length) : state.currentPhotosList.length;
  }
  if (state.selectedOtherAlbum) {
    const c = state.otherAlbumCounts.get(state.selectedOtherAlbum);
    return (c && c > 0) ? Math.max(c, state.currentPhotosList.length) : state.currentPhotosList.length;
  }
  if (typeof state.totalMatchingPhotosCount === 'number' && state.totalMatchingPhotosCount > 0) {
    return Math.max(state.totalMatchingPhotosCount, state.currentPhotosList.length);
  }
  return state.currentPhotosList.length;
}

export function updateLightboxHeader(index) {
  const total = getTotalPhotosCount();
  if (total <= 0) return;
  const normIndex = ((index % total) + total) % total;
  const item = state.currentPhotosList[normIndex];
  const filename = item ? getPhotoFilename(item) : `Photo ${normIndex + 1}`;
  const enc = item ? getPhotoEnc(item) : '';

  if (item) {
    trackGenuinePhotoView(item);
  }

  $('lightboxCounter').textContent = `${normIndex + 1} / ${total}`;
  $('lightboxFilename').textContent = filename;
  $('lightboxFilename').title = filename;

  const hdBtn = $('lightboxDownloadHdBtn');
  if (hdBtn) {
    if (enc) {
      hdBtn.href = `/download/hd/${enc}`;
      hdBtn.setAttribute('download', `${filename.replace(/\.[^/.]+$/, "")}_HD.jpg`);
      hdBtn.style.pointerEvents = '';
      hdBtn.style.opacity = '1';
    } else {
      hdBtn.removeAttribute('href');
      hdBtn.style.pointerEvents = 'none';
      hdBtn.style.opacity = '0.5';
    }
  }

  const btn4k = $('lightboxDownload4kBtn');
  if (btn4k) {
    if (enc) {
      btn4k.href = `/download/4k/${enc}`;
      btn4k.setAttribute('download', filename);
      btn4k.style.pointerEvents = '';
      btn4k.style.opacity = '1';
    } else {
      btn4k.removeAttribute('href');
      btn4k.style.pointerEvents = 'none';
      btn4k.style.opacity = '0.5';
    }
  }
}

const FILMSTRIP_WINDOW_SIZE = 9;

export function renderFilmstripWindow(centerIndex = state.currentPhotoIndex) {
  const track = $('lightboxFilmstripTrack');
  if (!track) return;

  const total = getTotalPhotosCount();
  if (total === 0) {
    track.replaceChildren();
    return;
  }

  const normCenter = ((centerIndex % total) + total) % total;
  const windowSize = Math.min(FILMSTRIP_WINDOW_SIZE, total);
  const half = Math.floor(windowSize / 2);

  const targetIndices = [];
  if (total >= FILMSTRIP_WINDOW_SIZE) {
    for (let offset = -half; offset <= half; offset++) {
      const idx = ((normCenter + offset) % total + total) % total;
      targetIndices.push(idx);
    }
  } else {
    for (let i = 0; i < total; i++) {
      targetIndices.push(i);
    }
  }

  while (track.children.length < targetIndices.length) {
    const thumb = document.createElement('div');
    thumb.className = 'filmstrip-thumb';
    const img = document.createElement('img');
    img.loading = 'eager';
    img.decoding = 'async';
    thumb.appendChild(img);

    thumb.addEventListener('click', (e) => {
      e.stopPropagation();
      const target = parseInt(thumb.dataset.index, 10);
      if (!isNaN(target) && target !== state.currentPhotoIndex) {
        jumpToPhoto(target);
      }
    });
    track.appendChild(thumb);
  }
  while (track.children.length > targetIndices.length) {
    track.removeChild(track.lastChild);
  }

  let needMorePhotos = false;

  for (let pos = 0; pos < targetIndices.length; pos++) {
    const photoIdx = targetIndices[pos];
    const thumb = track.children[pos];
    const img = thumb.firstElementChild;
    const item = state.currentPhotosList[photoIdx];

    thumb.dataset.index = photoIdx;
    const isActive = (photoIdx === normCenter);
    thumb.classList.toggle('active', isActive);
    thumb.setAttribute('aria-label', `Photo ${photoIdx + 1}${isActive ? ' (current)' : ''}`);

    if (item) {
      thumb.classList.remove('loading-placeholder');
      const p = typeof item === 'string' ? item : item.rel_path;
      const enc = p.split('/').map(encodeURIComponent).join('/');
      const thumbSrc = `/thumb/${enc}`;

      if (img.dataset.src !== thumbSrc) {
        img.dataset.src = thumbSrc;
        img.src = thumbSrc;
        img.alt = `Photo ${photoIdx + 1}`;
      }
    } else {
      needMorePhotos = true;
      thumb.classList.add('loading-placeholder');
      img.removeAttribute('src');
      img.dataset.src = '';
      img.alt = `Photo ${photoIdx + 1} (loading...)`;
    }
  }

  if (needMorePhotos || normCenter >= state.currentPhotosList.length - 15) {
    if (state.hasMoreSearchPages && !state.isFetchingNextSearchPage) {
      fetchNextSearchPage();
    }
  }
}

export function checkAndFetchMorePhotos(index) {
  const total = getTotalPhotosCount();
  const norm = ((index % total) + total) % total;
  if ((norm >= state.currentPhotosList.length - 15 || state.currentPhotosList.length < total) && state.hasMoreSearchPages && !state.isFetchingNextSearchPage) {
    fetchNextSearchPage();
  }
}

export function setSlideImage(imgEl, spinnerEl, src) {
  if (!imgEl) return;
  if (!src) {
    imgEl.src = '';
    if (spinnerEl) spinnerEl.classList.remove('visible');
    return;
  }
  if (imgEl.src && imgEl.src.endsWith(src)) {
    if (spinnerEl) spinnerEl.classList.remove('visible');
    return;
  }
  if (spinnerEl) spinnerEl.classList.add('visible');
  imgEl.onload = () => {
    if (spinnerEl) spinnerEl.classList.remove('visible');
  };
  imgEl.onerror = () => {
    if (spinnerEl) spinnerEl.classList.remove('visible');
  };
  imgEl.src = src;
}

export function updateSlideSlots(targetIdx) {
  initLightboxSlider();
  const total = getTotalPhotosCount();
  if (total === 0) return;

  const normTarget = ((targetIdx % total) + total) % total;
  const currItem = state.currentPhotosList[normTarget];
  if (currItem) {
    const currEnc = getPhotoEnc(currItem);
    setSlideImage(imgCurr, spinnerCurr, `/preview/${currEnc}`);
  } else {
    setSlideImage(imgCurr, spinnerCurr, '');
    if (spinnerCurr) spinnerCurr.classList.add('visible');
    if (state.hasMoreSearchPages && !state.isFetchingNextSearchPage) {
      fetchNextSearchPage();
    }
  }

  if (total > 1) {
    const prevIdx = ((normTarget - 1) % total + total) % total;
    const nextIdx = (normTarget + 1) % total;

    const prevItem = state.currentPhotosList[prevIdx];
    const nextItem = state.currentPhotosList[nextIdx];

    if (prevItem) {
      const prevEnc = getPhotoEnc(prevItem);
      setSlideImage(imgPrev, spinnerPrev, `/preview/${prevEnc}`);
    } else {
      setSlideImage(imgPrev, spinnerPrev, '');
    }

    if (nextItem) {
      const nextEnc = getPhotoEnc(nextItem);
      setSlideImage(imgNext, spinnerNext, `/preview/${nextEnc}`);
    } else {
      setSlideImage(imgNext, spinnerNext, '');
    }

    const prev2Idx = ((normTarget - 2) % total + total) % total;
    const next2Idx = (normTarget + 2) % total;
    if (state.currentPhotosList[prev2Idx]) {
      new Image().src = `/preview/${getPhotoEnc(state.currentPhotosList[prev2Idx])}`;
    }
    if (state.currentPhotosList[next2Idx]) {
      new Image().src = `/preview/${getPhotoEnc(state.currentPhotosList[next2Idx])}`;
    }
  } else {
    setSlideImage(imgPrev, spinnerPrev, '');
    setSlideImage(imgNext, spinnerNext, '');
  }
}

export function jumpToPhoto(targetIdx) {
  const total = getTotalPhotosCount();
  if (total === 0) return;
  const normalizedIdx = ((targetIdx % total) + total) % total;
  initLightboxSlider();
  state.currentPhotoIndex = normalizedIdx;
  resetZoom();

  if (sliderTrack) {
    sliderTrack.style.transition = 'none';
    sliderTrack.style.transform = 'translate3d(-100%, 0, 0)';
  }

  updateLightboxHeader(normalizedIdx);
  renderFilmstripWindow(normalizedIdx);
  checkAndFetchMorePhotos(normalizedIdx);

  if (imgCurr) {
    imgCurr.style.transition = 'opacity 0.15s ease';
    imgCurr.style.opacity = '0.4';
  }
  updateSlideSlots(normalizedIdx);
  setTimeout(() => {
    if (imgCurr) imgCurr.style.opacity = '1';
  }, 60);

  try {
    history.replaceState({ brkLightboxOpen: true, photoIndex: normalizedIdx }, '');
  } catch (e) {}
}

export function goToNextPhoto() {
  const total = getTotalPhotosCount();
  if (isTransitioning || total <= 1) return;
  initLightboxSlider();
  isTransitioning = true;
  resetZoom();

  if (sliderTrack) {
    sliderTrack.style.transition = 'transform 0.25s cubic-bezier(0.16, 1, 0.3, 1)';
    sliderTrack.style.transform = 'translate3d(-200%, 0, 0)';
  }
  playSwipeSound();

  setTimeout(() => {
    finishSlideTransition(1);
  }, 250);
}

export function goToPrevPhoto() {
  const total = getTotalPhotosCount();
  if (isTransitioning || total <= 1) return;
  initLightboxSlider();
  isTransitioning = true;
  resetZoom();

  if (sliderTrack) {
    sliderTrack.style.transition = 'transform 0.25s cubic-bezier(0.16, 1, 0.3, 1)';
    sliderTrack.style.transform = 'translate3d(0%, 0, 0)';
  }
  playSwipeSound();

  setTimeout(() => {
    finishSlideTransition(-1);
  }, 250);
}

export function finishSlideTransition(direction) {
  initLightboxSlider();
  const total = getTotalPhotosCount();
  if (total <= 0) {
    isTransitioning = false;
    return;
  }

  if (direction === 1) {
    state.currentPhotoIndex = (state.currentPhotoIndex + 1) % total;
  } else if (direction === -1) {
    state.currentPhotoIndex = ((state.currentPhotoIndex - 1) % total + total) % total;
  }

  updateLightboxHeader(state.currentPhotoIndex);
  renderFilmstripWindow(state.currentPhotoIndex);
  checkAndFetchMorePhotos(state.currentPhotoIndex);

  updateSlideSlots(state.currentPhotoIndex);

  if (sliderTrack) {
    sliderTrack.style.transition = 'none';
    sliderTrack.style.transform = 'translate3d(-100%, 0, 0)';
  }

  try {
    history.replaceState({ brkLightboxOpen: true, photoIndex: state.currentPhotoIndex }, '');
  } catch (e) {}

  isTransitioning = false;
}

export function openPhotoLightbox(index) {
  const total = getTotalPhotosCount();
  if (total === 0) return;
  state.currentPhotoIndex = ((index % total) + total) % total;
  resetZoom();

  const galleryBody = $('galleryScrollBody');
  if (galleryBody) {
    state.lastGalleryScrollTop = galleryBody.scrollTop;
  }

  initLightboxSlider();
  if (sliderTrack) {
    sliderTrack.style.transition = 'none';
    sliderTrack.style.transform = 'translate3d(-100%, 0, 0)';
  }

  updateLightboxHeader(state.currentPhotoIndex);
  renderFilmstripWindow(state.currentPhotoIndex);
  checkAndFetchMorePhotos(state.currentPhotoIndex);

  updateSlideSlots(state.currentPhotoIndex);

  const modal = $('photoLightboxModal');
  if (modal.hidden) {
    modal.hidden = false;
    requestAnimationFrame(() => {
      modal.classList.add('active');
    });

    try {
      history.pushState({ brkLightboxOpen: true, photoIndex: state.currentPhotoIndex }, '');
    } catch (e) {}
  } else {
    try {
      history.replaceState({ brkLightboxOpen: true, photoIndex: state.currentPhotoIndex }, '');
    } catch (e) {}
  }
}

export function closeLightbox(isFromPopState = false) {
  const modal = $('photoLightboxModal');
  if (!modal || (modal.hidden && !modal.classList.contains('active'))) return;

  modal.classList.remove('active');
  setTimeout(() => {
    modal.hidden = true;
    initLightboxSlider();
    if (imgCurr) imgCurr.src = '';
    if (imgPrev) imgPrev.src = '';
    if (imgNext) imgNext.src = '';
    resetZoom();
    toggleZoomBar(false);
  }, 260);

  if (!isFromPopState && window.history.state && window.history.state.brkLightboxOpen) {
    state.isClosingLightboxViaCode = true;
    try {
      window.history.back();
    } catch (e) {}
  }

  const galleryBody = $('galleryScrollBody');
  if (galleryBody && state.lastGalleryScrollTop > 0) {
    galleryBody.scrollTop = state.lastGalleryScrollTop;
  }

  const allCards = document.querySelectorAll('.photo-card');
  if (allCards && allCards[state.currentPhotoIndex]) {
    allCards[state.currentPhotoIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
    allCards[state.currentPhotoIndex].classList.add('focused-photo-pulse');
    setTimeout(() => {
      allCards[state.currentPhotoIndex]?.classList.remove('focused-photo-pulse');
    }, 1200);
  }
}
