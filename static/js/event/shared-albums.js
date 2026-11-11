// My Event Album creation, idempotent credentials, My Albums drawer, and secure link sharing
import { $, state, getSessionId, getVisitorId, showToast, escapeHtml } from './state.js';
import { clearSelection } from './selection.js';
import { getPhotoEnc } from './viewer.js';

export function updateMyAlbumsBadge() {
  const badge = $('myAlbumsBadge');
  const navBadge = $('navAlbumsBadge');
  try {
    const albums = JSON.parse(localStorage.getItem('my_shared_albums') || '[]');
    const activeCount = albums.filter(a => !a.is_revoked && (!a.expires_at || new Date(a.expires_at) > new Date())).length;
    if (badge) badge.style.display = activeCount > 0 ? 'inline-block' : 'none';
    if (navBadge) navBadge.style.display = activeCount > 0 ? 'inline-block' : 'none';
  } catch (e) {
    if (badge) badge.style.display = 'none';
    if (navBadge) navBadge.style.display = 'none';
  }
}

export function generate256BitHexToken(prefix = '') {
  const arr = new Uint8Array(32);
  if (window.crypto && window.crypto.getRandomValues) {
    window.crypto.getRandomValues(arr);
  } else {
    for (let i = 0; i < 32; i++) arr[i] = Math.floor(Math.random() * 256);
  }
  const hex = Array.from(arr, b => b.toString(16).padStart(2, '0')).join('');
  return prefix ? (prefix + hex) : hex;
}

export async function openCreateAlbumModal() {
  if (state.selectedPhotos.size === 0) {
    showToast('Please select at least one photograph to create an album.');
    return;
  }
  if (state.selectedPhotos.size > 100) {
    showToast('You can select up to 100 photographs for an album.');
    return;
  }

  let sId = state.currentSearchId || localStorage.getItem('brk_current_search_id');
  let cTok = state.currentCreationToken || localStorage.getItem('brk_current_creation_token');

  if (!sId || !cTok) {
    const savedThumb = localStorage.getItem('brk_saved_face_thumb');
    const savedEmbJson = localStorage.getItem('brk_saved_embedding');
    let savedEmb = null;
    if (savedEmbJson) {
      try { savedEmb = JSON.parse(savedEmbJson); } catch (e) {}
    }

    if (savedThumb || savedEmb) {
      const albumBtn = $('bulkCreateAlbumBtn');
      const albumText = $('bulkCreateAlbumText');
      const origText = albumText ? albumText.textContent : 'Create Album';
      if (albumText) albumText.textContent = 'Verifying...';
      if (albumBtn) albumBtn.disabled = true;

      try {
        const renewRes = await fetch('/api/check-profile-matches', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            face_thumb: savedThumb,
            embedding: savedEmb,
            session_id: getSessionId(),
            visitor_id: getVisitorId(),
            page: 1,
            page_size: 50
          })
        });
        if (renewRes.ok) {
          const renewData = await renewRes.json();
          if (renewData.search_id && renewData.creation_token) {
            state.currentSearchId = renewData.search_id;
            state.currentCreationToken = renewData.creation_token;
            try {
              localStorage.setItem('brk_current_search_id', state.currentSearchId);
              localStorage.setItem('brk_current_creation_token', state.currentCreationToken);
            } catch (e) {}
            sId = state.currentSearchId;
            cTok = state.currentCreationToken;
          }
        }
      } catch (e) {
        console.warn('Could not auto-renew search proof:', e);
      } finally {
        if (albumBtn) albumBtn.disabled = false;
        if (albumText) albumText.textContent = origText;
      }
    }
  }

  if (!sId || !cTok) {
    showToast('Search proof expired. Please find photos with your selfie to re-verify your album.');
    return;
  }

  const errAlert = $('albumCreateErrorAlert');
  if (errAlert) {
    errAlert.textContent = '';
    errAlert.style.display = 'none';
  }

  const countEl = $('albumModalSelectedCount');
  if (countEl) countEl.textContent = `Selected Photos: ${state.selectedPhotos.size}`;

  const strip = $('albumModalThumbStrip');
  if (strip) {
    strip.replaceChildren();
    const allItems = (state.currentPhotosList && state.currentPhotosList.length > 0)
      ? state.currentPhotosList
      : (state.rawPhotosList || []);

    const selectedArray = Array.from(state.selectedPhotos);
    selectedArray.slice(0, 20).forEach(path => {
      const item = allItems.find(it => (typeof it === 'string' ? it : it.rel_path) === path);
      const enc = item ? getPhotoEnc(item) : encodeURIComponent(path);
      const img = document.createElement('img');
      img.src = `/thumb/${enc}`;
      img.alt = 'Selected photo';
      img.style.cssText = 'width:56px;height:56px;object-fit:cover;border-radius:8px;border:1px solid rgba(255,255,255,0.1);flex-shrink:0;';
      strip.appendChild(img);
    });

    if (selectedArray.length > 20) {
      const more = document.createElement('div');
      more.style.cssText = 'width:56px;height:56px;border-radius:8px;background:rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:center;font-size:0.75rem;font-weight:700;color:var(--muted,#9ca3af);flex-shrink:0;';
      more.textContent = `+${selectedArray.length - 20}`;
      strip.appendChild(more);
    }
  }

  const titleInput = $('inputAlbumTitle');
  if (titleInput) {
    const evTitle = $('heroTitle')?.textContent?.trim() || 'BRK Sir';
    titleInput.value = `${evTitle} — My Moments`;
  }

  const expirySelect = $('selectAlbumExpiry');
  if (expirySelect) expirySelect.value = '30';

  const modal = $('createAlbumModalBackdrop');
  if (modal) {
    modal.classList.add('active');
    modal.style.display = 'flex';
  }
}

