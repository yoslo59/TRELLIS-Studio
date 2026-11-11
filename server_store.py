"""Transactional accounts, sessions and resource accounting for TRELLIS Server."""
from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import struct
import time
from urllib.parse import quote, urlsplit
import uuid


DEFAULT_DISK_LIMIT = 5 * 1024**3
SESSION_SECONDS = 12 * 60 * 60
TERMINAL_STATES = ('done', 'error', 'cancelled')
API_TOKEN_FORMAT = re.compile(r'trl_([a-f0-9]{32})_[A-Za-z0-9_-]{43}')


class StoreError(ValueError):
    def __init__(self, message, status=400, code='invalid_request'):
        super().__init__(message)
        self.status = status
        self.code = code


def password_hash(password, *, temporary=False):
    minimum = 8 if temporary else 12
    if not isinstance(password, str) or not minimum <= len(password) <= 1024:
        raise StoreError(f'Use a password of {minimum}–1024 characters.')
    salt = secrets.token_bytes(16)
    try:
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    except UnicodeEncodeError as error:
        raise StoreError('Use valid Unicode characters in your password.') from error
    return salt.hex() + ':' + digest.hex()


def password_matches(password, encoded, *, temporary=False):
    if not isinstance(password, str) or not (8 if temporary else 12) <= len(password) <= 1024:
        return False
    try:
        salt, expected = encoded.split(':')
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def normalized_login_email(email):
    if not isinstance(email, str) or len(email) > 254:
        return ''
    normalized = email.strip().casefold()
    try:
        normalized.encode()
    except UnicodeEncodeError:
        return ''
    return normalized if len(normalized) <= 254 else ''


