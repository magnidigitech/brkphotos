// Photo and bulk ZIP download handling
import { $, state, getSessionId, getVisitorId, showToast } from './state.js';

export async function downloadSelectedZip() {
  if (state.selectedPhotos.size === 0) {
    showToast('Please select at least 1 photo.');
    return;
  }

  const zipText = $('bulkDownloadZipText');
  const originalText = zipText ? zipText.textContent : 'Download ZIP';
  if (zipText) zipText.textContent = 'Preparing ZIP...';

  try {
    const payload = {
      photos: Array.from(state.selectedPhotos),
      quality: 'original',
      session_id: getSessionId(),
      visitor_id: getVisitorId()
    };
    const res = await fetch('/api/download/bulk-zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Could not create ZIP archive.');
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = url;
    a.download = `BRK_Photos_${state.selectedPhotos.size}_Original_Photos.zip`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      window.URL.revokeObjectURL(url);
      a.remove();
    }, 1500);

    showToast(`Downloaded ${state.selectedPhotos.size} photos as ZIP!`);
  } catch (err) {
    showToast(err.message || 'Error downloading ZIP archive.');
  } finally {
    if (zipText) zipText.textContent = originalText;
  }
}

export function downloadSinglePhoto(photoUrl, filename) {
  if (!photoUrl) return;
  const a = document.createElement('a');
  a.href = photoUrl;
  a.download = filename || 'photo.jpg';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 500);
}
