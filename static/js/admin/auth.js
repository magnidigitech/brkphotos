// BRK Photos Admin - Authentication & Security Controller (ES Module)
import { adminState, $, escapeHtml, parseDateUtcOrLocal, formatToIST } from './state.js?v=2.2.0';
import { safeFetchJson, openModal, closeModal } from './api.js?v=2.2.0';
import { populateEventSelector, pollTaskStatus, initSidebarCollapsibles, setEventWorkspace } from './dashboard.js?v=2.2.0';
import { setupAlbumSearch } from './events-photos.js?v=2.2.0';

async function checkAuth() {
  try {
    const res = await fetch('/api/admin/overview');
    if (res.ok) {
      const data = await res.json();
      $('authView').hidden = true;
      $('appShell').hidden = false;

      const events = data.events || [];
      const urlParams = new URLSearchParams(window.location.search);
      const targetSlug = urlParams.get('event') || localStorage.getItem('admin_active_event_slug');
      if (targetSlug && events.some(e => e.slug === targetSlug)) {
        activeSlug = targetSlug;
      } else if (events.length > 0) {
        activeSlug = events[0].slug;
      }
      localStorage.setItem('admin_active_event_slug', activeSlug);

      populateEventSelector(events);
      await setEventWorkspace(activeSlug, false);

      initSidebarCollapsibles();
      setupAlbumSearch();

      const targetTab = urlParams.get('tab') || localStorage.getItem('admin_active_tab') || 'dashboard';
      switchTab(targetTab);
      pollTaskStatus();
      return true;
    } else {
      localStorage.removeItem('admin_token');
      $('authView').hidden = false;
      $('appShell').hidden = true;
      return false;
    }
  } catch (e) {
    console.error('checkAuth error:', e);
    if (!localStorage.getItem('admin_token')) {
      $('authView').hidden = false;
      $('appShell').hidden = true;
    }
    return false;
  }
}

let currentChallengeId = null;


function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let str = '';
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function base64urlToBuffer(base64url) {
  let str = (base64url || '').replace(/-/g, '+').replace(/_/g, '/');
  while (str.length % 4 !== 0) str += '=';
  const binary = atob(str);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function resetLoginSteps() {
  currentChallengeId = null;
  if ($('stepPasswordWrap')) $('stepPasswordWrap').hidden = false;
  if ($('step2faWrap')) $('step2faWrap').hidden = true;
  if ($('stepRecoveryWrap')) $('stepRecoveryWrap').hidden = true;
  if ($('loginAlert')) $('loginAlert').hidden = true;
  if ($('step2faAlert')) $('step2faAlert').hidden = true;
  if ($('stepRecoveryAlert')) $('stepRecoveryAlert').hidden = true;
  if ($('adminKey')) $('adminKey').focus();
}

function show2faStep() {
  if ($('stepPasswordWrap')) $('stepPasswordWrap').hidden = true;
  if ($('step2faWrap')) $('step2faWrap').hidden = false;
  if ($('stepRecoveryWrap')) $('stepRecoveryWrap').hidden = true;
  if ($('totpCodeInput')) {
    $('totpCodeInput').value = '';
    $('totpCodeInput').focus();
  }
}

function showRecoveryCodeStep() {
  if ($('stepPasswordWrap')) $('stepPasswordWrap').hidden = true;
  if ($('step2faWrap')) $('step2faWrap').hidden = true;
  if ($('stepRecoveryWrap')) $('stepRecoveryWrap').hidden = false;
  if ($('recoveryCodeInput')) {
    $('recoveryCodeInput').value = '';
    $('recoveryCodeInput').focus();
  }
}


let isLoggingIn = false;

async function handleAdminLogin(e) {
  if (e && typeof e.preventDefault === 'function') e.preventDefault();
  if (isLoggingIn) return false;

  const keyInput = $('adminKey');
  if (!keyInput) return false;
  const key = keyInput.value.trim();
  if (!key) {
    keyInput.focus();
    return false;
  }

  isLoggingIn = true;
  const btn = $('loginBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Authenticating...';
  }
  const alertEl = $('loginAlert');
  if (alertEl) {
    alertEl.hidden = true;
    alertEl.textContent = '';
  }

  // Safety fallback: ensure button is never stuck indefinitely
  const safetyTimer = setTimeout(() => {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Sign In';
    }
    isLoggingIn = false;
  }, 25000);

  try {
    const { ok, status, data: j } = await safeFetchJson('/api/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key })
    }, 20000);

    clearTimeout(safetyTimer);

    // Check if 2FA is required
    if (ok && j && j.status === '2fa_required') {
      currentChallengeId = j.challenge_id;
      show2faStep();
      return false;
    }

    if (ok && j && j.token) {
      localStorage.setItem('admin_token', j.token);
      localStorage.setItem('admin_active_tab', 'dashboard');
      $('authView').hidden = true;
      $('appShell').hidden = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Sign In';
      }
      isLoggingIn = false;
      checkAuth().catch(err => console.error('checkAuth post-login error:', err));
      return true;
    } else {
      const errMsg = (j && (j.detail || j.message)) || (status === 401 ? 'Incorrect security passkey or password.' : 'Authentication failed. Please try again.');
      if (alertEl) {
        alertEl.textContent = errMsg;
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    clearTimeout(safetyTimer);
    if (alertEl) {
      alertEl.textContent = 'Server connection error: ' + (err.message || 'Unable to reach backend.');
      alertEl.hidden = false;
    }
  } finally {
    clearTimeout(safetyTimer);
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Sign In';
    }
    isLoggingIn = false;
  }
  return false;
}