def clean_profile(data, partial=False):
    allowed = {'first_name', 'last_name', 'email', 'bio', 'website', 'social_links'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise StoreError('Unknown profile fields.')
    result = {}
    limits = {'first_name': 80, 'last_name': 80, 'email': 254, 'bio': 2000, 'website': 500}
    for key, limit in limits.items():
        if partial and key not in data:
            continue
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise StoreError('Invalid ' + key.replace('_', ' ') + '.')
        value = value.strip()
        if key in ('first_name', 'last_name', 'email') and not value:
            raise StoreError('First name, last name and email are required.')
        if key == 'email':
            value = normalized_login_email(value)
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
                raise StoreError('Enter a valid email address.')
        if key == 'website' and value:
            validate_url(value)
        result[key] = value
    if not partial or 'social_links' in data:
        links = data.get('social_links', [])
        if isinstance(links, dict):
            if set(links) - {'linkedin', 'github', 'mastodon', 'instagram', 'other'}:
                raise StoreError('Choose supported social links.')
            values = links.values()
        elif isinstance(links, list) and len(links) <= 8:
            values = links
        else:
            raise StoreError('Use at most eight social links.')
        for link in values:
            validate_url(link)
        result['social_links'] = json.dumps(links, ensure_ascii=False)
    return result


def validate_url(value):
    if not isinstance(value, str) or len(value) > 500:
        raise StoreError('Enter a valid website or social URL.')
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise StoreError('Enter a valid website or social URL.') from error
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise StoreError('Website and social links must use http or https.')


def totp_step(secret, code, now=None):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
        return None
    key = base64.b32decode(secret)
    current = int((time.time() if now is None else now) // 30)
    for step in (current, current - 1, current + 1):
        digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
        offset = digest[-1] & 15
        number = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000
        if hmac.compare_digest(code, f'{number:06d}'):
            return step
    return None


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise StoreError('This catalog needs a newer TRELLIS Server version.', 503)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL,
                    status TEXT NOT NULL, bio TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '',
                    social_links TEXT NOT NULL DEFAULT '[]', avatar TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL, last_login REAL, disk_limit_bytes INTEGER,
                    disk_used_bytes INTEGER NOT NULL DEFAULT 0, credits INTEGER NOT NULL DEFAULT 0,
                    password_change_required INTEGER NOT NULL DEFAULT 0,
                    totp_secret TEXT, pending_totp TEXT, totp_last_step INTEGER NOT NULL DEFAULT -1,
                    recovery_codes TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS sessions (
                    hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    created REAL NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    hash TEXT NOT NULL, label TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS api_tokens_owner ON api_tokens(user_id, created);
                CREATE TABLE IF NOT EXISTS jobs (
                    user_id TEXT NOT NULL REFERENCES users(id), id TEXT NOT NULL,
                    state TEXT NOT NULL, credit_cost INTEGER NOT NULL, reserve_bytes INTEGER NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(user_id, id));
                CREATE TABLE IF NOT EXISTS storage_reservations (
                    user_id TEXT NOT NULL REFERENCES users(id), id TEXT NOT NULL, bytes INTEGER NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(user_id, id));
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    actor_id TEXT, job_id TEXT, delta INTEGER NOT NULL, reason TEXT NOT NULL,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, actor_id TEXT, target_id TEXT,
                    action TEXT NOT NULL, details TEXT NOT NULL, created REAL NOT NULL);
                PRAGMA user_version=2;
                COMMIT;
            ''')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=15000')
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                yield connection

    def _user(self, connection, identifier, active=False):
        row = connection.execute('SELECT * FROM users WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise StoreError('Account not found.', 404)
        if active and row['status'] != 'active':
            raise StoreError('This account is not active.', 403, 'account_inactive')
        return row

    def _admin(self, connection, actor_id):
        user = self._user(connection, actor_id, active=True)
        if user['role'] != 'admin':
            raise StoreError('Administrator access required.', 403)
        return user

    def _audit(self, connection, actor_id, target_id, action, details=None):
        connection.execute('INSERT INTO audit(actor_id,target_id,action,details,created) VALUES(?,?,?,?,?)',
                           (actor_id, target_id, action, json.dumps(details or {}), time.time()))

    def _public(self, connection, row):
        reserved = connection.execute('SELECT COALESCE(SUM(credit_cost),0),COALESCE(SUM(reserve_bytes),0) '
                                      'FROM jobs WHERE user_id=? AND state IN (\'reserved\',\'running\')',
                                      (row['id'],)).fetchone()
        storage = connection.execute('SELECT COALESCE(SUM(bytes),0) FROM storage_reservations WHERE user_id=?',
                                     (row['id'],)).fetchone()[0]
        fields = ('id', 'email', 'first_name', 'last_name', 'role', 'status', 'bio', 'website', 'avatar',
                  'created', 'last_login', 'disk_limit_bytes', 'disk_used_bytes', 'credits',
                  'password_change_required')
        result = {key: row[key] for key in fields}
        admin = row['role'] == 'admin'
        result.update(social_links=json.loads(row['social_links']), credits_reserved=0 if admin else reserved[0],
                      credits_available=None if admin else max(0, row['credits'] - reserved[0]),
                      storage_reserved_bytes=reserved[1] + storage, totp_enabled=bool(row['totp_secret']))
        if admin:
            result['disk_limit_bytes'] = None
        return result

    def initialized(self):
        with self.connect() as connection:
            return bool(connection.execute('SELECT 1 FROM users LIMIT 1').fetchone())

    def has_admin(self):
        with self.connect() as connection:
            return bool(connection.execute('SELECT 1 FROM users WHERE role=\'admin\' AND status=\'active\' LIMIT 1').fetchone())

    def _insert_user(self, connection, profile, password, role, status, disk_limit, credits, temporary=False):
        profile = clean_profile(profile)
        identifier = uuid.uuid4().hex
        values = dict(id=identifier, **profile, password=password_hash(password, temporary=temporary), role=role, status=status,
                      created=time.time(), disk_limit_bytes=None if role == 'admin' else disk_limit,
                      credits=credits, password_change_required=int(temporary))
        keys = list(values)
        try:
            connection.execute(f'INSERT INTO users({",".join(keys)}) VALUES({",".join("?" for _ in keys)})',
                               [values[key] for key in keys])
        except sqlite3.IntegrityError as error:
            raise StoreError('An account already uses this email address.', 409) from error
        return self._public(connection, self._user(connection, identifier))

    def bootstrap_admin(self, profile, password, *, temporary=False):
        with self.transaction() as connection:
            if connection.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise StoreError('The server has already been configured.', 409)
            user = self._insert_user(connection, profile, password, 'admin', 'active', None, 0, temporary)
            self._audit(connection, user['id'], user['id'], 'server_initialized')
            return user

    def register(self, profile, password):
        with self.transaction() as connection:
            if not connection.execute('SELECT 1 FROM users WHERE role=\'admin\'').fetchone():
                raise StoreError('Complete server installation first.', 503)
            user = self._insert_user(connection, profile, password, 'user', 'pending', DEFAULT_DISK_LIMIT, 0)
            self._audit(connection, user['id'], user['id'], 'account_requested')
            return user

    def create_user(self, actor_id, profile, password=None, disk_limit_bytes=DEFAULT_DISK_LIMIT, credits=0):
        self._valid_limits(disk_limit_bytes, credits)
        generated = password is None
        password = password or secrets.token_urlsafe(18)
        with self.transaction() as connection:
            self._admin(connection, actor_id)
            user = self._insert_user(connection, profile, password, 'user', 'active', disk_limit_bytes, credits)
            connection.execute('UPDATE users SET password_change_required=1 WHERE id=?', (user['id'],))
            if credits:
                connection.execute('INSERT INTO ledger(user_id,actor_id,delta,reason,created) VALUES(?,?,?,?,?)',
                                   (user['id'], actor_id, credits, 'Initial credit allocation', time.time()))
            self._audit(connection, actor_id, user['id'], 'account_created')
            user = self._public(connection, self._user(connection, user['id']))
            return dict(user=user, temporary_password=password if generated else None)

    def _valid_limits(self, disk_limit_bytes, credits):
        if type(disk_limit_bytes) is not int or not 0 <= disk_limit_bytes <= 2**60:
            raise StoreError('Choose a non-negative disk quota in bytes.')
        if type(credits) is not int or not 0 <= credits <= 1_000_000_000:
            raise StoreError('Choose a non-negative number of credits.')

    def get_user(self, identifier):
        with self.connect() as connection:
            return self._public(connection, self._user(connection, identifier))

    def list_users(self, actor_id):
        with self.connect() as connection:
            self._admin(connection, actor_id)
            return [self._public(connection, row) for row in connection.execute('SELECT * FROM users ORDER BY created')]

    def update_user(self, actor_id, identifier, changes):
        allowed = {'role', 'status', 'disk_limit_bytes', 'credits', 'credit_delta', 'reason'}
        profile_keys = {'first_name', 'last_name', 'email', 'bio', 'website', 'social_links'}
        if not isinstance(changes, dict) or set(changes) - allowed - profile_keys:
            raise StoreError('Unknown administrative account fields.')
        values = clean_profile({key: value for key, value in changes.items() if key in profile_keys}, partial=True)
        with self.transaction() as connection:
            self._admin(connection, actor_id)
            current = self._user(connection, identifier)
            role = changes.get('role', current['role'])
            status = changes.get('status', current['status'])
            if role not in ('admin', 'user') or status not in ('active', 'pending', 'disabled'):
                raise StoreError('Choose a valid role and account status.')
            if current['role'] == 'admin' and current['status'] == 'active' and (role != 'admin' or status != 'active'):
                count = connection.execute('SELECT COUNT(*) FROM users WHERE role=\'admin\' AND status=\'active\'').fetchone()[0]
                if count <= 1:
                    raise StoreError('Keep at least one active administrator.', 409)
            limit = changes.get('disk_limit_bytes', current['disk_limit_bytes'])
            if role == 'admin':
                limit = None
            elif limit is None:
                limit = DEFAULT_DISK_LIMIT
            delta = changes.get('credit_delta', 0)
            if type(delta) is not int or abs(delta) > 1_000_000_000 or ('credits' in changes and 'credit_delta' in changes):
                raise StoreError('Choose one credit adjustment.')
            credits = changes.get('credits', current['credits'] + delta)
            self._valid_limits(0 if role == 'admin' else limit, credits)
            if credits != current['credits']:
                reason = changes.get('reason', '')
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                    raise StoreError('Give a reason for the credit adjustment.')
                reserved = connection.execute('SELECT COALESCE(SUM(credit_cost),0) FROM jobs WHERE user_id=? '
                                              'AND state IN (\'reserved\',\'running\')', (identifier,)).fetchone()[0]
                if credits < reserved:
                    raise StoreError('These credits are reserved by queued jobs.', 409)
                connection.execute('INSERT INTO ledger(user_id,actor_id,delta,reason,created) VALUES(?,?,?,?,?)',
                                   (identifier, actor_id, credits - current['credits'], reason.strip(), time.time()))
            values.update(role=role, status=status, disk_limit_bytes=limit, credits=credits)
            self._update(connection, identifier, values)
            if status != 'active' or role != current['role'] or ('email' in values and values['email'] != current['email']):
                self._revoke_access(connection, identifier)
            self._audit(connection, actor_id, identifier, 'account_updated', {k: v for k, v in changes.items() if k != 'reason'})
            return self._public(connection, self._user(connection, identifier))

    def _update(self, connection, identifier, values):
        if not values:
            return
        try:
            connection.execute('UPDATE users SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                               [*values.values(), identifier])
        except sqlite3.IntegrityError as error:
            raise StoreError('An account already uses this email address.', 409) from error

    def update_profile(self, identifier, changes):
        values = clean_profile(changes, partial=True)
        with self.transaction() as connection:
            current = self._user(connection, identifier, active=True)
            if 'email' in values and values['email'] != current['email']:
                raise StoreError('Ask the administrator to change your login email.')
            self._update(connection, identifier, values)
            self._audit(connection, identifier, identifier, 'profile_updated')
            return self._public(connection, self._user(connection, identifier))

    def set_avatar(self, identifier, filename):
        if not isinstance(filename, str) or (filename and not re.fullmatch(r'[a-f0-9]{32}\.png', filename)):
            raise StoreError('Invalid avatar filename.')
        with self.transaction() as connection:
            self._user(connection, identifier, active=True)
            connection.execute('UPDATE users SET avatar=? WHERE id=?', (filename, identifier))
            return self._public(connection, self._user(connection, identifier))

    def verify_password(self, identifier, password):
        with self.connect() as connection:
            return password_matches(password, self._user(connection, identifier, active=True)['password'])

    def _consume_factor(self, connection, user, code):
        if not user['totp_secret']:
            return
        if not code:
            raise StoreError('Enter your authentication code.', 401, 'mfa_required')
        step = totp_step(user['totp_secret'], code)
        if step is not None and step > user['totp_last_step']:
            connection.execute('UPDATE users SET totp_last_step=? WHERE id=?', (step, user['id']))
            return
        hashes = json.loads(user['recovery_codes'])
        hashed = token_hash(str(code).strip().replace('-', '').lower())
        match = next((value for value in hashes if hmac.compare_digest(value, hashed)), None)
        if match:
            hashes.remove(match)
            connection.execute('UPDATE users SET recovery_codes=? WHERE id=?', (json.dumps(hashes), user['id']))
            return
        raise StoreError('Invalid or already used authentication code.', 401, 'invalid_factor')

    def login(self, email, password, code=''):
        email = normalized_login_email(email)
        if not isinstance(code, str) or len(code) > 64:
            raise StoreError('Invalid authentication code.', 401, 'invalid_factor')
        with self.transaction() as connection:
            user = connection.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            encoded = user['password'] if user else '0' * 32 + ':' + '0' * 128
            if not password_matches(password, encoded, temporary=bool(user and user['password_change_required'])) or user is None:
                raise StoreError('Invalid email or password.', 401, 'invalid_credentials')
            self._user(connection, user['id'], active=True)
            self._consume_factor(connection, user, code)
            token = secrets.token_urlsafe(32)
            now = time.time()
            connection.execute('DELETE FROM sessions WHERE expires<?', (now,))
            connection.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token_hash(token), user['id'], now, now + SESSION_SECONDS))
            connection.execute('UPDATE users SET last_login=? WHERE id=?', (now, user['id']))
            self._audit(connection, user['id'], user['id'], 'login')
            return dict(token=token, user=self._public(connection, self._user(connection, user['id'])))

    def authenticate(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise StoreError('Sign in to continue.', 401, 'unauthenticated')
        with self.connect() as connection:
            row = connection.execute('SELECT user_id FROM sessions WHERE hash=? AND expires>?',
                                     (token_hash(token), time.time())).fetchone()
            if row is None:
                raise StoreError('Sign in to continue.', 401, 'unauthenticated')
            return self._public(connection, self._user(connection, row['user_id'], active=True))

    def _check_api_account(self, user):
        if user['password_change_required']:
            raise StoreError('Change your temporary password first.', 403, 'password_change_required')
        if user['role'] == 'admin' and not user['totp_secret']:
            raise StoreError('Set up two-factor authentication first.', 403, 'mfa_setup_required')

    def create_api_token(self, identifier, label):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or not label.strip().isprintable():
            raise StoreError('Use a token label of 1–80 characters.')
        label = label.strip()
        token_id = uuid.uuid4().hex
        secret = f'trl_{token_id}_{secrets.token_urlsafe(32)}'
        created = time.time()
        with self.transaction() as connection:
            self._check_api_account(self._user(connection, identifier, active=True))
            connection.execute('INSERT INTO api_tokens(id,user_id,hash,label,created) VALUES(?,?,?,?,?)',
                               (token_id, identifier, token_hash(secret), label, created))
            self._audit(connection, identifier, identifier, 'api_token_created', {'token_id': token_id, 'label': label})
        return dict(id=token_id, label=label, prefix=f'trl_{token_id[:8]}', created=created, token=secret)

    def list_api_tokens(self, identifier):
        with self.connect() as connection:
            self._user(connection, identifier, active=True)
            rows = connection.execute('SELECT id,label,created FROM api_tokens WHERE user_id=? ORDER BY created DESC',
                                      (identifier,))
            return [dict(row, prefix=f'trl_{row["id"][:8]}') for row in rows]

    def revoke_api_token(self, identifier, token_id):
        with self.transaction() as connection:
            self._user(connection, identifier, active=True)
            deleted = connection.execute('DELETE FROM api_tokens WHERE id=? AND user_id=?', (token_id, identifier))
            if not deleted.rowcount:
                raise StoreError('API token not found.', 404)
            self._audit(connection, identifier, identifier, 'api_token_revoked', {'token_id': token_id})

    def authenticate_api(self, token):
        match = API_TOKEN_FORMAT.fullmatch(token) if isinstance(token, str) else None
        if not match:
            raise StoreError('Invalid API token.', 401, 'invalid_api_token')
        with self.connect() as connection:
            row = connection.execute('SELECT user_id,hash FROM api_tokens WHERE id=?', (match[1],)).fetchone()
            if row is None or not hmac.compare_digest(row['hash'], token_hash(token)):
                raise StoreError('Invalid API token.', 401, 'invalid_api_token')
            user = self._user(connection, row['user_id'], active=True)
            self._check_api_account(user)
            return self._public(connection, user)

    def _revoke_access(self, connection, identifier):
        connection.execute('DELETE FROM sessions WHERE user_id=?', (identifier,))
        connection.execute('DELETE FROM api_tokens WHERE user_id=?', (identifier,))

    def logout(self, token):
        with self.transaction() as connection:
            connection.execute('DELETE FROM sessions WHERE hash=?', (token_hash(token),))

    def revoke_sessions(self, actor_id, identifier):
        with self.transaction() as connection:
            if actor_id != identifier:
                self._admin(connection, actor_id)
            self._user(connection, actor_id, active=True)
            connection.execute('DELETE FROM sessions WHERE user_id=?', (identifier,))
            self._audit(connection, actor_id, identifier, 'sessions_revoked')

    def change_password(self, identifier, current, new):
        encoded = password_hash(new)
        with self.transaction() as connection:
            user = self._user(connection, identifier, active=True)
            if not password_matches(current, user['password'], temporary=bool(user['password_change_required'])):
                raise StoreError('Current password is incorrect.', 403)
            connection.execute('UPDATE users SET password=?,password_change_required=0 WHERE id=?', (encoded, identifier))
            self._revoke_access(connection, identifier)
            self._audit(connection, identifier, identifier, 'password_changed')

    def reset_password(self, actor_id, identifier):
        password = secrets.token_urlsafe(18)
        encoded = password_hash(password)
        with self.transaction() as connection:
            self._admin(connection, actor_id)
            self._user(connection, identifier)
            connection.execute('UPDATE users SET password=?,password_change_required=1 WHERE id=?', (encoded, identifier))
            self._revoke_access(connection, identifier)
            self._audit(connection, actor_id, identifier, 'password_reset')
        return password

    def recover_admin(self, email, password):
        """Reset an active administrator from the local server console."""
        encoded = password_hash(password)
        with self.transaction() as connection:
            user = connection.execute("SELECT id FROM users WHERE email=? AND role='admin' AND status='active'",
                                      (normalized_login_email(email),)).fetchone()
            if user is None:
                raise StoreError('Active administrator account not found.', 404)
            identifier = user['id']
            connection.execute('UPDATE users SET password=?,password_change_required=0,totp_secret=NULL,'
                               "pending_totp=NULL,recovery_codes='[]',totp_last_step=-1 WHERE id=?",
                               (encoded, identifier))
            self._revoke_access(connection, identifier)
            self._audit(connection, None, identifier, 'administrator_recovered_locally')

    def begin_totp(self, identifier, current_password):
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        with self.transaction() as connection:
            user = self._user(connection, identifier, active=True)
            if not password_matches(current_password, user['password']):
                raise StoreError('Current password is incorrect.', 403)
            if user['totp_secret']:
                raise StoreError('Disable the existing authenticator first.', 409)
            connection.execute('UPDATE users SET pending_totp=? WHERE id=?', (secret, identifier))
            uri = 'otpauth://totp/' + quote('TRELLIS Studio:' + user['email'], safe='')
            return dict(secret=secret, uri=uri + '?secret=' + secret + '&issuer=TRELLIS%20Studio&algorithm=SHA1&digits=6&period=30')

    def confirm_totp(self, identifier, code):
        with self.transaction() as connection:
            user = self._user(connection, identifier, active=True)
            secret = user['pending_totp']
            step = totp_step(secret, code) if secret else None
            if step is None:
                raise StoreError('Enter the current code from the authenticator.')
            codes = [secrets.token_hex(8) for _ in range(10)]
            hashes = [token_hash(code) for code in codes]
            connection.execute('UPDATE users SET totp_secret=?,pending_totp=NULL,totp_last_step=?,recovery_codes=? WHERE id=?',
                               (secret, step, json.dumps(hashes), identifier))
            self._revoke_access(connection, identifier)
            self._audit(connection, identifier, identifier, 'totp_enabled')
            return codes

    def disable_totp(self, identifier, current_password, code):
        with self.transaction() as connection:
            user = self._user(connection, identifier, active=True)
            if not password_matches(current_password, user['password']):
                raise StoreError('Current password is incorrect.', 403)
            self._consume_factor(connection, user, code)
            connection.execute('UPDATE users SET totp_secret=NULL,pending_totp=NULL,recovery_codes=\'[]\',totp_last_step=-1 WHERE id=?',
                               (identifier,))
            self._revoke_access(connection, identifier)
            self._audit(connection, identifier, identifier, 'totp_disabled')

    def reset_totp(self, actor_id, identifier):
        with self.transaction() as connection:
            self._admin(connection, actor_id)
            self._user(connection, identifier)
            connection.execute('UPDATE users SET totp_secret=NULL,pending_totp=NULL,recovery_codes=\'[]\',totp_last_step=-1 WHERE id=?',
                               (identifier,))
            self._revoke_access(connection, identifier)
            self._audit(connection, actor_id, identifier, 'totp_reset')

    def sync_usage(self, identifier, used_bytes):
        if type(used_bytes) is not int or used_bytes < 0:
            raise StoreError('Invalid disk usage measurement.')
        with self.transaction() as connection:
            self._user(connection, identifier)
            connection.execute('UPDATE users SET disk_used_bytes=? WHERE id=?', (used_bytes, identifier))

    def _check_storage(self, connection, user, amount):
        if type(amount) is not int or not 0 <= amount <= 2**60:
            raise StoreError('Invalid disk reservation.')
        public = self._public(connection, user)
        if user['role'] != 'admin' and user['disk_used_bytes'] + public['storage_reserved_bytes'] + amount > user['disk_limit_bytes']:
            raise StoreError('Disk quota reached. Free space or ask the administrator.', 413, 'disk_quota')

    def reserve_storage(self, identifier, reservation_id, amount):
        with self.transaction() as connection:
            user = self._user(connection, identifier, active=True)
            if connection.execute('SELECT 1 FROM storage_reservations WHERE user_id=? AND id=?',
                                  (identifier, reservation_id)).fetchone():
                raise StoreError('This storage operation is already reserved.', 409)
            self._check_storage(connection, user, amount)
            connection.execute('INSERT INTO storage_reservations VALUES(?,?,?,?)',
                               (identifier, reservation_id, amount, time.time()))

    def release_storage(self, identifier, reservation_id):
        with self.transaction() as connection:
            connection.execute('DELETE FROM storage_reservations WHERE user_id=? AND id=?', (identifier, reservation_id))

    def clear_storage_reservations(self):
        """Reclaim request-scoped reservations after the only server process restarts."""
        with self.transaction() as connection:
            connection.execute('DELETE FROM storage_reservations')

    def reserve_job(self, identifier, job_id, generation=True, reserve_bytes=0):
        return self.reserve_jobs(identifier, [dict(job_id=job_id, generation=generation, reserve_bytes=reserve_bytes)])[0]

    def reserve_jobs(self, identifier, jobs):
        ids = [job['job_id'] for job in jobs]
        if len(ids) != len(set(ids)):
            raise StoreError('The job batch contains duplicate IDs.')
        with self.transaction() as connection:
            user = self._user(connection, identifier, active=True)
            result = []
            for job in jobs:
                job_id = job['job_id']
                existing = connection.execute('SELECT * FROM jobs WHERE user_id=? AND id=?', (identifier, job_id)).fetchone()
                if existing:
                    result.append(dict(existing))
                    continue
                cost = int(bool(job.get('generation', True)) and user['role'] != 'admin')
                if cost and self._public(connection, user)['credits_available'] < cost:
                    raise StoreError('No generation credit available. Ask the administrator.', 402, 'credits_exhausted')
                reserve_bytes = job.get('reserve_bytes', 0)
                self._check_storage(connection, user, reserve_bytes)
                now = time.time()
                connection.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',
                                   (identifier, job_id, 'reserved', cost, reserve_bytes, now, now))
                result.append(dict(connection.execute('SELECT * FROM jobs WHERE user_id=? AND id=?',
                                                     (identifier, job_id)).fetchone()))
            return result

    def mark_running(self, identifier, job_id):
        with self.transaction() as connection:
            connection.execute('UPDATE jobs SET state=\'running\',updated=? WHERE user_id=? AND id=? AND state=\'reserved\'',
                               (time.time(), identifier, job_id))

    def finish_job(self, identifier, job_id, state):
        if state not in TERMINAL_STATES:
            raise StoreError('Choose a terminal job state.')
        with self.transaction() as connection:
            job = connection.execute('SELECT * FROM jobs WHERE user_id=? AND id=?', (identifier, job_id)).fetchone()
            if job is None:
                raise StoreError('Job reservation not found.', 404)
            if job['state'] in TERMINAL_STATES:
                return dict(job)
            if state == 'done' and job['credit_cost'] and self._user(connection, identifier)['role'] != 'admin':
                connection.execute('UPDATE users SET credits=credits-? WHERE id=?', (job['credit_cost'], identifier))
                connection.execute('INSERT INTO ledger(user_id,job_id,delta,reason,created) VALUES(?,?,?,?,?)',
                                   (identifier, job_id, -job['credit_cost'], 'Generation completed', time.time()))
            connection.execute('UPDATE jobs SET state=?,reserve_bytes=0,updated=? WHERE user_id=? AND id=?',
                               (state, time.time(), identifier, job_id))
            return dict(connection.execute('SELECT * FROM jobs WHERE user_id=? AND id=?', (identifier, job_id)).fetchone())

    def jobs(self, identifier=None):
        with self.connect() as connection:
            if identifier is None:
                return [dict(row) for row in connection.execute('SELECT * FROM jobs ORDER BY created')]
            return [dict(row) for row in connection.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY created', (identifier,))]

    def list_jobs(self, identifier=None):
        return self.jobs(identifier)

    def ledger(self, actor_id, identifier=None):
        identifier = identifier or actor_id
        with self.connect() as connection:
            self._user(connection, actor_id, active=True)
            if identifier != actor_id:
                self._admin(connection, actor_id)
            return [dict(row) for row in connection.execute('SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 500',
                                                           (identifier,))]

    def user_ledger(self, actor_id):
        return self.ledger(actor_id)

    def audit(self, actor_id):
        with self.connect() as connection:
            self._admin(connection, actor_id)
            return [dict(row) for row in connection.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 1000')]

    def all_users(self):
        """Internal scheduler inventory; never expose without an administrator check."""
        with self.connect() as connection:
            return [self._public(connection, row) for row in connection.execute('SELECT * FROM users ORDER BY created')]
