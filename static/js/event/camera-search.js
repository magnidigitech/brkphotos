// In-browser selfie camera streaming, photo capture, search requests, and profile match caching
import { $, state, getSessionId, getVisitorId, getDeviceType } from './state.js';
import { renderPhotos, openGalleryOverlay, fetchNextSearchPage } from './gallery.js';

let mediaStream = null;
let cameraFacingMode = 'user';
let cameraCanvas = null;
let isSyncingProfileCount = false;

export function isCameraSupported() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

export async function openWebcamModal() {
  const modal = $('webcamModal');
  const video = $('webcamVideo');
  const previewImg = $('cameraPreviewImg');
  const captureControls = $('cameraCaptureControls');
  const previewControls = $('cameraPreviewControls');
  const faceGuide = $('cameraFaceGuide');
  const title = $('cameraModalTitle');
  const subtitle = $('cameraModalSubtitle');
  const flipBtn = $('flipCameraBtn');

  if (!modal || !video) return;

  if (!isCameraSupported() || (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1')) {
    console.warn('In-browser camera requires HTTPS or is unsupported; opening file upload fallback.');
    $('selfieFileInput').click();
    return;
  }

  if (previewImg) { previewImg.src = ''; previewImg.style.display = 'none'; }
  if (video) { video.style.display = 'block'; }
  if (faceGuide) { faceGuide.style.display = 'block'; }
  if (flipBtn) { flipBtn.style.display = 'flex'; }
  if (captureControls) { captureControls.style.display = 'flex'; }
  if (previewControls) { previewControls.style.display = 'none'; }
  if (title) { title.textContent = 'Take a Selfie'; }
  if (subtitle) { subtitle.textContent = 'Position your face inside the frame and tap Capture'; }

  modal.hidden = false;
  document.body.style.overflow = 'hidden';

  await startCameraStream();
}

export async function startCameraStream() {
  const video = $('webcamVideo');
  if (!video) return;

  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }

  video.style.transform = cameraFacingMode === 'user' ? 'scaleX(-1)' : 'scaleX(1)';

  try {
    const constraints = {
      video: {
        facingMode: cameraFacingMode,
        width: { ideal: 1280 },
        height: { ideal: 960 }
      },
      audio: false
    };
    mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
    video.srcObject = mediaStream;
    await video.play();
  } catch (err) {
    console.warn('Camera stream error:', err);
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      video.srcObject = mediaStream;
      await video.play();
    } catch (fallbackErr) {
      console.error('All camera attempts failed:', fallbackErr);
      stopWebcam();
      const statusEl = $('searchStatus');
      if (statusEl) {
        statusEl.innerHTML = `<span style="color:#d97706;font-weight:600;">Camera access was blocked or is unavailable. Please upload a photo instead.</span>`;
      }
      $('selfieFileInput').click();
    }
  }
}

export async function toggleCameraFlip() {
  cameraFacingMode = cameraFacingMode === 'user' ? 'environment' : 'user';
  await startCameraStream();
}

export function captureWebcamPhoto() {
  const video = $('webcamVideo');
  const previewImg = $('cameraPreviewImg');
  const captureControls = $('cameraCaptureControls');
  const previewControls = $('cameraPreviewControls');
  const faceGuide = $('cameraFaceGuide');
  const flipBtn = $('flipCameraBtn');
  const title = $('cameraModalTitle');
  const subtitle = $('cameraModalSubtitle');

  if (!video || !video.videoWidth) return;

  if (!cameraCanvas) {
    cameraCanvas = document.createElement('canvas');
  }
  cameraCanvas.width = video.videoWidth;
  cameraCanvas.height = video.videoHeight;
  const ctx = cameraCanvas.getContext('2d');

  if (cameraFacingMode === 'user') {
    ctx.translate(cameraCanvas.width, 0);
    ctx.scale(-1, 1);
  }
  ctx.drawImage(video, 0, 0, cameraCanvas.width, cameraCanvas.height);

  const previewDataUrl = cameraCanvas.toDataURL('image/jpeg', 0.92);
  if (previewImg) {
    previewImg.src = previewDataUrl;
    previewImg.style.display = 'block';
  }
  video.style.display = 'none';
  if (faceGuide) faceGuide.style.display = 'none';
  if (flipBtn) flipBtn.style.display = 'none';
  if (captureControls) captureControls.style.display = 'none';
  if (previewControls) previewControls.style.display = 'flex';
  if (title) title.textContent = 'Preview Selfie';
  if (subtitle) subtitle.textContent = 'Is your face clear? Tap Use Photo to search or Retake';
}

