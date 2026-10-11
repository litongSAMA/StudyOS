"""Local account protection. Accounts and sessions never travel in sync snapshots."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import sqlite3
from fastapi import HTTPException

AUTH_SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL, last_login_at TEXT);
CREATE TABLE IF NOT EXISTS auth_sessions(id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auth_events(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, username TEXT NOT NULL, source_ip TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auth_attempts(key TEXT PRIMARY KEY, failures INTEGER NOT NULL, locked_until TEXT, window_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auth_flags(key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''
ROUNDS = 600_000


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


@contextmanager
def connection():
    from .db import engine
    con = sqlite3.connect(engine.url.database, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        con.execute('BEGIN IMMEDIATE')
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def init_auth():
    with connection() as con:
        con.executescript(AUTH_SCHEMA)


def hash_password(password):
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ROUNDS).hex()
    return f'{ROUNDS}${salt}${hashed}'


def verify(password, encoded):
    try:
        rounds, salt, expected = encoded.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


def validate_password(password, username):
    if not isinstance(password, str) or not 6 <= len(password) <= 128 or password.casefold() == username.casefold():
        raise HTTPException(422, '密码须为 6 到 128 位，且不能与用户名相同')


def user_out(row):
    return {key: row[key] for key in ('id','username','created_at','last_login_at')}


def enabled(con):
    row = con.execute("SELECT value FROM auth_flags WHERE key='enabled'").fetchone()
    return bool(row and row[0] == '1')


def event(con, kind, username='', source_ip='', detail=''):
    con.execute('INSERT INTO auth_events(kind,username,source_ip,detail,created_at) VALUES (?,?,?,?,?)',(kind,username,source_ip,detail,now()))
    con.execute('DELETE FROM auth_events WHERE id NOT IN (SELECT id FROM auth_events ORDER BY id DESC LIMIT 500)')


def token_id(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session_user(con, authorization):
    if not authorization or not authorization.startswith('Bearer '):
        return None
    sid = token_id(authorization[7:])
    row = con.execute('SELECT users.*,auth_sessions.expires_at FROM users JOIN auth_sessions ON users.id=auth_sessions.user_id WHERE auth_sessions.id=?',(sid,)).fetchone()
    if not row or row['expires_at'] <= now():
        return None
    until = (datetime.now(timezone.utc)+timedelta(days=30)).isoformat(timespec='microseconds')
    con.execute('UPDATE auth_sessions SET last_seen_at=?,expires_at=? WHERE id=?',(now(),until,sid))
    return user_out(row)


def require_user(request, con):
    user = session_user(con, request.headers.get('authorization'))
    if not user:
        raise HTTPException(401, '请先登录', headers={'WWW-Authenticate':'Bearer'})
    return user


def authorize_request(request):
    with connection() as con:
        user = session_user(con, request.headers.get('authorization'))
        protected = enabled(con)
    if protected and not user:
        if request.url.path in {'/api/sync/exchange','/api/sync/snapshot','/api/sync/now','/api/sync/backup','/api/sync/restore'}:
            from .db import SessionLocal
            from .sync import token_ok
            with SessionLocal() as db:
                if token_ok(db, request.headers.get('x-sync-token')):
                    return 'device-sync'
        raise HTTPException(401, '登录会话无效或登录保护已开启', headers={'WWW-Authenticate':'Bearer'})
    return 'user:'+user['username'].casefold() if user else 'local'


def new_session(con, user_id):
    token = secrets.token_urlsafe(32)
    stamp = now()
    until = (datetime.now(timezone.utc)+timedelta(days=30)).isoformat(timespec='microseconds')
    con.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,?)',(token_id(token),user_id,stamp,stamp,until))
    return token