export function closeCreateAlbumModal() {
  const modal = $('createAlbumModalBackdrop');
  if (modal) {
    modal.classList.remove('active');
    modal.style.display = 'none';
  }
}

export async function confirmCreateAlbum() {
  const sId = state.currentSearchId || localStorage.getItem('brk_current_search_id');
  const cTok = state.currentCreationToken || localStorage.getItem('brk_current_creation_token');

  if (!sId || !cTok) {
    showToast('Search session proof missing. Please search again with your selfie.');
    return;
  }

  const allItems = (state.currentPhotosList && state.currentPhotosList.length > 0)
    ? state.currentPhotosList
    : (state.rawPhotosList || []);

  const photoIds = [];
  for (const path of state.selectedPhotos) {
    const item = allItems.find(it => (typeof it === 'string' ? it : it.rel_path) === path);
    if (item && item.id) {
      photoIds.push(item.id);
    }
  }

  if (photoIds.length === 0) {
    showToast('Could not resolve photo IDs. Please ensure photos are loaded and try again.');
    return;
  }

  const title = $('inputAlbumTitle')?.value?.trim() || 'My Event Album';
  const expiryDays = parseInt($('selectAlbumExpiry')?.value || '30', 10);

  const confirmBtn = $('btnConfirmCreateAlbum');
  const origText = confirmBtn ? confirmBtn.innerHTML : '';
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<svg class="icon-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><span>Creating...</span>';
  }

  const pendingKey = `brk_pending_album_${sId}`;
  let clientShareToken = null;
  let clientManageToken = null;
  try {
    const cached = JSON.parse(localStorage.getItem(pendingKey) || '{}');
    const sortedPhotoIds = photoIds.slice().sort((a,b)=>a-b).join(',');
    if (cached && cached.photo_ids_str === sortedPhotoIds && cached.share_token && cached.manage_token) {
      clientShareToken = cached.share_token;
      clientManageToken = cached.manage_token;
    }
  } catch (e) {}

  if (!clientShareToken || !clientManageToken) {
    clientShareToken = generate256BitHexToken('sh_');
    clientManageToken = generate256BitHexToken('mg_');
    try {
      localStorage.setItem(pendingKey, JSON.stringify({
        search_id: sId,
        photo_ids_str: photoIds.slice().sort((a,b)=>a-b).join(','),
        share_token: clientShareToken,
        manage_token: clientManageToken,
        title: title,
        expiry_days: expiryDays,
        timestamp: Date.now()
      }));
    } catch (e) {}
  }

  try {
    const payload = {
      search_id: sId,
      creation_token: cTok,
      photo_ids: photoIds,
      title: title,
      expiry_days: expiryDays,
      share_token: clientShareToken,
      manage_token: clientManageToken
    };

    const res = await fetch('/api/shared-albums', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Session-Id': getSessionId()
      },
      body: JSON.stringify(payload)
    });

    let j;
    try {
      j = await res.json();
    } catch (_) {
      const txt = await res.text().catch(() => '');
      j = { detail: txt || 'Album creation failed.' };
    }
    if (!res.ok) {
      throw new Error(j.detail || 'Album creation failed.');
    }

    try {
      localStorage.removeItem(pendingKey);
    } catch (e) {}

    closeCreateAlbumModal();

    const fullShareUrl = window.location.origin + (j.share_url || `/s/${j.share_token}`);
    const savedAlbums = JSON.parse(localStorage.getItem('my_shared_albums') || '[]');
    savedAlbums.unshift({
      id: j.id,
      title: j.title || title,
      share_token: j.share_token,
      manage_token: j.manage_token,
      share_url: fullShareUrl,
      photo_count: j.photo_count || photoIds.length,
      created_at: j.created_at || new Date().toISOString(),
      expires_at: j.expires_at,
      is_revoked: false
    });
    localStorage.setItem('my_shared_albums', JSON.stringify(savedAlbums));
    updateMyAlbumsBadge();

    clearSelection();

    const linkInput = $('successShareLinkInput');
    if (linkInput) linkInput.value = fullShareUrl;

    const compactText = $('compactShareLinkText');
    if (compactText) {
      compactText.textContent = getCompactDisplayUrl(fullShareUrl);
    }
    const compactPill = $('compactShareLinkDisplay');
    if (compactPill) {
      compactPill.setAttribute('data-full-url', fullShareUrl);
      compactPill.title = `Click to copy full link:\n${fullShareUrl}`;
    }

    const waBtn = $('btnWhatsAppShare');
    if (waBtn) {
      const waText = encodeURIComponent(`Here is my private photo album "${j.title || title}":\n${fullShareUrl}`);
      waBtn.href = `https://api.whatsapp.com/send?text=${waText}`;
    }

    const subTitle = $('successAlbumSubtitle');
    if (subTitle) {
      subTitle.textContent = `${photoIds.length} photograph${photoIds.length === 1 ? '' : 's'} included. Link expires in ${expiryDays} days.`;
    }

    const succModal = $('albumSuccessModalBackdrop');
    if (succModal) {
      succModal.classList.add('active');
      succModal.style.display = 'flex';
    }
    showToast('Private album created successfully!');
  } catch (err) {
    const errAlert = $('albumCreateErrorAlert');
    if (errAlert) {
      errAlert.textContent = err.message || 'Album creation failed.';
      errAlert.style.display = 'block';
    }
    showToast(`Failed to create album: ${err.message}`);
  } finally {
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.innerHTML = origText;
    }
  }
}