async function handle2faVerify() {
  const codeInput = $('totpCodeInput');
  if (!codeInput || !currentChallengeId) return;
  const code = codeInput.value.trim();
  if (!code) return;

  const btn = $('step2faBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Verifying...';
  }
  const alertEl = $('step2faAlert');
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/auth/2fa/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ challenge_id: currentChallengeId, code: code })
    }, 10000);
    if (ok && j.token) {
      localStorage.setItem('admin_token', j.token);
      localStorage.setItem('admin_active_tab', 'dashboard');
      $('authView').hidden = true;
      $('appShell').hidden = false;
      resetLoginSteps();
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Verify & Sign In';
      }
      checkAuth().catch(err => console.error('checkAuth post-2fa error:', err));
      return;
    } else {
      if (alertEl) {
        alertEl.textContent = j.detail || 'Invalid 6-digit authenticator code.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Connection error: ' + (err.message || 'Verification failed.');
      alertEl.hidden = false;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Verify & Sign In';
    }
  }
}

async function handleRecoveryCodeVerify() {
  const codeInput = $('recoveryCodeInput');
  if (!codeInput || !currentChallengeId) return;
  const code = codeInput.value.trim();
  if (!code) return;

  const btn = $('stepRecoveryBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Verifying...';
  }
  const alertEl = $('stepRecoveryAlert');
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/auth/recovery-code/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ challenge_id: currentChallengeId, recovery_code: code })
    }, 10000);
    if (ok && j.token) {
      localStorage.setItem('admin_token', j.token);
      localStorage.setItem('admin_active_tab', 'dashboard');
      if (j.warning) {
        alert(j.warning);
      }
      $('authView').hidden = true;
      $('appShell').hidden = false;
      resetLoginSteps();
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Authenticate with Recovery Code';
      }
      checkAuth().catch(err => console.error('checkAuth post-recovery error:', err));
      return;
    } else {
      if (alertEl) {
        alertEl.textContent = j.detail || 'Invalid or already-consumed recovery code.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Connection error: ' + (err.message || 'Recovery failed.');
      alertEl.hidden = false;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Authenticate with Recovery Code';
    }
  }
}

