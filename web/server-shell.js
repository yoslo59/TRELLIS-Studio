const API = '/server/api';
const number = new Intl.NumberFormat('en-US', {maximumFractionDigits: 1});
const errors = {
  account_inactive: 'This account is awaiting approval or has been disabled. Contact your administrator.',
  admin_required: 'This page requires administrator access.',
  disk_quota: 'You have reached your storage quota. Free up space or contact your administrator.',
  credits_exhausted: 'You have no generation credits available. Contact your administrator.',
  invalid_credentials: 'Incorrect email address or password.',
  invalid_factor: 'This code is incorrect or has already been used.',
  mfa_required: 'Enter a code from your authenticator app or a recovery code.',
  mfa_setup_required: 'Set up two-factor authentication to access this page.',
  password_change_required: 'Change your temporary password to continue.',
  unauthenticated: 'Sign in to continue.',
};

export function element(tag, className = '', text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export async function request(path, {method = 'GET', body, signal} = {}) {
  const headers = new Headers({'Accept': 'application/json'});
  if (method !== 'GET') headers.set('X-Trellis-Request', '1');
  if (body != null && !(body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(body);
  }
  const response = await fetch(API + path, {method, headers, body, signal, credentials: 'same-origin', cache: 'no-store'});
  const result = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof result.detail === 'string' ? result.detail : result.detail?.message;
    const code = result.code || result.detail?.code;
    const message = Object.hasOwn(errors, code) ? errors[code] : detail;
    const error = new Error(message || (response.status === 422
      ? 'Check the information you entered.' : 'The server could not process the request.'));
    error.status = response.status;
    error.code = code;
    throw error;
  }
  return result;
}

export const getUser = payload => payload.user || payload;
export const nameOf = user => [user.first_name, user.last_name].filter(Boolean).join(' ') || user.email || 'TRELLIS account';
export const diskLimit = user => user.disk_limit_bytes ?? user.disk_limit ?? null;
export const diskUsed = user => user.disk_used_bytes ?? user.used_bytes ?? 0;
export const availableCredits = user => user.credits_available ?? Math.max(0, (user.credits || 0) - (user.credits_reserved || 0));
export const formatNumber = value => number.format(value);
export function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value >= 1024 ** 4) return `${formatNumber(value / 1024 ** 4)} TiB`;
  if (value >= 1024 ** 3) return `${formatNumber(value / 1024 ** 3)} GiB`;
  if (value >= 1024 ** 2) return `${formatNumber(value / 1024 ** 2)} MiB`;
  if (value >= 1024) return `${formatNumber(value / 1024)} KiB`;
  return `${formatNumber(value)} B`;
}

export function avatar(target, user, version = '') {
  const initials = [user.first_name, user.last_name].filter(Boolean).map(name => name.charAt(0).toUpperCase()).join('');
  target.textContent = initials || 'T';
  target.setAttribute('aria-hidden', 'true');
  if (!(user.avatar || user.avatar_url || user.has_avatar) || !user.id) return;
  const image = element('img');
  image.alt = '';
  image.addEventListener('load', () => target.replaceChildren(image), {once: true});
  image.src = `${API}/avatar/${encodeURIComponent(user.id)}${version ? `?v=${encodeURIComponent(version)}` : ''}`;
}