export function retakeWebcamPhoto() {
  const video = $('webcamVideo');
  const previewImg = $('cameraPreviewImg');
  const captureControls = $('cameraCaptureControls');
  const previewControls = $('cameraPreviewControls');
  const faceGuide = $('cameraFaceGuide');
  const flipBtn = $('flipCameraBtn');
  const title = $('cameraModalTitle');
  const subtitle = $('cameraModalSubtitle');

  if (previewImg) { previewImg.src = ''; previewImg.style.display = 'none'; }
  if (video) { video.style.display = 'block'; }
  if (faceGuide) faceGuide.style.display = 'block';
  if (flipBtn) flipBtn.style.display = 'flex';
  if (captureControls) captureControls.style.display = 'flex';
  if (previewControls) previewControls.style.display = 'none';
  if (title) title.textContent = 'Take a Selfie';
  if (subtitle) subtitle.textContent = 'Position your face inside the frame and tap Capture';
}

export async function useCapturedPhoto() {
  if (!cameraCanvas) return;
  const useBtn = $('usePhotoBtn');
  const originalText = useBtn ? useBtn.innerHTML : '';
  if (useBtn) {
    useBtn.disabled = true;
    useBtn.innerHTML = `<span>Processing...</span>`;
  }

  try {
    const blob = await new Promise((resolve, reject) => {
      cameraCanvas.toBlob(b => {
        if (b) resolve(b);
        else reject(new Error('Failed to create photo image.'));
      }, 'image/jpeg', 0.92);
    });

    if (!blob || blob.size === 0) {
      throw new Error('Captured photo was empty. Please retake.');
    }

    const file = new File([blob], 'selfie.jpg', { type: 'image/jpeg', lastModified: Date.now() });
    stopWebcam();
    await processAndSearchSelfie(file);
  } catch (err) {
    console.error('Error in useCapturedPhoto:', err);
    alert(err.message || 'Could not process photo. Please try again.');
    retakeWebcamPhoto();
  } finally {
    if (useBtn) {
      useBtn.disabled = false;
      useBtn.innerHTML = originalText;
    }
  }
}

export function stopWebcam() {
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  const modal = $('webcamModal');
  if (modal) modal.hidden = true;
  document.body.style.overflow = '';
}

export function triggerCamera() {
  if (isCameraSupported()) {
    openWebcamModal();
  } else {
    $('selfieFileInput').click();
  }
}

export function checkSavedProfile() {
  const badge = $('savedProfileBadge');
  if (!badge) return;

  const savedThumb = localStorage.getItem('brk_saved_face_thumb');
  const savedPhotosJson = localStorage.getItem('brk_saved_photos');
  const savedCount = parseInt(localStorage.getItem('brk_saved_count') || '0', 10);
  const acknowledgedCount = parseInt(localStorage.getItem('brk_acknowledged_count') || `${savedCount}`, 10);

  if (savedThumb && savedPhotosJson && savedCount > 0) {
    try {
      const photos = JSON.parse(savedPhotosJson);
      if (Array.isArray(photos) && photos.length > 0) {
        const avatarImg = $('savedAvatarImg');
        if (avatarImg) avatarImg.src = savedThumb;
        const countText = $('savedPhotosCountText');
        if (countText) {
          countText.textContent = `Found ${savedCount} photo${savedCount === 1 ? '' : 's'} with BRK Sir`;
        }
        const newBadge = $('newBadgeIndicator');
        if (newBadge) {
          newBadge.hidden = !(savedCount > acknowledgedCount);
        }
        badge.hidden = false;
        return;
      }
    } catch (e) {
      console.warn('Error reading saved photos from localStorage:', e);
    }
  }
  badge.hidden = true;
}

