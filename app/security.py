import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request

from . import db

SECRET_KEYS = {'openai_api_key', 'supadata_api_key', 'wp_password'}


def cipher():
    path = db.data_dir() / 'encryption.key'
    if not path.exists():
        try:
            with path.open('xb') as f:
                f.write(Fernet.generate_key())
            path.chmod(0o600)
        except FileExistsError:
            pass
    return Fernet(path.read_bytes())


def save_secret(key, value):
    db.set_setting(key, cipher().encrypt(value.encode()).decode() if value else '')


def get_secret(key):
    value = db.get_setting(key)
    if value:
        return cipher().decrypt(value.encode()).decode()
    return os.getenv(key.upper(), '')


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return f'{salt}:{digest}'


def check_password(password):
    saved = db.get_setting('password_hash')
    if not saved:
        return False
    return hmac.compare_digest(saved, hash_password(password, saved.split(':')[0]))


def init_auth():
    cipher()
    if not db.get_setting('password_hash'):
        password = os.getenv('ADMIN_PASSWORD', '')
        if len(password) < 12:
            raise RuntimeError('Configure ADMIN_PASSWORD com pelo menos 12 caracteres antes de iniciar.')
        db.set_setting('password_hash', hash_password(password))


def session_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def require_auth(request: Request):
    token = session_hash(request.cookies.get('seo_session', ''))
    with db.connect() as c:
        session = c.execute('SELECT expires FROM sessions WHERE token=?', (token,)).fetchone()
    if not session or session['expires'] < time.time():
        raise HTTPException(401, 'Entre na sua conta para continuar.')
    return True


def public_https_url(value):
    """Validate destinations before credentialed requests; redirects are never followed."""
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use uma URL pública HTTPS, sem credenciais no endereço.')
    if parsed.port not in (None, 443) or parsed.query or parsed.fragment:
        raise ValueError('Use a URL base HTTPS do site, sem porta, consulta ou fragmento.')
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError('Não foi possível localizar o domínio.') from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('O endereço precisa apontar para um site público.')
    return value.rstrip('/')