export function getCompactDisplayUrl(fullUrl) {
  if (!fullUrl) return 'photos.bhashyamramakrishna.in/s/…';
  try {
    const u = new URL(fullUrl, window.location.origin);
    return `${u.host}/s/…`;
  } catch (e) {
    return 'photos.bhashyamramakrishna.in/s/…';
  }
}

export function closeSuccessModal() {
  const modal = $('albumSuccessModalBackdrop');
  if (modal) {
    modal.classList.remove('active');
    modal.style.display = 'none';
  }
}

export function openMyAlbumsModal() {
  renderMyAlbumsList();
  const modal = $('myAlbumsModalBackdrop');
  if (modal) {
    modal.classList.add('active');
    modal.style.display = 'flex';
  }
}

export function closeMyAlbumsModal() {
  const modal = $('myAlbumsModalBackdrop');
  if (modal) {
    modal.classList.remove('active');
    modal.style.display = 'none';
  }
}

export function renderMyAlbumsList() {
  const container = $('myAlbumsListContainer');
  if (!container) return;
  container.replaceChildren();

  let albums = [];
  try {
    albums = JSON.parse(localStorage.getItem('my_shared_albums') || '[]');
  } catch (e) {
    albums = [];
  }

  if (albums.length === 0) {
    container.innerHTML = `
      <div style="text-align:center;padding:2.5rem 1rem;color:#64748b;">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="1.8" style="margin:0 auto 0.75rem;"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
        <p style="font-weight:700;font-size:1rem;color:#0f172a;margin-bottom:0.25rem;">No albums created yet</p>
        <p style="font-size:0.82rem;color:#64748b;margin:0;">Select photos from your face search results and click "Create Album" to share them privately.</p>
      </div>
    `;
    return;
  }

  albums.forEach((album, idx) => {
    const isExpired = album.expires_at && new Date(album.expires_at) < new Date();
    const isRevoked = !!album.is_revoked;
    const statusText = isRevoked ? 'Revoked' : (isExpired ? 'Expired' : 'Active');
    const statusBg = isRevoked ? '#fee2e2' : (isExpired ? '#fef3c7' : '#dcfce7');
    const statusColor = isRevoked ? '#b91c1c' : (isExpired ? '#b45309' : '#15803d');
    const statusBorder = isRevoked ? '#fecaca' : (isExpired ? '#fde68a' : '#bbf7d0');
    const compactUrl = getCompactDisplayUrl(album.share_url);

    const card = document.createElement('div');
    card.style.cssText = 'background:#f8fafc;border:1.5px solid #e2e8f0;border-radius:12px;padding:0.95rem;display:flex;flex-direction:column;gap:0.75rem;box-shadow:0 1px 3px rgba(0,0,0,0.02);';
    card.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:0.5rem;">
        <div>
          <h4 style="font-size:1.05rem;font-weight:700;margin:0 0 0.25rem;color:#0f172a;line-height:1.3;">${escapeHtml(album.title || 'My Event Album')}</h4>
          <p style="font-size:0.8rem;color:#64748b;margin:0;">
            ${album.photo_count || 0} photos • Created ${album.created_at ? new Date(album.created_at).toLocaleDateString() : ''}
          </p>
        </div>
        <span style="display:inline-block;padding:3px 12px;border-radius:9999px;font-size:0.75rem;font-weight:700;background:${statusBg};color:${statusColor};border:1px solid ${statusBorder};white-space:nowrap;">
          ${statusText}
        </span>
      </div>

      <div style="display:flex;flex-direction:column;gap:0.6rem;">
        ${(!isRevoked && !isExpired) ? `
          <div class="compact-link-pill btn-copy-album-link" data-url="${album.share_url}"
               style="background:#ffffff;border:1.5px solid #cbd5e1;border-radius:8px;padding:0 0.75rem;height:38px;min-height:38px;flex:none;display:flex;align-items:center;gap:0.55rem;width:100%;box-sizing:border-box;cursor:pointer;"
               title="Click to copy full link:\n${album.share_url}" role="button" tabindex="0">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="2.2" style="flex-shrink:0;">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
            </svg>
            <span class="compact-link-text" style="color:#334155;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:0.86rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${compactUrl}</span>
          </div>

          <div style="display:flex;align-items:center;gap:0.45rem;flex-wrap:wrap;width:100%;">
            <button type="button" class="btn-copy-action btn-copy-album-link" data-url="${album.share_url}"
              style="background:#0f5c4d;color:#ffffff;border:none;border-radius:8px;padding:0 0.85rem;height:38px;min-height:38px;font-size:0.82rem;font-weight:700;display:inline-flex;align-items:center;gap:6px;cursor:pointer;white-space:nowrap;"
              title="Copy full link to clipboard">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              <span class="btn-copy-label">Copy Link</span>
            </button>

            <a href="https://api.whatsapp.com/send?text=${encodeURIComponent('Here is my photo album: ' + album.share_url)}" target="_blank" rel="noopener noreferrer"
              class="btn-whatsapp-icon"
              style="width:38px;height:38px;min-width:38px;min-height:38px;background:#25d366;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;color:#fff;text-decoration:none;border:none;flex-shrink:0;"
              aria-label="Share on WhatsApp"
              title="Share on WhatsApp">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                <path fill-rule="evenodd" clip-rule="evenodd" d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91C2.13 13.66 2.59 15.36 3.45 16.86L2.05 22L7.3 20.63C8.75 21.41 10.38 21.83 12.04 21.83C17.5 21.83 21.95 17.38 21.95 11.92C21.95 9.27 20.92 6.78 19.05 4.91C17.18 3.03 14.69 2 12.04 2ZM12.04 20.15C10.55 20.15 9.09 19.75 7.81 19L7.51 18.82L4.39 19.64L5.22 16.6L5.02 16.29C4.16 14.93 3.71 13.44 3.71 11.91C3.71 7.32 7.45 3.58 12.04 3.58C14.27 3.58 16.36 4.45 17.93 6.02C19.5 7.59 20.37 9.68 20.37 11.91C20.37 16.5 16.63 20.15 12.04 20.15ZM16.6 14.34C16.35 14.21 15.12 13.61 14.89 13.52C14.66 13.43 14.49 13.39 14.32 13.64C14.15 13.89 13.66 14.47 13.51 14.64C13.36 14.81 13.21 14.83 12.96 14.71C12.71 14.58 11.91 14.32 10.96 13.47C10.22 12.81 9.72 11.99 9.57 11.74C9.42 11.49 9.55 11.36 9.68 11.23C9.79 11.12 9.93 10.94 10.06 10.79C10.19 10.64 10.23 10.54 10.31 10.37C10.39 10.2 10.35 10.06 10.29 9.93C10.23 9.8 9.74 8.59 9.53 8.1C9.33 7.62 9.12 7.69 8.97 7.68L8.49 7.67C8.32 7.67 8.06 7.73 7.83 7.98C7.6 8.23 6.95 8.84 6.95 10.08C6.95 11.32 7.85 12.52 7.98 12.69C8.11 12.86 9.75 15.38 12.26 16.46C12.86 16.72 13.32 16.87 13.68 16.99C14.28 17.18 14.82 17.15 15.26 17.09C15.75 17.02 16.76 16.48 16.97 15.89C17.18 15.3 17.18 14.8 17.12 14.69C17.06 14.58 16.85 14.47 16.6 14.34Z"/>
              </svg>
            </a>

            <a href="${album.share_url}" target="_blank" rel="noopener noreferrer"
              style="background:#ffffff;border:1.5px solid #0f5c4d;color:#0f5c4d;border-radius:8px;padding:0 0.85rem;height:38px;min-height:38px;font-size:0.82rem;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;gap:6px;white-space:nowrap;"
              title="Open album in new tab">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0f5c4d" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
              </svg>
              <span>Open</span>
            </a>

            <button type="button" class="btn-revoke-album" data-idx="${idx}" data-album-id="${album.id}" data-manage-token="${album.manage_token || ''}"
              style="margin-left:auto;background:#ffffff;border:1.5px solid #dc2626;color:#dc2626;border-radius:8px;padding:0 0.85rem;height:38px;min-height:38px;font-size:0.82rem;font-weight:700;cursor:pointer;white-space:nowrap;">
              Revoke Link
            </button>
          </div>
        ` : `
          <div style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;padding:0.65rem 0.85rem;font-size:0.8rem;color:#64748b;display:flex;align-items:center;gap:6px;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>
            <span>This link is ${statusText.toLowerCase()} and cannot be viewed by recipients.</span>
          </div>
        `}
      </div>
    `;
    container.appendChild(card);
  });

  container.querySelectorAll('.btn-copy-album-link').forEach(btn => {
    btn.addEventListener('click', () => {
      const url = btn.getAttribute('data-url');
      if (!url) return;
      const notify = () => {
        const label = btn.querySelector('.btn-copy-label');
        if (label) {
          const orig = label.textContent;
          label.textContent = '✓ Copied!';
          setTimeout(() => { label.textContent = orig; }, 2000);
        }
        showToast('Link copied to clipboard!');
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(notify).catch(() => {
          fallbackCopyText(url);
          notify();
        });
      } else {
        fallbackCopyText(url);
        notify();
      }
    });
  });

  container.querySelectorAll('.btn-revoke-album').forEach(btn => {
    btn.addEventListener('click', async () => {
      const idx = parseInt(btn.getAttribute('data-idx'), 10);
      const album = albums[idx];
      const albumId = (album && album.id) || btn.getAttribute('data-album-id');
      const mgToken = (album && album.manage_token) || btn.getAttribute('data-manage-token');
      if (!albumId || !mgToken) {
        showToast('Management credentials missing for this album.');
        return;
      }
      if (!confirm('Are you sure you want to revoke this album link? Anyone with the link will no longer be able to view these photos.')) {
        return;
      }

      btn.disabled = true;
      btn.textContent = 'Revoking...';
      try {
        const res = await fetch(`/api/shared-albums/${encodeURIComponent(albumId)}/revoke`, {
          method: 'POST',
          headers: {
            'X-Manage-Token': mgToken
          }
        });
        if (!res.ok) {
          const j = await res.json().catch(() => ({}));
          throw new Error(j.detail || 'Revocation failed.');
        }
        albums[idx].is_revoked = true;
        localStorage.setItem('my_shared_albums', JSON.stringify(albums));
        updateMyAlbumsBadge();
        renderMyAlbumsList();
        showToast('Album link revoked successfully.');
      } catch (e) {
        showToast('Could not revoke album: ' + e.message);
        btn.disabled = false;
        btn.textContent = 'Revoke Link';
      }
    });
  });
}

export function fallbackCopyText(text) {
  const input = document.createElement('textarea');
  input.value = text;
  input.style.position = 'fixed';
  input.style.opacity = '0';
  document.body.appendChild(input);
  input.focus();
  input.select();
  try { document.execCommand('copy'); } catch (e) {}
  document.body.removeChild(input);
}

export function copyFullSuccessLink() {
  const link = $('successShareLinkInput')?.value || $('compactShareLinkDisplay')?.getAttribute('data-full-url');
  if (!link) return;

  const finishCopy = () => {
    const btnText = $('btnCopySuccessText');
    if (btnText) {
      const orig = btnText.textContent;
      btnText.textContent = '✓ Copied!';
      setTimeout(() => { btnText.textContent = orig; }, 2200);
    }
    const fb = $('copyFeedbackToast');
    if (fb) {
      fb.style.display = 'inline';
      setTimeout(() => { fb.style.display = 'none'; }, 2200);
    }
    showToast('Link copied to clipboard!');
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(link).then(finishCopy).catch(() => {
      fallbackCopyText(link);
      finishCopy();
    });
  } else {
    fallbackCopyText(link);
    finishCopy();
  }
}