async function handlePasskeyLogin() {
  const alertEl = $('loginAlert');
  if (alertEl) alertEl.hidden = true;

  if (!window.PublicKeyCredential) {
    if (alertEl) {
      alertEl.textContent = 'Passkeys are not supported by this browser. Please use password login.';
      alertEl.hidden = false;
    }
    return;
  }

  const btn = $('passkeyLoginBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Connecting Passkey...';
  }

  try {
    // 1. Fetch challenge from backend safely
    const { ok: chalOk, data: chalData } = await safeFetchJson('/api/admin/auth/passkey/generate-challenge', { method: 'POST' }, 10000);
    if (!chalOk) {
      throw new Error(chalData?.detail || 'Could not initialize passkey login.');
    }

    // 2. Format options for WebAuthn get
    const publicKeyOptions = {
      challenge: base64urlToBuffer(chalData.challenge),
      rpId: chalData.rpId,
      allowCredentials: (chalData.allowCredentials || []).map(c => ({
        type: c.type,
        id: base64urlToBuffer(c.id),
        transports: c.transports
      })),
      userVerification: chalData.userVerification || 'preferred',
      timeout: chalData.timeout || 60000
    };

    // 3. Request credential from browser / authenticator
    const credential = await navigator.credentials.get({ publicKey: publicKeyOptions });
    if (!credential) throw new Error('Passkey verification was cancelled.');

    // 4. Send assertion to server
    const clientDataJSON = bufferToBase64url(credential.response.clientDataJSON);
    const authenticatorData = bufferToBase64url(credential.response.authenticatorData);
    const signature = bufferToBase64url(credential.response.signature);
    const credentialId = credential.id;

    const { ok: verifyOk, data: verifyData } = await safeFetchJson('/api/admin/auth/passkey/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: chalData.challenge_id,
        credential_id: credentialId,
        client_data_json: clientDataJSON,
        authenticator_data: authenticatorData,
        signature: signature
      })
    }, 10000);
    if (!verifyOk) {
      throw new Error(verifyData?.detail || 'Passkey verification failed on server.');
    }

    // 5. Store token & enter admin console
    localStorage.setItem('admin_token', verifyData.token);
    localStorage.setItem('admin_active_tab', 'dashboard');
    $('authView').hidden = true;
    $('appShell').hidden = false;
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `
        <svg class="icon" style="width:18px;height:18px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm0 2a3 3 0 0 1 3 3v3H9V7a3 3 0 0 1 3-3z"/><circle cx="12" cy="16" r="1"/></svg>
        Sign in with Passkey / Face ID
      `;
    }
    checkAuth().catch(err => console.error('checkAuth post-passkey error:', err));
  } catch (err) {
    if (err.name !== 'NotAllowedError') {
      if (alertEl) {
        alertEl.textContent = (err.message || 'Passkey authentication failed.');
        alertEl.hidden = false;
      }
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `
        <svg class="icon" style="width:18px;height:18px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm0 2a3 3 0 0 1 3 3v3H9V7a3 3 0 0 1 3-3z"/><circle cx="12" cy="16" r="1"/></svg>
        Sign in with Passkey / Face ID
      `;
    }
  }
}


async function handleLogout() {
  localStorage.removeItem('admin_token');
  localStorage.removeItem('admin_active_tab');
  try {
    await fetch('/api/admin/logout', { method: 'POST' });
  } catch (e) {}
  $('appShell').hidden = true;
  $('authView').hidden = false;
  window.location.href = '/admin';
}


let cachedTotpSetupData = null;

