"""Configure the Windows server and complete its one-time local browser setup."""
from argparse import ArgumentParser
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import ssl
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parent


def active_administrator(data_dir):
    database = data_dir / 'server.sqlite3'
    if not database.is_file():
        return False
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        tables = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        return bool(tables and connection.execute("SELECT 1 FROM users WHERE role='admin' AND status='active' LIMIT 1").fetchone())
    finally:
        connection.close()


def settings(raw, config_path):
    directory = Path(raw['data_dir']).expanduser()
    if not directory.is_absolute():
        raise ValueError('Choose an absolute data folder on a disk of this server.')
    directory = directory.resolve()
    protected = [ROOT / name for name in ('outputs', 'trash', 'exports', 'config', 'models', 'engine', 'web', '.venv')]
    if ROOT.is_relative_to(directory) or any(directory.is_relative_to(p) or p.is_relative_to(directory) for p in protected):
        raise ValueError('Choose a data folder separate from the program and the desktop library.')
    if config_path.is_file():
        previous = Path(json.loads(config_path.read_text(encoding='utf-8'))['data_dir']).resolve()
        if directory != previous:
            raise ValueError('The server is already configured. Keep its data folder to retain accounts and libraries.')
    host = raw['host'].strip()
    address = ipaddress.ip_address(host)
    if address.version != 4:
        raise ValueError('Choose an IPv4 address, 127.0.0.1 or 0.0.0.0.')
    port = int(raw['port'])
    if not 1024 <= port <= 65535:
        raise ValueError('Choose a port between 1024 and 65535.')
    tls_cert, tls_key = (raw.get('tls_cert') or '').strip(), (raw.get('tls_key') or '').strip()
    if bool(tls_cert) != bool(tls_key):
        raise ValueError('Provide both the PEM certificate and its private key.')
    if tls_cert:
        tls_cert = str(Path(tls_cert).resolve(strict=True))
        tls_key = str(Path(tls_key).resolve(strict=True))
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(tls_cert, tls_key, password=lambda: '')
    if not address.is_loopback and not tls_cert:
        raise ValueError('Network access requires HTTPS. Choose a certificate and private key.')
    public_url = raw['public_url'].strip().rstrip('/')
    url = urlsplit(public_url)
    if (url.scheme != ('https' if tls_cert else 'http') or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment or url.hostname == '0.0.0.0'
            or (url.port or (443 if tls_cert else 80)) != port):
        raise ValueError('Enter the full browser address using the selected scheme and port, without a path.')
    if address.is_loopback and url.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('A local-only server needs a localhost or 127.0.0.1 browser address.')
    if (directory.exists() and not (directory / 'server.sqlite3').is_file()
            and any(path.name not in ('setup.log', 'instance.lock') for path in directory.iterdir())):
        raise ValueError('Use an empty data folder or one already initialized by TRELLIS Server.')
    return dict(data_dir=str(directory), host=host, port=port, public_url=public_url,
                tls_cert=tls_cert or None, tls_key=tls_key or None, autostart=raw.get('autostart') is True)


def setup_administrator(directory):
    if active_administrator(directory):
        return
    directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    with NamedTemporaryFile(dir=directory, prefix='.setup-', delete=False, mode='w', encoding='utf-8') as stream:
        stream.write(token)
        token_path = Path(stream.name)
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        command = [sys.executable, '-B', str(ROOT / 'server_app.py'), '--setup-only', '--setup-token-file',
                   str(token_path), '--data-dir', str(directory), '--port', str(port)]
        with (directory / 'setup.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                until = time.monotonic() + 30
                while time.monotonic() < until and process.poll() is None:
                    try:
                        with urlopen(base + '/server/api/health', timeout=1) as response:
                            health = json.load(response)
                        if health.get('service') == 'trellis-server' and not health.get('setup_complete'):
                            break
                    except (URLError, OSError, ValueError):
                        time.sleep(.2)
                else:
                    raise RuntimeError(f'The local setup did not start. See {directory / "setup.log"}.')
                if not webbrowser.open(base + '/server/setup#token=' + token):
                    raise RuntimeError('Windows did not open the local setup page. Configure a default browser and retry.')
                print('Create the first administrator in the browser to finish installation.', flush=True)
                try:
                    result = process.wait(timeout=900)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError('Administrator setup expired. Run the server configuration again.') from error
                if result or not active_administrator(directory):
                    raise RuntimeError(f'The administrator was not created. See {directory / "setup.log"}.')
            finally:
                if process.poll() is None:
                    token_path.unlink(missing_ok=True)
                    # A Windows virtual environment can spawn the real Python interpreter.
                    # Only processes started by this setup invocation belong to this cleanup.
                    import psutil
                    try:
                        children = psutil.Process(process.pid).children(recursive=True)
                    except psutil.NoSuchProcess:
                        children = []
                    for child in reversed(children):
                        try:
                            child.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    process.terminate()
                    process.wait(timeout=10)
    finally:
        token_path.unlink(missing_ok=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Check the saved configuration and administrator.')
    args = parser.parse_args()
    config_path = args.config.resolve()
    try:
        if args.check:
            raw = json.loads(config_path.read_text(encoding='utf-8'))
            preferences = settings(raw, config_path)
            if not active_administrator(Path(preferences['data_dir'])):
                raise ValueError('Configure the first administrator before starting the server.')
            return 0
        # Read preferences as UTF-8. The password is entered only in the browser.
        preferences = settings(json.loads(sys.stdin.buffer.read().decode('utf-8-sig')), config_path)
        setup_administrator(Path(preferences['data_dir']))
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=config_path.parent, prefix='.server-', delete=False, mode='w', encoding='utf-8') as stream:
            json.dump(preferences, stream, ensure_ascii=False, indent=2)
            pending = Path(stream.name)
        try:
            os.replace(pending, config_path)
        finally:
            pending.unlink(missing_ok=True)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