export async function syncLatestProfileCount() {
  if (isSyncingProfileCount) return;
  const savedThumb = localStorage.getItem('brk_saved_face_thumb');
  const savedEmbJson = localStorage.getItem('brk_saved_embedding');
  let savedEmb = null;
  if (savedEmbJson) {
    try { savedEmb = JSON.parse(savedEmbJson); } catch (e) {}
  }
  if (!savedThumb && !savedEmb) return;

  const lastCount = parseInt(localStorage.getItem('brk_saved_count') || '0', 10);
  const acknowledgedCount = parseInt(localStorage.getItem('brk_acknowledged_count') || `${lastCount}`, 10);

  isSyncingProfileCount = true;
  try {
    const payload = {
      face_thumb: savedThumb,
      embedding: savedEmb,
      last_count: lastCount,
      page: 1,
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
    if (res.ok) {
      const data = await res.json();
      if (data.search_id) {
        state.currentSearchId = data.search_id;
        try { localStorage.setItem('brk_current_search_id', data.search_id); } catch (e) {}
      }
      if (data.creation_token) {
        state.currentCreationToken = data.creation_token;
        try { localStorage.setItem('brk_current_creation_token', data.creation_token); } catch (e) {}
      }
      if (data.embedding && !savedEmb) {
        try { localStorage.setItem('brk_saved_embedding', JSON.stringify(data.embedding)); } catch (e) {}
      }
      const newCount = (typeof data.total_count === 'number' && data.total_count > 0) ? data.total_count : data.count;
      if (typeof newCount === 'number' && newCount > 0) {
        localStorage.setItem('brk_saved_count', newCount);
        if (data.date_summary) {
          state.serverDateSummary = data.date_summary;
          try {
            localStorage.setItem('brk_saved_date_summary', JSON.stringify(data.date_summary));
          } catch (e) {}
        }
        if (data.photos && data.photos.length > 0) {
          localStorage.setItem('brk_saved_photos', JSON.stringify(data.photos));
        }

        const countText = $('savedPhotosCountText');
        if (countText) {
          countText.textContent = `Found ${newCount} photo${newCount === 1 ? '' : 's'} with BRK Sir`;
        }

        const newBadge = $('newBadgeIndicator');
        if (newBadge) {
          newBadge.hidden = !(newCount > acknowledgedCount);
        }

        if (newCount > acknowledgedCount) {
          const diff = newCount - acknowledgedCount;
          const banner = $('newPhotosAlertBanner');
          const alertText = $('newPhotosAlertText');
          if (banner && alertText) {
            alertText.textContent = `🎉 ${diff} new photo${diff === 1 ? '' : 's'} found with BRK Sir since your last visit!`;
            banner.hidden = false;
          }
        }
      }
    }
  } catch (err) {
    // Quiet background check
  } finally {
    isSyncingProfileCount = false;
  }
}

export function viewSavedPhotos(e) {
  if (e) e.preventDefault();
  const savedPhotosJson = localStorage.getItem('brk_saved_photos');
  const savedCount = parseInt(localStorage.getItem('brk_saved_count') || '0', 10);
  const savedThumb = localStorage.getItem('brk_saved_face_thumb');
  if (!savedPhotosJson) return;

  localStorage.setItem('brk_acknowledged_count', savedCount);
  const newBadge = $('newBadgeIndicator');
  if (newBadge) newBadge.hidden = true;
  const banner = $('newPhotosAlertBanner');
  if (banner) banner.hidden = true;

  try {
    const photos = JSON.parse(savedPhotosJson);
    if (Array.isArray(photos) && photos.length > 0) {
      const actualCount = savedCount > 0 ? savedCount : photos.length;
      state.totalMatchingPhotosCount = actualCount;
      state.currentSearchFaceThumb = savedThumb || null;
      const savedEmbJson = localStorage.getItem('brk_saved_embedding');
      if (savedEmbJson) {
        try { state.currentSearchEmbedding = JSON.parse(savedEmbJson); } catch (e) {}
      }
      state.currentSearchId = localStorage.getItem('brk_current_search_id') || null;
      state.currentCreationToken = localStorage.getItem('brk_current_creation_token') || null;
      state.currentSearchPage = Math.max(1, Math.floor(photos.length / 50));
      state.hasMoreSearchPages = actualCount > photos.length;

      const savedDateSummaryJson = localStorage.getItem('brk_saved_date_summary');
      if (savedDateSummaryJson) {
        try {
          state.serverDateSummary = JSON.parse(savedDateSummaryJson);
        } catch (e) {}
      }

      const statusEl = $('searchStatus');
      if (statusEl) {
        statusEl.innerHTML = `<span style="color:var(--emerald,#059669);font-weight:700;">✓ Restored ${actualCount} saved photo${actualCount === 1 ? '' : 's'} with BRK Sir!</span>`;
      }
      $('photosSectionTitle').textContent = 'Photos with BRK Sir';
      $('photosSectionSubtitle').textContent = actualCount === 1 ? '1 photograph found' : `${actualCount} photographs found`;
      renderPhotos(photos);
      openGalleryOverlay();
      if (state.hasMoreSearchPages) {
        fetchNextSearchPage();
      }
    }
  } catch (err) {
    console.error('Error rendering saved photos from storage:', err);
  }
}

export function clearSavedProfile(e) {
  if (e) {
    e.preventDefault();
    e.stopPropagation();
  }
  localStorage.removeItem('brk_saved_face_thumb');
  localStorage.removeItem('brk_saved_embedding');
  localStorage.removeItem('brk_saved_photos');
  localStorage.removeItem('brk_saved_count');
  localStorage.removeItem('brk_acknowledged_count');
  localStorage.removeItem('brk_saved_time');
  localStorage.removeItem('brk_current_search_id');
  localStorage.removeItem('brk_current_creation_token');
  state.currentSearchFaceThumb = null;
  state.currentSearchEmbedding = null;
  state.currentSearchId = null;
  state.currentCreationToken = null;
  state.currentSelectedFaceIndex = null;
  state.hasMoreSearchPages = false;
  state.totalMatchingPhotosCount = 0;
  const badge = $('savedProfileBadge');
  if (badge) badge.hidden = true;
}

export function showMultiFacePicker(faces) {
  const picker = $('multiFacePicker');
  const list = $('multiFaceList');
  const actionsRow = $('finderActionsRow');
  if (!picker || !list) return;

  const allBtn = $('searchAllFacesBtn');
  if (allBtn) {
    const textSpan = $('searchAllFacesBtnText');
    if (textSpan) textSpan.textContent = `Search for All ${faces.length} People`;
    allBtn.onclick = () => {
      if (state.currentSearchFile) {
        processAndSearchSelfie(state.currentSearchFile, 'all');
      }
    };
  }

  list.replaceChildren();
  faces.forEach((f, idx) => {
    const item = document.createElement('div');
    item.className = 'multi-face-item';
    item.title = `Search photos for Person ${idx + 1}`;
    item.innerHTML = `
      <img src="${f.thumb || ''}" class="multi-face-avatar" alt="Person ${idx + 1}">
      <span class="multi-face-btn-label">Person ${idx + 1}</span>
    `;
    item.addEventListener('click', () => {
      picker.querySelectorAll('.multi-face-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      if (state.currentSearchFile) {
        processAndSearchSelfie(state.currentSearchFile, f.face_index);
      }
    });
    list.appendChild(item);
  });

  if (actionsRow) actionsRow.hidden = true;
  picker.hidden = false;
  picker.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

export function hideMultiFacePicker() {
  const picker = $('multiFacePicker');
  const actionsRow = $('finderActionsRow');
  if (picker) picker.hidden = true;
  if (actionsRow) actionsRow.hidden = false;
}

export async function processAndSearchSelfie(file, faceIndex = null) {
  if (!file) return;
  state.currentSearchFile = file;

  const statusEl = $('searchStatus');
  if (statusEl) {
    const text = faceIndex === 'all'
      ? 'Searching photographs for all detected people with BRK Sir...'
      : (faceIndex !== null
        ? 'Searching photographs for selected person with BRK Sir...'
        : 'Analyzing faces with AI and searching photographs with BRK Sir...');
    statusEl.innerHTML = `<span style="display:inline-flex;align-items:center;gap:8px;color:var(--accent,#0f5c4d);font-weight:600;">
      <svg class="icon-spin" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
      ${text}
    </span>`;
    statusEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  const fd = new FormData();
  fd.append('selfie', file);
  if (faceIndex !== null && faceIndex !== undefined) {
    fd.append('face_index', String(faceIndex));
  }
  fd.append('page', 1);
  fd.append('page_size', 50);
  fd.append('session_id', getSessionId());
  fd.append('visitor_id', getVisitorId());
  fd.append('device_type', getDeviceType());

  try {
    let res = await fetch('/api/search', { method: 'POST', body: fd });
    if (!res.ok && res.status !== 400 && res.status !== 413 && res.status !== 422) {
      res = await fetch(`/api/${state.slug}/search`, { method: 'POST', body: fd });
    }
    let j;
    try {
      j = await res.json();
    } catch (_) {
      const txt = await res.text().catch(() => '');
      j = { detail: txt || 'Search could not be completed.' };
    }
    if (!res.ok) throw new Error(j.detail || 'Search could not be completed.');

    if (j.multiple_faces === true && j.faces && j.faces.length > 1) {
      if (statusEl) {
        statusEl.innerHTML = `<span style="color:#0f5c4d;font-weight:700;">👥 Found ${j.total_faces} people in this photo. Search for all people together or pick an individual:</span>`;
      }
      showMultiFacePicker(j.faces);
      return;
    }

    hideMultiFacePicker();

    if (j.photos && j.photos.length > 0) {
      const totalMatches = (typeof j.total_count === 'number' && j.total_count > 0)
        ? j.total_count
        : (typeof j.count === 'number' && j.count > 0 ? j.count : j.photos.length);
      state.totalMatchingPhotosCount = totalMatches;

      if (j.date_summary) {
        state.serverDateSummary = j.date_summary;
        try {
          localStorage.setItem('brk_saved_date_summary', JSON.stringify(j.date_summary));
        } catch (e) {}
      }

      state.currentSearchFaceThumb = j.face_thumb || null;
      state.currentSearchEmbedding = j.embedding || null;
      state.currentSearchId = j.search_id || null;
      state.currentCreationToken = j.creation_token || null;
      if (state.currentSearchId) {
        try { localStorage.setItem('brk_current_search_id', state.currentSearchId); } catch(e){}
      }
      if (state.currentCreationToken) {
        try { localStorage.setItem('brk_current_creation_token', state.currentCreationToken); } catch(e){}
      }
      state.currentSelectedFaceIndex = faceIndex;
      state.currentSearchPage = j.page || 1;
      state.hasMoreSearchPages = !!j.has_more;

      try {
        if (j.face_thumb) {
          localStorage.setItem('brk_saved_face_thumb', j.face_thumb);
        }
        if (j.embedding) {
          localStorage.setItem('brk_saved_embedding', JSON.stringify(j.embedding));
        }
        localStorage.setItem('brk_saved_photos', JSON.stringify(j.photos));
        localStorage.setItem('brk_saved_count', totalMatches);
        localStorage.setItem('brk_acknowledged_count', totalMatches);
        localStorage.setItem('brk_saved_time', new Date().toISOString());
        checkSavedProfile();
      } catch (e) {
        console.warn('LocalStorage save failed:', e);
      }

      if (statusEl) {
        const groupSuffix = j.is_group_search ? ` featuring any of the ${j.total_faces_in_query || 'group'} people` : '';
        statusEl.innerHTML = `<span style="color:var(--emerald,#059669);font-weight:700;">✓ Found ${totalMatches} photo${totalMatches === 1 ? '' : 's'}${groupSuffix} with BRK Sir!</span>`;
      }
      if ($('emptyResultsCard')) $('emptyResultsCard').hidden = true;
      $('photosSectionTitle').textContent = 'Photos with BRK Sir';
      const subText = j.is_group_search
        ? `${totalMatches} photographs found featuring any of the ${j.total_faces_in_query || 'group'} people`
        : (totalMatches === 1 ? '1 photograph found featuring your face' : `${totalMatches} unique photographs found featuring your face`);
      $('photosSectionSubtitle').textContent = subText;
      renderPhotos(j.photos);
      openGalleryOverlay();
      if (j.has_more) {
        fetchNextSearchPage();
      }
    } else {
      if (statusEl) {
        statusEl.innerHTML = `<span style="color:#d97706;font-weight:600;">No matching photos found with your face. See tips below:</span>`;
      }
      $('photosSectionTitle').textContent = 'Search Results';
      $('photosSectionSubtitle').textContent = '0 matching photographs found';
      $('photosGrid').replaceChildren();
      if ($('discoveryToolbar')) $('discoveryToolbar').hidden = true;
      if ($('emptyResultsCard')) $('emptyResultsCard').hidden = false;
      openGalleryOverlay();
    }
  } catch (err) {
    if (statusEl) {
      statusEl.innerHTML = `<span style="color:#dc2626;font-weight:600;">${err.message || 'Error searching photos. Please try again.'}</span>`;
    }
  }
}