async function loadSecuritySettings() {
  try {
    const { ok, data } = await safeFetchJson('/api/admin/security/status');
    if (!ok || !data) return;

    // 1. Two-Step Verification (TOTP)
    const badge = $('totpStatusBadge');
    const enableBtn = $('enable2faBtn');
    const disableBtn = $('disable2faBtn');
    const recoverySec = $('recoveryCodesSection');
    const recoveryCount = $('recoveryCodesCount');

    if (badge) {
      if (data.totp_enabled) {
        badge.textContent = 'Active (2FA Protected)';
        badge.style.background = '#dcfce7';
        badge.style.color = '#15803d';
        if (enableBtn) enableBtn.hidden = true;
        if (disableBtn) disableBtn.hidden = false;
        if (recoverySec) recoverySec.hidden = false;
      } else {
        badge.textContent = 'Disabled (Password Only)';
        badge.style.background = '#fef9c3';
        badge.style.color = '#854d0e';
        if (enableBtn) enableBtn.hidden = false;
        if (disableBtn) disableBtn.hidden = true;
        if (recoverySec) recoverySec.hidden = true;
      }
    }

    if (recoveryCount && data.recovery_codes) {
      const unused = data.recovery_codes.unused_count || 0;
      const total = data.recovery_codes.total_count || 0;
      recoveryCount.textContent = `${unused} of ${total} available`;
      recoveryCount.style.color = unused > 2 ? '#0f5c4d' : '#dc2626';
    }

    // 2. Passkeys List
    renderPasskeysList(data.passkeys || []);

    // 3. Active Sessions List
    renderSessionsList(data.active_sessions || []);

  } catch (err) {
    console.error('Failed to load security settings:', err);
  }
}

