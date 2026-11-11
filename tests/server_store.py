"""TRELLIS Server account and accounting regressions against a disposable database."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server_store import Store, StoreError, totp_step

PASSWORD = 'Example password 3'


def expect_error(call, status, code=None):
    try:
        call()
    except StoreError as error:
        assert error.status == status, error
        assert code is None or error.code == code, error
    else:
        raise AssertionError('Expected an account or quota rejection.')


def profile(name):
    return dict(first_name=name, last_name='Test', email=name + '@example.test')


def run():
    with TemporaryDirectory(prefix='trellis-server-store-') as directory:
        path = Path(directory) / 'studio.sqlite3'
        store = Store(path)
        assert not store.has_admin()
        admin = store.bootstrap_admin(profile('Admin'), PASSWORD)['id']
        assert store.has_admin() and Store(path).has_admin()
        expect_error(lambda: store.bootstrap_admin(profile('Extra'), PASSWORD), 409)
        assert store.get_user(admin)['disk_limit_bytes'] is None
        assert store.get_user(admin)['credits_available'] is None
        user = store.register(profile('Artist'), PASSWORD)['id']
        other = store.register(profile('Other'), PASSWORD)['id']
        expect_error(lambda: store.login('artist@example.test', PASSWORD), 403, 'account_inactive')
        assert store.get_user(user)['status'] == 'pending'
        store.update_user(admin, user, dict(status='active', disk_limit_bytes=500, credits=1, reason='Pilot allowance'))
        store.update_user(admin, other, dict(status='active'))
        session = store.login('ARTIST@example.test', PASSWORD)
        token = session['token']
        assert store.authenticate(token)['id'] == user
        assert not {'password', 'recovery_codes', 'totp_secret'} & session['user'].keys()

        # Reopen a v1 catalog containing accounts and a live browser session.
        with store.transaction() as database:
            database.execute('DROP TABLE api_tokens')
            database.execute('PRAGMA user_version=1')
        store = Store(path)
        assert store.authenticate(token)['id'] == user
        expect_error(lambda: store.create_api_token(admin, 'Agent'), 403, 'mfa_setup_required')
        credential = store.create_api_token(other, 'Workstation')
        personal_token = credential['token']
        assert store.authenticate_api(personal_token)['id'] == other
        assert store.list_api_tokens(user) == []
        assert store.list_api_tokens(other) == [{key: value for key, value in credential.items() if key != 'token'}]
        with store.connect() as database:
            assert database.execute('PRAGMA user_version').fetchone()[0] == 2
            assert personal_token not in str(tuple(database.execute('SELECT * FROM api_tokens').fetchone()))
        expect_error(lambda: store.authenticate_api(personal_token[:-1] + ('B' if personal_token[-1] == 'A' else 'A')), 401)
        expect_error(lambda: store.revoke_api_token(user, credential['id']), 404)
        store.revoke_api_token(other, credential['id'])
        expect_error(lambda: store.authenticate_api(personal_token), 401)
        personal_token = store.create_api_token(other, 'Worker')['token']
        store.change_password(other, PASSWORD, 'Changed password 5')
        expect_error(lambda: store.authenticate_api(personal_token), 401)
        personal_token = store.create_api_token(other, 'Worker')['token']
        store.reset_password(admin, other)
        expect_error(lambda: store.authenticate_api(personal_token), 401)
        expect_error(lambda: store.create_api_token(other, 'Worker'), 403, 'password_change_required')

        expect_error(lambda: store.update_user(user, user, {'disk_limit_bytes': 5000}), 403)
        expect_error(lambda: store.list_users(user), 403)
        expect_error(lambda: store.ledger(user, other), 403)
        expect_error(lambda: store.update_user(admin, admin, {'status': 'disabled'}), 409)
        expect_error(lambda: store.update_profile(user, {'role': 'admin'}), 400)

        # Either all reservations are accepted or none are inserted.
        expect_error(lambda: store.reserve_jobs(user, [{'job_id': 'first'}, {'job_id': 'second'}]), 402)
        assert store.jobs(user) == []
        start = Barrier(2)

        def compete(identifier):
            start.wait()
            try:
                store.reserve_job(user, identifier, reserve_bytes=200)
                return identifier
            except StoreError as error:
                assert error.code == 'credits_exhausted'
                return None

        with ThreadPoolExecutor(2) as pool:
            winners = [value for value in pool.map(compete, ('first', 'second')) if value]
        assert len(winners) == 1
        winning_job, = winners
        assert store.get_user(user)['credits_reserved'] == 1
        assert store.get_user(user)['credits_available'] == 0
        expect_error(lambda: store.reserve_storage(user, 'upload', 301), 413)
        store.reserve_storage(user, 'upload', 300)
        assert store.get_user(user)['storage_reserved_bytes'] == 500
        store.clear_storage_reservations()
        assert store.get_user(user)['storage_reserved_bytes'] == 200
        store.finish_job(user, winning_job, 'error')
        assert store.get_user(user)['credits_available'] == 1
        store.reserve_job(user, 'retry')
        store.mark_running(user, 'retry')
        store.finish_job(user, 'retry', 'done')
        store.finish_job(user, 'retry', 'done')
        assert store.get_user(user)['credits_available'] == 0
        assert [entry['delta'] for entry in store.ledger(user)] == [-1, 1]
        store.reserve_job(user, 'remesh', generation=False, reserve_bytes=500)
        store.finish_job(user, 'remesh', 'cancelled')
        store.sync_usage(user, 600)
        expect_error(lambda: store.reserve_job(user, 'out-of-space', generation=False), 413)
        store.reserve_job(admin, 'unlimited', reserve_bytes=2 * 1024**4)
        store.finish_job(admin, 'unlimited', 'done')
        assert store.ledger(admin) == []

        setup = store.begin_totp(user, PASSWORD)
        assert setup['uri'].startswith('otpauth://totp/')
        # RFC 6238 SHA-1 reference key and code at epoch 59, truncated to six digits.
        assert totp_step('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ', '287082', now=59) == 1
        with patch('server_store.totp_step', return_value=1):
            codes = store.confirm_totp(user, '123456')
        expect_error(lambda: store.authenticate(token), 401)
        expect_error(lambda: store.login('artist@example.test', PASSWORD), 401, 'mfa_required')
        token = store.login('artist@example.test', PASSWORD, codes[0])['token']
        expect_error(lambda: store.login('artist@example.test', PASSWORD, codes[0]), 401, 'invalid_factor')
        personal_token = store.create_api_token(user, 'MCP')['token']
        store.update_user(admin, user, {'status': 'disabled'})
        expect_error(lambda: store.authenticate(token), 401)
        expect_error(lambda: store.authenticate_api(personal_token), 401)
        expect_error(lambda: store.reserve_job(user, 'disabled'), 403)
        assert Store(path).get_user(user)['status'] == 'disabled'
        assert any(event['action'] == 'account_updated' for event in store.audit(admin))

        store.begin_totp(admin, PASSWORD)
        with patch('server_store.totp_step', return_value=1):
            admin_codes = store.confirm_totp(admin, '123456')
        admin_token = store.login('admin@example.test', PASSWORD, admin_codes[0])['token']
        admin_api = store.create_api_token(admin, 'Admin workstation')['token']
        replacement = 'A new example password 4'
        expect_error(lambda: store.recover_admin('other@example.test', replacement), 404)
        assert store.recover_admin('ADMIN@example.test', replacement) is None
        expect_error(lambda: store.authenticate(admin_token), 401)
        expect_error(lambda: store.authenticate_api(admin_api), 401)
        expect_error(lambda: store.login('admin@example.test', PASSWORD), 401)
        assert not store.login('admin@example.test', replacement)['user']['totp_enabled']
        recovery = next(event for event in store.audit(admin) if event['action'] == 'administrator_recovered_locally')
        assert recovery['actor_id'] is None and recovery['target_id'] == admin
        assert replacement not in str(recovery)

    print('PASS: account isolation, quota reservations, concurrent credits, local recovery and personal API tokens')


if __name__ == '__main__':
    run()
