const API = '/server/api';
const numbers = {
  fr: new Intl.NumberFormat('fr-FR', {maximumFractionDigits: 1}),
  en: new Intl.NumberFormat('en-US', {maximumFractionDigits: 1}),
};
const errors = {
  account_inactive: 'Ce compte est en attente d’activation ou a été désactivé. Contactez l’administration.',
  admin_required: 'Cette page est réservée à l’administration.',
  disk_quota: 'Le quota disque est atteint. Libérez de l’espace ou contactez l’administration.',
  credits_exhausted: 'Aucun crédit de génération disponible. Contactez l’administration.',
  invalid_credentials: 'Adresse mail ou mot de passe incorrect.',
  invalid_factor: 'Le code est incorrect ou a déjà été utilisé.',
  mfa_required: 'Saisissez le code de votre application ou un code de récupération.',
  mfa_setup_required: 'Activez la double authentification pour accéder à cet espace.',
  password_change_required: 'Modifiez votre mot de passe temporaire pour continuer.',
  unauthenticated: 'Connectez-vous pour continuer.',
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
    const english = document.documentElement.lang === 'en' && location.pathname.replace(/\/$/, '') === '/server/admin';
    const message = !english && Object.hasOwn(errors, code) ? errors[code] : detail;
    const fallback = english ? 'The server could not process the request.' : 'Le serveur ne peut pas traiter cette demande.';
    const invalid = english ? 'Check the information you entered.' : 'Vérifiez les informations saisies.';
    const error = new Error(message || (response.status === 422 ? invalid : fallback));
    error.status = response.status;
    error.code = code;
    throw error;
  }
  return result;
}

export const getUser = payload => payload.user || payload;
export const nameOf = user => [user.first_name, user.last_name].filter(Boolean).join(' ') || user.email || 'Compte TRELLIS';
export const diskLimit = user => user.disk_limit_bytes ?? user.disk_limit ?? null;
export const diskUsed = user => user.disk_used_bytes ?? user.used_bytes ?? 0;
export const availableCredits = user => user.credits_available ?? Math.max(0, (user.credits || 0) - (user.credits_reserved || 0));
export const formatNumber = (value, language = 'fr') => numbers[language].format(value);
export function formatBytes(value, language = 'fr') {
  if (!Number.isFinite(value) || value < 0) return '—';
  const byte = language === 'en' ? 'B' : 'o';
  if (value >= 1024 ** 4) return `${formatNumber(value / 1024 ** 4, language)} Ti${byte}`;
  if (value >= 1024 ** 3) return `${formatNumber(value / 1024 ** 3, language)} Gi${byte}`;
  if (value >= 1024 ** 2) return `${formatNumber(value / 1024 ** 2, language)} Mi${byte}`;
  if (value >= 1024) return `${formatNumber(value / 1024, language)} Ki${byte}`;
  return `${formatNumber(value, language)} ${byte}`;
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
    developerLink.title = 'Documentation API et MCP';
    developerLink.setAttribute('aria-label', developerLink.title);
  }
  const edition = document.querySelector('.brand .version');
  if (edition) edition.textContent = 'SERVEUR';
  const local = document.querySelector('.rail-bottom .local-badge');
  const railBottom = local?.parentElement;
  const creditBadge = element('a', 'server-credit-badge');
  creditBadge.href = '/server/account#usage';
  creditBadge.setAttribute('aria-label', 'Mon utilisation');
  const creditCount = element('span', 'server-credit-count', '—');
  const creditLabel = element('span', 'server-credit-label', 'Crédits');
  creditBadge.append(creditCount, creditLabel);
  local?.replaceWith(creditBadge);
  const privacy = document.querySelector('.rail-bottom small');
  if (privacy) privacy.textContent = 'Votre bibliothèque sur le serveur.';
  const importFile = document.getElementById('importFile');
  if (importFile) importFile.accept = '.glb,model/gltf-binary';
  const importHint = document.querySelector('#importForm > p');
  if (importHint) importHint.textContent = 'Importez un fichier GLB autonome avec ses textures intégrées (500 Mo maximum).';
  const animation = document.getElementById('animationLibraryControls');
  if (animation) animation.prepend(element('p', 'hint', 'Pour ajouter ou modifier un clip, ouvrez le modèle dans TRELLIS Desktop.'));

  const menu = element('details', 'server-shell');
  const summary = element('summary');
  summary.setAttribute('aria-label', 'Menu de votre compte TRELLIS');
  const icon = element('span', 'server-avatar', 'T');
  const title = element('span', 'server-shell-name', 'Mon compte');
  summary.append(icon, title, element('span', '', '⌄'));
  const items = element('div', 'server-shell-menu');
  const usage = element('p');
  usage.hidden = true;
  const profileLink = element('a', '', 'Mon profil');
  profileLink.href = '/server/account';
  const usageLink = element('a', '', 'Mon utilisation');
  usageLink.href = '/server/account#usage';
  const securityLink = element('a', '', 'Sécurité');
  securityLink.href = '/server/account#security';
  const adminLink = element('a', '', 'Administration');
  adminLink.href = '/server/admin';
  adminLink.hidden = true;
  const logout = element('button', 'server-shell-logout', 'Déconnexion');
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
    const label = available === 1 ? 'Crédit' : 'Crédits';
    const balance = available === null ? 'Crédits illimités' : `${count} ${label.toLowerCase()} ${available === 1 ? 'disponible' : 'disponibles'}`;
    if (creditCount.textContent !== count) creditCount.textContent = count;
    creditLabel.textContent = label;
    creditBadge.title = balance;
    creditBadge.setAttribute('aria-label', `${balance} · Mon utilisation`);
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
      usage.textContent = user.role === 'admin'
        ? 'Disque et crédits illimités'
        : `${formatBytes(diskUsed(user))} / ${formatBytes(limit)} · ${formatNumber(availableCredits(user))} crédits`;
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