function renderPasskeysList(passkeys) {
  const container = $('passkeysListContainer');
  if (!container) return;

  if (passkeys.length === 0) {
    container.innerHTML = `
      <div style="padding:1rem;background:#f9fafb;border-radius:8px;border:1px dashed var(--line);text-align:center;color:var(--muted);font-size:0.85rem;">
        No passkeys registered yet. Click <strong>"Register New Passkey"</strong> to enable instant biometric login with Face ID, Touch ID, or Windows Hello.
      </div>
    `;
    return;
  }

  let html = `
    <div style="border:1px solid var(--line);border-radius:8px;overflow:hidden;">
      <table style="width:100%;font-size:0.82rem;border-collapse:collapse;">
        <thead>
          <tr style="text-align:left;background:#f9fafb;border-bottom:1px solid var(--line);color:var(--muted);">
            <th style="padding:0.6rem 0.75rem;">Device Name</th>
            <th style="padding:0.6rem 0.75rem;">Registered On</th>
            <th style="padding:0.6rem 0.75rem;">Last Used</th>
            <th style="padding:0.6rem 0.75rem;text-align:right;">Actions</th>
          </tr>
        </thead>
        <tbody>
  `;

  passkeys.forEach(p => {
    const devNameEscaped = escapeHtml(p.device_name || 'Passkey Device');
    const safeJsName = JSON.stringify(p.device_name || 'Passkey Device').replace(/"/g, '&quot;');
    html += `
      <tr style="border-bottom:1px solid var(--line);">
        <td style="padding:0.65rem 0.75rem;font-weight:600;">
          <div style="display:flex;align-items:center;gap:0.5rem;">
            <svg class="icon" style="width:16px;height:16px;color:#0f5c4d;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            ${devNameEscaped}
          </div>
        </td>
        <td style="padding:0.65rem 0.75rem;color:var(--muted);">${escapeHtml(p.created_at || 'Recently')}</td>
        <td style="padding:0.65rem 0.75rem;color:var(--muted);">${escapeHtml(p.last_used_at || 'Never')}</td>
        <td style="padding:0.65rem 0.75rem;text-align:right;">
          <button class="btn-outline" onclick="renamePasskey(${p.id}, ${safeJsName})" style="font-size:0.75rem;padding:0.25rem 0.5rem;margin-right:0.35rem;">Rename</button>
          <button class="btn-outline" onclick="deletePasskey(${p.id}, ${safeJsName})" style="font-size:0.75rem;padding:0.25rem 0.5rem;color:var(--err);border-color:var(--err);">Revoke</button>
        </td>
      </tr>
    `;
  });

  html += `</tbody></table></div>`;
  container.innerHTML = html;
}

function renderSessionsList(sessions) {
  const tbody = $('sessionsTableBody');
  if (!tbody) return;

  if (sessions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="padding:1rem;text-align:center;color:var(--muted);">No active sessions found.</td></tr>';
    return;
  }

  tbody.replaceChildren();
  sessions.forEach(s => {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid var(--line)';

    const currentBadge = s.is_current ? `<span class="badge" style="background:#dcfce7;color:#15803d;font-size:0.7rem;padding:0.15rem 0.45rem;border-radius:4px;margin-left:0.4rem;">Current Session</span>` : '';
    const deviceStr = escapeHtml(s.user_agent ? (s.user_agent.length > 50 ? s.user_agent.substring(0, 50) + '...' : s.user_agent) : 'Browser');

    tr.innerHTML = `
      <td style="padding:0.65rem 0.75rem;font-weight:600;">
        ${deviceStr}${currentBadge}
      </td>
      <td style="padding:0.65rem 0.75rem;font-family:monospace;font-size:0.8rem;">${escapeHtml(s.ip_address || 'Unknown')}</td>
      <td style="padding:0.65rem 0.75rem;color:var(--muted);">${escapeHtml(s.created_at || '')}</td>
      <td style="padding:0.65rem 0.75rem;color:var(--muted);">${escapeHtml(s.last_active_at || '')}</td>
      <td style="padding:0.65rem 0.75rem;text-align:right;">
        ${s.is_current ? '<span style="color:var(--muted);font-size:0.75rem;">Active Now</span>' : `<button class="btn-outline" onclick="revokeSession(${s.id})" style="font-size:0.75rem;padding:0.25rem 0.5rem;color:var(--err);border-color:var(--err);">Revoke</button>`}
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function handleAdminChangePassword() {
  const currentPwd = $('currentPwdInput')?.value;
  const newPwd = $('newPwdInput')?.value;
  const confirmPwd = $('confirmPwdInput')?.value;
  const statusMsg = $('pwdStatusMsg');
  const btn = $('savePwdBtn');

  if (!currentPwd || !newPwd) return;
  if (newPwd !== confirmPwd) {
    if (statusMsg) {
      statusMsg.textContent = 'New passwords do not match.';
      statusMsg.style.color = 'var(--err)';
      statusMsg.hidden = false;
    }
    return;
  }
  if (newPwd.length < 8) {
    if (statusMsg) {
      statusMsg.textContent = 'Password must be at least 8 characters long.';
      statusMsg.style.color = 'var(--err)';
      statusMsg.hidden = false;
    }
    return;
  }

  if (btn) btn.disabled = true;
  if (statusMsg) statusMsg.hidden = true;

  try {
    const { ok, status, data: j } = await safeFetchJson('/api/admin/security/password/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPwd, new_password: newPwd })
    });
    if (ok) {
      if (statusMsg) {
        statusMsg.textContent = 'Password updated successfully!';
        statusMsg.style.color = '#15803d';
        statusMsg.hidden = false;
      }
      $('changePasswordForm')?.reset();
      loadSecurityLogs();
    } else {
      if (statusMsg) {
        statusMsg.textContent = j?.detail || j?.message || 'Password update failed.';
        statusMsg.style.color = 'var(--err)';
        statusMsg.hidden = false;
      }
    }
  } catch (err) {
    if (statusMsg) {
      statusMsg.textContent = 'Error: ' + err.message;
      statusMsg.style.color = 'var(--err)';
      statusMsg.hidden = false;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function openTotpSetupModal() {
  try {
    const { ok, status, data: j } = await safeFetchJson('/api/admin/security/totp/setup', { method: 'POST' });
    if (!ok) {
      alert(j?.detail || j?.message || 'Failed to start 2FA setup.');
      return;
    }

    cachedTotpSetupData = j;
    if ($('totpQrCodeImg')) {
      $('totpQrCodeImg').innerHTML = `<img src="${j.qr_svg}" alt="2FA QR Code" style="width:160px;height:160px;display:block;">`;
    }
    if ($('totpSecretText')) {
      $('totpSecretText').textContent = j.secret;
    }

    const codesContainer = $('totpModalRecoveryCodes');
    if (codesContainer && j.recovery_codes) {
      codesContainer.innerHTML = j.recovery_codes.map(c => `<div>${escapeHtml(c)}</div>`).join('');
    }

    if ($('confirmTotpCodeInput')) $('confirmTotpCodeInput').value = '';
    if ($('confirmTotpAlert')) $('confirmTotpAlert').hidden = true;

    openModal('totpSetupModal');
  } catch (err) {
    alert('Error setting up 2FA: ' + (err.detail || err.message));
  }
}

function copyRecoveryCodesFromModal() {
  if (!cachedTotpSetupData || !cachedTotpSetupData.recovery_codes) return;
  const text = "BRK Photos Admin - Emergency Recovery Codes\n" +
    "Generated on: " + new Date().toISOString() + "\n\n" +
    cachedTotpSetupData.recovery_codes.join('\n') +
    "\n\nEach code can only be used once for emergency account access.";
  navigator.clipboard.writeText(text);
  alert('Recovery codes copied to clipboard! Save them in a secure password manager.');
}

function downloadRecoveryCodesFromModal() {
  if (!cachedTotpSetupData || !cachedTotpSetupData.recovery_codes) return;
  const text = "BRK Photos Admin - Emergency Recovery Codes\n" +
    "Generated on: " + new Date().toISOString() + "\n\n" +
    cachedTotpSetupData.recovery_codes.join('\n') +
    "\n\nKeep these codes safe. Each code can only be used once.";
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `brk_recovery_codes_${new Date().toISOString().slice(0, 10)}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function handleConfirmTotpSetup() {
  const code = $('confirmTotpCodeInput')?.value.trim();
  const alertEl = $('confirmTotpAlert');
  const btn = $('confirmTotpBtn');

  if (!code || !cachedTotpSetupData) return;
  if (btn) btn.disabled = true;
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, status, data: j } = await safeFetchJson('/api/admin/security/totp/enable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        secret: cachedTotpSetupData.secret,
        code: code,
        recovery_codes: cachedTotpSetupData.recovery_codes
      })
    });
    if (ok) {
      closeModal('totpSetupModal');
      alert('Two-step verification has been activated successfully! Your account is now protected with 2FA.');
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      if (alertEl) {
        alertEl.textContent = j?.detail || j?.message || 'Verification code incorrect. Please verify device clock and try again.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Connection error: ' + (err.detail || err.message);
      alertEl.hidden = false;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openDisableTotpModal() {
  if ($('disableTotpPwdInput')) $('disableTotpPwdInput').value = '';
  if ($('disableTotpAlert')) $('disableTotpAlert').hidden = true;
  openModal('disableTotpModal');
}

async function handleConfirmDisableTotp() {
  const pwd = $('disableTotpPwdInput')?.value;
  const alertEl = $('disableTotpAlert');
  const btn = $('confirmDisableTotpBtn');

  if (!pwd) return;
  if (btn) btn.disabled = true;
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/security/totp/disable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd })
    });
    if (ok) {
      closeModal('disableTotpModal');
      alert('Two-step verification has been disabled.');
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      if (alertEl) {
        alertEl.textContent = j?.detail || j?.message || 'Incorrect password.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Error: ' + err.message;
      alertEl.hidden = false;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function openRegenerateRecoveryModal() {
  if ($('regenRecoveryPwdInput')) $('regenRecoveryPwdInput').value = '';
  if ($('regenRecoveryAlert')) $('regenRecoveryAlert').hidden = true;
  openModal('regenRecoveryModal');
}

async function handleConfirmRegenRecovery() {
  const pwd = $('regenRecoveryPwdInput')?.value;
  const alertEl = $('regenRecoveryAlert');
  const btn = $('confirmRegenRecoveryBtn');

  if (!pwd) return;
  if (btn) btn.disabled = true;
  if (alertEl) alertEl.hidden = true;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/security/recovery-codes/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd })
    });
    if (ok) {
      closeModal('regenRecoveryModal');
      const codes = j?.recovery_codes || [];
      const text = "BRK Photos Admin - Newly Regenerated Emergency Recovery Codes\n" +
        "Generated on: " + new Date().toISOString() + "\n\n" +
        codes.join('\n') +
        "\n\nPrevious recovery codes are now invalid. Keep these safe.";
      navigator.clipboard.writeText(text);
      alert('10 new recovery codes generated and copied to clipboard! Save them safely.');
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      if (alertEl) {
        alertEl.textContent = j?.detail || j?.message || 'Incorrect password.';
        alertEl.hidden = false;
      }
    }
  } catch (err) {
    if (alertEl) {
      alertEl.textContent = 'Error: ' + err.message;
      alertEl.hidden = false;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function registerNewPasskey() {
  if (!window.PublicKeyCredential) {
    alert('WebAuthn Passkeys are not supported on this browser or platform.');
    return;
  }

  const deviceName = prompt('Enter a friendly name for this passkey device:', (navigator.platform || 'My Device') + ' Passkey');
  if (!deviceName) return;

  try {
    // 1. Request registration options from server
    const { ok: chalOk, status: chalStatus, data: chalData } = await safeFetchJson('/api/admin/security/passkey/register-challenge', { method: 'POST' });
    if (!chalOk) {
      alert(chalData?.detail || chalData?.message || 'Failed to start passkey registration.');
      return;
    }

    // 2. Prepare options
    const publicKeyOptions = {
      challenge: base64urlToBuffer(chalData.challenge),
      rp: chalData.rp,
      user: {
        id: base64urlToBuffer(chalData.user.id),
        name: chalData.user.name,
        displayName: chalData.user.displayName
      },
      pubKeyCredParams: chalData.pubKeyCredParams,
      authenticatorSelection: chalData.authenticatorSelection,
      timeout: chalData.timeout || 60000,
      excludeCredentials: (chalData.excludeCredentials || []).map(c => ({
        type: c.type,
        id: base64urlToBuffer(c.id)
      }))
    };

    // 3. Create credential via browser WebAuthn API
    const credential = await navigator.credentials.create({ publicKey: publicKeyOptions });
    if (!credential) return;

    // 4. Verify on server
    const clientDataJSON = bufferToBase64url(credential.response.clientDataJSON);
    const attestationObject = bufferToBase64url(credential.response.attestationObject);
    const credentialId = credential.id;

    const { ok: verifyOk, status: verifyStatus, data: verifyData } = await safeFetchJson('/api/admin/security/passkey/register-verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: chalData.challenge_id,
        credential_id: credentialId,
        public_key: attestationObject,
        device_name: deviceName,
        client_data_json: clientDataJSON
      })
    });

    if (verifyOk) {
      alert(`Passkey '${deviceName}' registered successfully! You can now use Face ID / Touch ID to sign in.`);
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      alert(verifyData?.detail || verifyData?.message || 'Failed to verify passkey with server.');
    }
  } catch (err) {
    if (err.name !== 'NotAllowedError') {
      alert('Passkey registration error: ' + (err.detail || err.message));
    }
  }
}