const studioHeader = document.querySelector('.top-status');
if (studioHeader) {
  document.body.classList.add('trellis-server-mode');
  const shutdown = document.getElementById('shutdown');
  if (shutdown) shutdown.hidden = true;
  const developerLink = document.querySelector('.developer-link');
  if (developerLink) {
    developerLink.title = 'API and MCP documentation';
    developerLink.setAttribute('aria-label', developerLink.title);
  }
  const edition = document.querySelector('.brand .version');
  if (edition) edition.textContent = 'SERVER';
  const local = document.querySelector('.rail-bottom .local-badge');
  const railBottom = local?.parentElement;
  const creditBadge = element('a', 'server-credit-badge');
  creditBadge.href = '/server/account#usage';
  creditBadge.setAttribute('aria-label', 'Usage');
  const creditCount = element('span', 'server-credit-count', '—');
  const creditLabel = element('span', 'server-credit-label', 'Credits');
  creditBadge.append(creditCount, creditLabel);
  local?.replaceWith(creditBadge);
  const privacy = document.querySelector('.rail-bottom small');
  if (privacy) privacy.textContent = 'Your library is stored on the server.';
  const importFile = document.getElementById('importFile');
  if (importFile) importFile.accept = '.glb,model/gltf-binary';
  const importHint = document.querySelector('#importForm > p');
  if (importHint) importHint.textContent = 'Import a self-contained GLB with embedded textures (up to 500 MiB).';
  const animation = document.getElementById('animationLibraryControls');
  if (animation) animation.prepend(element('p', 'hint', 'Open the model in TRELLIS Desktop to add or edit animation clips.'));

  const menu = element('details', 'server-shell');
  const summary = element('summary');
  summary.setAttribute('aria-label', 'TRELLIS account menu');
  const icon = element('span', 'server-avatar', 'T');
  const title = element('span', 'server-shell-name', 'My account');
  summary.append(icon, title, element('span', '', '⌄'));
  const items = element('div', 'server-shell-menu');
  const usage = element('p');
  usage.hidden = true;
  const profileLink = element('a', '', 'My account');
  profileLink.href = '/server/account';
  const usageLink = element('a', '', 'Usage');
  usageLink.href = '/server/account#usage';
  const securityLink = element('a', '', 'Security');
  securityLink.href = '/server/account#security';
  const adminLink = element('a', '', 'Administration');
  adminLink.href = '/server/admin';
  adminLink.hidden = true;
  const logout = element('button', 'server-shell-logout', 'Sign out');
  logout.type = 'button';
  const logoutIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  logoutIcon.setAttribute('class', 'server-logout-icon');
  logoutIcon.setAttribute('aria-hidden', 'true');
  const logoutSymbol = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  logoutSymbol.setAttribute('href', '/web/icons.svg#logout');
  logoutIcon.append(logoutSymbol);
  logout.prepend(logoutIcon);
  const notice = element('p', 'server-shell-error');
  notice.setAttribute('role', 'status');
  notice.hidden = true;
  items.append(usage, profileLink, usageLink, securityLink, adminLink, logout, notice);
  menu.append(summary, items);
  studioHeader.append(menu);

  const compact = matchMedia('(max-width: 850px)');
  function placeCreditBadge() {
    if (compact.matches || !railBottom) studioHeader.insertBefore(creditBadge, menu);
    else railBottom.prepend(creditBadge);
  }
  placeCreditBadge();
  compact.addEventListener('change', placeCreditBadge);

  let balanceReadSequence = 0;
  let latestBalanceRead = 0;
  function renderCredits(available, read) {
    if (read < latestBalanceRead || (available !== null && !Number.isFinite(available))) return;
    latestBalanceRead = read;
    const count = available === null ? '∞' : formatNumber(available);
    const label = available === 1 ? 'Credit' : 'Credits';
    const balance = available === null ? 'Unlimited credits' : `${count} ${label.toLowerCase()} available`;
    if (creditCount.textContent !== count) creditCount.textContent = count;
    creditLabel.textContent = label;
    creditBadge.title = balance;
    creditBadge.setAttribute('aria-label', `${balance} · Usage`);
  }

  let checkingCredits = false;
  async function refreshCredits() {
    if (document.hidden || checkingCredits) return;
    checkingCredits = true;
    const read = ++balanceReadSequence;
    try {
      const {credits_available: available} = await request('/credits');
      renderCredits(available, read);
    } catch (error) {
      if (error.status === 401 || error.status === 403) location.replace('/server/login');
    } finally { checkingCredits = false; }
  }

  async function refresh() {
    const read = ++balanceReadSequence;
    try {
      const user = getUser(await request('/me'));
      renderCredits(user.role === 'admin' ? null : availableCredits(user), read);
      title.textContent = nameOf(user);
      avatar(icon, user);
      adminLink.hidden = user.role !== 'admin';
      const limit = diskLimit(user);
      const credits = availableCredits(user);
      usage.textContent = user.role === 'admin'
        ? 'Unlimited storage and credits'
        : `${formatBytes(diskUsed(user))} / ${formatBytes(limit)} · ${formatNumber(credits)} ${credits === 1 ? 'credit' : 'credits'}`;
      usage.hidden = false;
      notice.hidden = true;
    } catch (error) {
      if (error.status === 401 || error.status === 403) location.replace('/server/login');
    }
  }

  logout.addEventListener('click', async () => {
    logout.disabled = true;
    try {
      await request('/logout', {method: 'POST'});
      location.replace('/server/login');
    } catch (error) {
      if (error.status === 401) location.replace('/server/login');
      else {
        notice.textContent = error.message;
        notice.hidden = false;
      }
    } finally { logout.disabled = false; }
  });
  menu.addEventListener('toggle', () => { if (menu.open) refresh(); });
  document.addEventListener('click', event => { if (!menu.contains(event.target)) menu.open = false; });
  menu.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      menu.open = false;
      summary.focus();
    }
  });
  window.addEventListener('focus', refresh);
  window.addEventListener('focus', refreshCredits);
  document.addEventListener('visibilitychange', refreshCredits);
  window.setInterval(refreshCredits, 5000);
  refresh();
  refreshCredits();
}