async function renamePasskey(passkeyId, currentName) {
  const newName = prompt('Enter new device name for this passkey:', currentName || '');
  if (!newName || !newName.trim() || newName === currentName) return;

  try {
    const { ok, data } = await safeFetchJson(`/api/admin/security/passkeys/${passkeyId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_name: newName.trim() })
    });
    if (ok) {
      loadSecuritySettings();
    } else {
      alert(data?.detail || data?.message || 'Failed to rename passkey.');
    }
  } catch (err) {
    alert('Error renaming passkey: ' + (err.detail || err.message));
  }
}

async function deletePasskey(passkeyId, deviceName) {
  const nameLabel = deviceName ? `'${deviceName}'` : 'this device';
  if (!confirm(`Are you sure you want to revoke passkey ${nameLabel}? It will no longer be able to log into this system.`)) return;

  try {
    const { ok, data } = await safeFetchJson(`/api/admin/security/passkeys/${passkeyId}`, { method: 'DELETE' });
    if (ok) {
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      alert(data?.detail || data?.message || 'Failed to revoke passkey.');
    }
  } catch (err) {
    alert('Error revoking passkey: ' + (err.detail || err.message));
  }
}

async function revokeSession(sessionId) {
  if (!confirm('Revoke this session? The device will be signed out immediately.')) return;
  try {
    const { ok, data } = await safeFetchJson('/api/admin/security/sessions/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId })
    });
    if (ok) {
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      alert(data?.detail || data?.message || 'Failed to revoke session.');
    }
  } catch (e) {
    alert('Error revoking session: ' + (e.detail || e.message));
  }
}

async function revokeOtherSessions() {
  if (!confirm('Revoke all other active sessions across other devices and browsers?')) return;
  try {
    const { ok, data } = await safeFetchJson('/api/admin/security/sessions/revoke', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revoke_all_others: true })
    });
    if (ok) {
      alert('All other sessions have been revoked.');
      loadSecuritySettings();
      loadSecurityLogs();
    } else {
      alert(data?.detail || data?.message || 'Failed to revoke other sessions.');
    }
  } catch (e) {
    alert('Error revoking sessions: ' + (e.detail || e.message));
  }
}

async function loadSecurityLogs() {
  const tbody = $('securityLogsTableBody');
  if (!tbody) return;

  try {
    const { ok, data: j } = await safeFetchJson('/api/admin/security/logs?limit=50');
    if (!ok || !j) return;
    const logs = j.events || [];

    tbody.replaceChildren();
    if (logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="padding:1rem;text-align:center;color:var(--muted);">No security events recorded yet.</td></tr>';
      return;
    }

    logs.forEach(log => {
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid var(--line)';

      let badgeColor = '#6b7280';
      let badgeBg = '#f3f4f6';
      if (log.status === 'success') {
        badgeColor = '#15803d';
        badgeBg = '#dcfce7';
      } else if (log.status === 'failed' || log.status === 'blocked') {
        badgeColor = '#dc2626';
        badgeBg = '#fee2e2';
      }

      tr.innerHTML = `
        <td style="padding:0.6rem 0.75rem;white-space:nowrap;color:var(--muted);">${escapeHtml(log.created_at || '')}</td>
        <td style="padding:0.6rem 0.75rem;font-weight:600;">${escapeHtml(log.event_type || '')}</td>
        <td style="padding:0.6rem 0.75rem;color:var(--text);">${escapeHtml(log.details || '')}</td>
        <td style="padding:0.6rem 0.75rem;font-family:monospace;font-size:0.78rem;">${escapeHtml(log.ip_address || '—')}</td>
        <td style="padding:0.6rem 0.75rem;">
          <span style="background:${badgeBg};color:${badgeColor};font-size:0.72rem;font-weight:700;padding:0.15rem 0.45rem;border-radius:4px;">
            ${escapeHtml(log.status || 'info')}
          </span>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error('Failed to load security logs:', err);
  }
}

// ===========================================================================
// INDEXING DIAGNOSTICS & ERROR LOGGING
// ===========================================================================


export {
  bufferToBase64url,
  base64urlToBuffer,
  resetLoginSteps,
  show2faStep,
  showRecoveryCodeStep,
  handleAdminLogin,
  handle2faVerify,
  handleRecoveryCodeVerify,
  handlePasskeyLogin,
  checkAuth,
  handleLogout,
  loadSecuritySettings,
  renderPasskeysList,
  renderSessionsList,
  handleAdminChangePassword,
  openTotpSetupModal,
  copyRecoveryCodesFromModal,
  downloadRecoveryCodesFromModal,
  handleConfirmTotpSetup,
  openDisableTotpModal,
  handleConfirmDisableTotp,
  openRegenerateRecoveryModal,
  handleConfirmRegenRecovery,
  registerNewPasskey,
  renamePasskey,
  deletePasskey,
  revokeSession,
  revokeOtherSessions,
  loadSecurityLogs
};
