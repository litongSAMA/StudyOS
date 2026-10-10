"""本地账号体系：注册/登录/登出、登录保护中间件、改密码、账号管理、同步通道放行。"""
from app.db import SessionLocal
from app.models import AppSetting, User


def _register(client, username="alice", password="secret123"):
    return client.post("/api/auth/register", json={
        "username": username, "password": password,
    })


def _login(client, username="alice", password="secret123"):
    r = client.post("/api/auth/login", json={
        "username": username, "password": password,
    })
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _enable(client, token):
    return client.post("/api/auth/enable", json={"enabled": True},
                       headers={"Authorization": f"Bearer {token}"})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---- 状态与注册 ----

def test_status_default_disabled(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["user"] is None
    assert body["users"] == []


def test_register_auto_login_and_status(client):
    r = _register(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["user"]["username"] == "alice"
    assert body["token"]  # 未开启保护时注册即登录
    st = client.get("/api/auth/status", headers=_bearer(body["token"])).json()
    assert st["user"]["username"] == "alice"
    assert st["users"] == [{"id": body["user"]["id"], "username": "alice"}]


def test_register_validation(client):
    # 用户名非法（过短 / 非法开头 / 过长）
    assert _register(client, username="a").status_code == 422
    assert _register(client, username="-aaa").status_code == 422
    assert _register(client, username="a" * 33).status_code == 422
    # 密码过短/过长
    assert _register(client, password="12345").status_code == 422
    assert _register(client, password="x" * 129).status_code == 422


def test_register_duplicate_case_insensitive(client):
    assert _register(client, username="Alice").status_code == 200
    assert _register(client, username="alice").status_code == 409
    assert _register(client, username="ALICE").status_code == 409


def test_register_with_chinese_username(client):
    r = _register(client, username="小明同学", password="pass1234")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "小明同学"


# ---- 登录 / 登出 ----

def test_login_wrong_password_and_unknown_user(client):
    _register(client)
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "wrong-pass",
    }).status_code == 401
    assert client.post("/api/auth/login", json={
        "username": "nobody", "password": "secret123",
    }).status_code == 401


def test_login_success_and_logout(client):
    _register(client)
    token = _login(client)
    r = client.post("/api/auth/logout", headers=_bearer(token))
    assert r.status_code == 200
    # 登出后令牌立即失效
    assert client.get("/api/auth/status", headers=_bearer(token)).json()["user"] is None


def test_login_records_last_login(client):
    _register(client)
    token = _login(client)
    users = client.get("/api/auth/users", headers=_bearer(token)).json()
    assert len(users) == 1
    assert users[0]["last_login_at"] is not None
    assert users[0]["created_at"] is not None


# ---- 登录保护中间件 ----

def test_enable_requires_login(client):
    _register(client)
    r = client.post("/api/auth/enable", json={"enabled": True})
    assert r.status_code == 401


def test_enable_without_users_rejected(client):
    # 无法先登录（没有账号）→ 只能 401/422，绝不能开启成功
    assert client.post("/api/auth/enable", json={"enabled": True}).status_code == 401


def test_enabled_locks_api_and_bearer_unlocks(client):
    _register(client)
    token = _login(client)
    assert _enable(client, token).status_code == 200
    # 无令牌：数据接口一律 401
    assert client.get("/api/courses").status_code == 401
    assert client.get("/api/stats").status_code == 401
    assert client.post("/api/courses", json={"name": "x"}).status_code == 401
    # 带令牌：正常访问
    r = client.get("/api/courses", headers=_bearer(token))
    assert r.status_code == 200
    # 公开接口不受影响
    assert client.get("/api/health").status_code == 200
    st = client.get("/api/auth/status").json()
    assert st["enabled"] is True and st["user"] is None and st["users"] is None


def test_enabled_share_view_stays_public(client):
    _register(client)
    token = _login(client)
    _enable(client, token)
    # 先建一条清单并分享（登录态下操作）
    c = client.post("/api/checklists", json={"title": "共享清单"}, headers=_bearer(token))
    cid = c.json()["id"]
    sh = client.post(f"/api/checklists/{cid}/share", json={"expires_h": None},
                     headers=_bearer(token)).json()
    # 未登录也可查看公开分享视图
    r = client.get(f"/api/share/{sh['token']}")
    assert r.status_code == 200
    assert r.json()["title"] == "共享清单"


def test_enabled_sync_exchange_via_x_sync_token(client):
    """登录保护开启后：远端设备凭 X-Sync-Token 仍可交换快照（不要求登录会话）。"""
    _register(client)
    token = _login(client)
    _enable(client, token)
    # 经本机接口披露同步令牌
    sync_token = client.post("/api/sync/token/reveal", headers=_bearer(token)).json()["token"]
    empty = {"version": 3, "device_id": "peer-123", "generated_at": None}
    r = client.post("/api/sync/exchange", json={"snapshot": empty},
                    headers={"X-Sync-Token": sync_token})
    assert r.status_code == 200
    # 无令牌 / 错令牌则 401（登录保护兜底）
    assert client.post("/api/sync/exchange", json={"snapshot": empty}).status_code == 401
    assert client.post("/api/sync/exchange", json={"snapshot": empty},
                       headers={"X-Sync-Token": "bad"}).status_code == 401


def test_enabled_sync_status_needs_bearer(client):
    _register(client)
    token = _login(client)
    _enable(client, token)
    assert client.get("/api/sync/status").status_code == 401
    assert client.get("/api/sync/status", headers=_bearer(token)).status_code == 200


def test_disable_requires_login(client):
    _register(client)
    token = _login(client)
    _enable(client, token)
    assert client.post("/api/auth/enable", json={"enabled": False}).status_code == 401
    r = client.post("/api/auth/enable", json={"enabled": False}, headers=_bearer(token))
    assert r.status_code == 200
    assert client.get("/api/courses").status_code == 200  # 恢复免登录


# ---- 注册门（保护开启后） ----

def test_register_blocked_when_enabled_without_session(client):
    _register(client, username="admin", password="admin123")
    token = _login(client, "admin", "admin123")
    _enable(client, token)
    # 未登录注册被拒（无 Authorization 头）；登录后（管理员）可添加成员
    assert _register(client, username="bob", password="bob12345").status_code == 401
    r = client.post("/api/auth/register", json={"username": "bob", "password": "bob12345"},
                    headers=_bearer(token))
    assert r.status_code == 200
    assert r.json()["token"] is None  # 管理员添加成员不自动登录
    # 新成员可正常登录
    assert _login(client, "bob", "bob12345")


# ---- 修改密码 ----

def test_change_password(client):
    _register(client)
    token = _login(client)
    r = client.post("/api/auth/password", json={
        "current_password": "secret123", "new_password": "newpass789",
    }, headers=_bearer(token))
    assert r.status_code == 200
    # 旧密码失效、新密码可用
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "secret123",
    }).status_code == 401
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "newpass789",
    }).status_code == 200


def test_change_password_requires_current(client):
    _register(client)
    token = _login(client)
    assert client.post("/api/auth/password", json={
        "current_password": "wrong-old", "new_password": "newpass789",
    }, headers=_bearer(token)).status_code == 401
    assert client.post("/api/auth/password", json={
        "current_password": "secret123", "new_password": "short",
    }, headers=_bearer(token)).status_code == 422


def test_change_password_kills_other_sessions(client):
    _register(client)
    token_a = _login(client)
    token_b = _login(client)
    # 用会话 A 改密码 → 会话 B 立即失效、会话 A 保留
    client.post("/api/auth/password", json={
        "current_password": "secret123", "new_password": "newpass789",
    }, headers=_bearer(token_a))
    assert client.get("/api/auth/status", headers=_bearer(token_b)).json()["user"] is None
    assert client.get("/api/auth/status", headers=_bearer(token_a)).json()["user"] is not None


# ---- 账号管理 ----

def test_users_list_requires_login(client):
    _register(client)
    assert client.get("/api/auth/users").status_code == 401
    token = _login(client)
    assert client.get("/api/auth/users", headers=_bearer(token)).status_code == 200


def test_delete_user_rules(client):
    _register(client, username="admin", password="admin123")
    token = _login(client, "admin", "admin123")
    _enable(client, token)
    client.post("/api/auth/register", json={"username": "bob", "password": "bob12345"},
                headers=_bearer(token))
    bob_id = client.get("/api/auth/users", headers=_bearer(token)).json()[1]["id"]
    # 不能删自己
    me = client.get("/api/auth/status", headers=_bearer(token)).json()["user"]
    assert client.delete(f"/api/auth/users/{me['id']}",
                         headers=_bearer(token)).status_code == 422
    # 删掉 bob 后（还剩 admin 一个）不能删最后一个账号
    assert client.delete(f"/api/auth/users/{bob_id}", headers=_bearer(token)).status_code == 200
    with SessionLocal() as db:
        assert db.query(User).count() == 1
    assert client.delete("/api/auth/users/999", headers=_bearer(token)).status_code == 404


def test_delete_user_closes_their_sessions(client):
    _register(client, username="admin", password="admin123")
    token = _login(client, "admin", "admin123")
    _enable(client, token)
    r = client.post("/api/auth/register", json={"username": "bob", "password": "bob12345"},
                    headers=_bearer(token))
    bob_id = r.json()["user"]["id"]
    bob_token = _login(client, "bob", "bob12345")
    assert client.delete(f"/api/auth/users/{bob_id}", headers=_bearer(token)).status_code == 200
    # bob 的会话已失效：状态与数据接口都不认
    assert client.get("/api/auth/status", headers=_bearer(bob_token)).json()["user"] is None
    assert client.get("/api/courses", headers=_bearer(bob_token)).status_code == 401


# ---- 存储与开关落库 ----

def test_auth_enabled_persisted_in_app_settings(client):
    _register(client)
    token = _login(client)
    _enable(client, token)
    with SessionLocal() as db:
        assert db.get(AppSetting, "auth_enabled").value == "1"


def test_password_stored_hashed(client):
    _register(client, password="secret123")
    with SessionLocal() as db:
        user = db.query(User).first()
        assert "secret123" not in user.password_hash
        assert user.password_hash.startswith("pbkdf2_sha256$")


# ---- 登录防爆破（锁定退避） ----

def test_login_lockout_progressive_and_ip_scoped(client, lan_client, monkeypatch):
    """连续失败 → 锁定（429 + Retry-After），锁定期内尝试逐次翻倍；
    锁定按「用户名 × IP」计，不影响其它来源设备的正常登录。"""
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "LOGIN_MAX_FAILURES", 3)
    monkeypatch.setattr(auth_mod, "LOGIN_LOCKOUT_BASE_S", 2.0)
    monkeypatch.setattr(auth_mod, "LOGIN_LOCKOUT_MAX_S", 32.0)
    _register(client)
    for _ in range(3):
        r = client.post("/api/auth/login", json={
            "username": "alice", "password": "wrong-pass",
        })
        assert r.status_code == 401
        assert r.headers.get("www-authenticate") == "Bearer"
    # 第 4 次即使密码正确也被锁定
    r = client.post("/api/auth/login", json={
        "username": "alice", "password": "secret123",
    })
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 2
    # 锁定期内继续尝试 → 锁定时间翻倍
    r = client.post("/api/auth/login", json={
        "username": "alice", "password": "secret123",
    })
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 4
    # 同用户名从另一来源 IP（lan_client）登录不受影响
    r = lan_client.post("/api/auth/login", json={
        "username": "alice", "password": "secret123",
    })
    assert r.status_code == 200


def test_login_lockout_reset_after_window(client, monkeypatch):
    """失败计数窗口滑过后锁定自然解除（无需重启进程）。"""
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "LOGIN_MAX_FAILURES", 2)
    monkeypatch.setattr(auth_mod, "LOGIN_LOCKOUT_BASE_S", 5.0)
    _register(client)
    for _ in range(2):
        client.post("/api/auth/login", json={
            "username": "alice", "password": "wrong-pass",
        })
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "secret123",
    }).status_code == 429
    # 让窗口时间流逝（monotonic 时钟前移）
    real_now = auth_mod.time_monotonic()
    monkeypatch.setattr(auth_mod, "time_monotonic",
                        lambda: real_now + auth_mod.LOGIN_FAILURE_WINDOW_S + 1)
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "secret123",
    }).status_code == 200


# ---- 注册限速（开放注册窗口，按来源 IP） ----

def test_register_rate_limit_per_ip(client, monkeypatch):
    """登录保护未开启时：同一来源 IP 成功注册超过阈值 → 429 + Retry-After；
    校验失败的注册不消耗配额。"""
    import app.routers.auth as auth_router

    monkeypatch.setattr(auth_router, "REGISTER_PER_HOUR_PER_IP", 2)
    assert _register(client, username="u1").status_code == 200
    # 校验失败（密码过短）不计数
    assert _register(client, username="u3", password="123").status_code == 422
    assert _register(client, username="u2").status_code == 200  # 第 2 个成功仍放行
    r = _register(client, username="u3")
    assert r.status_code == 429
    assert "retry-after" in r.headers


def test_register_rate_limit_not_applied_when_enabled(client, monkeypatch):
    """登录保护开启后：管理员添加成员不走 IP 限速（不限批量添加）。"""
    import app.routers.auth as auth_router

    monkeypatch.setattr(auth_router, "REGISTER_PER_HOUR_PER_IP", 1)
    _register(client, username="admin", password="admin123")
    token = _login(client, "admin", "admin123")
    _enable(client, token)
    for name in ("m1", "m2", "m3"):
        r = client.post("/api/auth/register", json={
            "username": name, "password": "member123",
        }, headers=_bearer(token))
        assert r.status_code == 200, r.text


# ---- 密码强度：不得与用户名相同 ----

def test_password_same_as_username_rejected(client):
    assert _register(client, username="alice", password="alice").status_code == 422
    assert _register(client, username="alice", password="Alice").status_code == 422
    _register(client, username="alice", password="secret123")
    token = _login(client)
    r = client.post("/api/auth/password", json={
        "current_password": "secret123", "new_password": "alice",
    }, headers=_bearer(token))
    assert r.status_code == 422


# ---- 登录会话管理 ----

def test_sessions_list_current_flag_and_revoke(client):
    r = _register(client)
    # 注册自动登录产生的会话先登出，保证会话列表口径清晰
    client.post("/api/auth/logout", headers=_bearer(r.json()["token"]))
    token_a = _login(client)
    token_b = _login(client)
    sessions = client.get("/api/auth/sessions", headers=_bearer(token_b)).json()
    assert len(sessions) == 2
    assert sum(1 for s in sessions if s["current"]) == 1
    assert all(s["expires_at"] for s in sessions)
    # 撤销 token_a 的会话 → token_a 立即失效，token_b 不受影响
    id_a = next(s["id"] for s in sessions if not s["current"])
    r = client.delete(f"/api/auth/sessions/{id_a}", headers=_bearer(token_b))
    assert r.status_code == 200
    assert client.get("/api/auth/status", headers=_bearer(token_a)).json()["user"] is None
    assert client.get("/api/auth/status", headers=_bearer(token_b)).json()["user"] is not None
    # 非法 id / 别人的会话 id → 404
    assert client.delete("/api/auth/sessions/abc", headers=_bearer(token_b)).status_code == 404
    id_b = next(s["id"] for s in sessions if s["current"])
    # 撤销当前会话 = 立即登出
    assert client.delete(f"/api/auth/sessions/{id_b}",
                         headers=_bearer(token_b)).status_code == 200
    assert client.get("/api/auth/status", headers=_bearer(token_b)).json()["user"] is None


def test_revoke_other_sessions_keeps_current(client):
    _register(client)
    token_a = _login(client)
    token_b = _login(client)
    r = client.delete("/api/auth/sessions/others", headers=_bearer(token_b))
    assert r.status_code == 200
    assert client.get("/api/auth/status", headers=_bearer(token_a)).json()["user"] is None
    assert client.get("/api/auth/status", headers=_bearer(token_b)).json()["user"] is not None
    assert len(client.get("/api/auth/sessions", headers=_bearer(token_b)).json()) == 1


def test_sessions_require_login(client):
    assert client.get("/api/auth/sessions").status_code == 401
    assert client.delete("/api/auth/sessions/others").status_code == 401


# ---- 会话滑动续期与过期 ----

def test_session_sliding_renewal(client):
    import hashlib
    from datetime import timedelta

    from app.auth import LAST_SEEN_THROTTLE, SESSION_TTL
    from app.models import AuthSession
    from app.timeutil import utcnow

    _register(client)
    token = _login(client)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with SessionLocal() as db:
        row = db.get(AuthSession, token_hash)
        # 模拟「很久以前活跃、即将过期」的会话
        row.last_seen_at = utcnow() - LAST_SEEN_THROTTLE - timedelta(minutes=1)
        row.expires_at = utcnow() + timedelta(minutes=1)
        db.commit()
    # 一次正常请求 → 滑动续期：有效期顺延到 ~30 天后
    assert client.get("/api/auth/status", headers=_bearer(token)).json()["user"] is not None
    with SessionLocal() as db:
        row = db.get(AuthSession, token_hash)
        assert (row.expires_at - utcnow()) > (SESSION_TTL - timedelta(hours=1))
        assert row.last_seen_at is not None


def test_expired_session_rejected_and_purged(client):
    import hashlib
    from datetime import timedelta

    from app.models import AuthSession
    from app.timeutil import utcnow

    _register(client)
    token = _login(client)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with SessionLocal() as db:
        row = db.get(AuthSession, token_hash)
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/auth/status", headers=_bearer(token)).json()["user"] is None
    with SessionLocal() as db:
        assert db.get(AuthSession, token_hash) is None


# ---- 安全事件审计 ----

def test_auth_events_recorded_and_listed(client):
    _register(client, username="alice", password="secret123")
    token = _login(client)
    # 一次失败登录（未知用户）
    client.post("/api/auth/login", json={
        "username": "nobody", "password": "guess-pass",
    })
    _enable(client, token)
    r = client.get("/api/auth/events", headers=_bearer(token))
    assert r.status_code == 200
    events = r.json()
    kinds = [e["kind"] for e in events]
    # 注册 → 登录成功 → 登录失败 → 开启保护，全部留痕
    assert "register" in kinds
    assert "login_ok" in kinds
    assert "login_fail" in kinds
    assert "enable" in kinds
    fail = next(e for e in events if e["kind"] == "login_fail")
    assert fail["username"] == "nobody"  # 尝试的用户名留痕（绝不含密码）
    assert "guess-pass" not in (fail["detail"] or "")
    assert fail["source_ip"] == "127.0.0.1"
    # limit 参数生效（倒序、最新在前）
    top2 = client.get("/api/auth/events?limit=2", headers=_bearer(token)).json()
    assert len(top2) == 2
    assert top2[0]["id"] >= top2[1]["id"]
    # 未登录不可查看
    client.post("/api/auth/logout", headers=_bearer(token))
    assert client.get("/api/auth/events").status_code == 401


def test_auth_events_trimmed_to_keep(client):
    """事件表只保留最近 EVENT_KEEP 条（写入时惰性裁剪）。"""
    from app.auth import EVENT_KEEP, record_event

    _register(client)
    token = _login(client)
    with SessionLocal() as db:
        for i in range(EVENT_KEEP + 20):
            record_event(db, "login_fail", username="spam", source_ip="1.2.3.4",
                         commit=False)
        db.commit()
    events = client.get(f"/api/auth/events?limit=200", headers=_bearer(token)).json()
    assert len(events) == 200  # 分页上限
    with SessionLocal() as db:
        from app.models import AuthEvent
        total = db.query(AuthEvent).count()
        assert total <= EVENT_KEEP


# ---- 数据库兜底与 CLI 恢复工具 ----

def test_unique_username_index_exists(client):
    from sqlalchemy import text

    _register(client)
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND name='ux_users_username_lower'"
        )).scalars().all()
    assert rows == ["ux_users_username_lower"]


def test_authadmin_reset_password_and_disable(client):
    from app.authadmin import disable_protection, list_users, reset_password

    _register(client, username="alice", password="secret123")
    token = _login(client)
    _enable(client, token)
    with SessionLocal() as db:
        # 找回密码：重置后旧密码失效、新密码可登录、其它会话全部注销
        reset_password(db, "alice", "recovered-456")
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "secret123",
    }).status_code == 401
    assert client.get("/api/auth/status", headers=_bearer(token)).json()["user"] is None
    new_token = _login(client, "alice", "recovered-456")
    # 恢复工具关闭登录保护 → 免登录恢复
    with SessionLocal() as db:
        assert disable_protection(db) is True
    assert client.get("/api/courses").status_code == 200
    # 非法输入报错
    with SessionLocal() as db:
        try:
            reset_password(db, "nobody", "whatever1")
            raise AssertionError("应当报错")
        except ValueError as e:
            assert "不存在" in str(e)
        try:
            reset_password(db, "alice", "short")
            raise AssertionError("应当报错")
        except ValueError:
            pass
        try:
            reset_password(db, "alice", "alice")
            raise AssertionError("应当报错")
        except ValueError:
            pass
        assert [u.username for u in list_users(db)] == ["alice"]
    assert client.get("/api/auth/status", headers=_bearer(new_token)).json()["user"] is not None


def test_authadmin_delete_user(client):
    from app.authadmin import delete_user, list_users

    _register(client, username="alice", password="secret123")
    with SessionLocal() as db:
        delete_user(db, "alice")
        assert list_users(db) == []
    # 账号没了，登录自然失败
    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "secret123",
    }).status_code == 401


# ---- 登录保护全 API 表面收口 ----

def test_enabled_protects_entire_api_surface(client):
    """登录保护开启后，除公开豁免外的**每一个** /api 路由都要收口：

    - 无令牌：一律 401 + `WWW-Authenticate: Bearer`（中间件先于路由/参数校验执行，
      无论参数是否合法都先拒之门外）；
    - 持有效凭据：一律不再因登录被拒（其余 200/404/422 均属正常，绝不 401）。
      普通路由的凭据是 Bearer 会话令牌；`/api/sync/*` 点对点通道的凭据是
      `X-Sync-Token` 设备令牌（其端点自身的收口与语义不依赖登录会话）；
    - OPTIONS 预检不受登录门禁拦截（CORS 预检由 CORSMiddleware 处理）。

    路由清单从 `app.routes` 动态枚举（含新版 FastAPI 的 `_IncludedRouter` 嵌套结构），
    以后新增任何接口都会自动纳入本用例的检查范围——漏加豁免即测试红灯。
    """
    import re

    from fastapi.routing import APIRoute

    from app.main import app as fastapi_app

    _register(client)
    token = _login(client)
    assert _enable(client, token).status_code == 200

    def walk(router, out):
        for r in router.routes:
            if type(r).__name__ == "_IncludedRouter":  # FastAPI 嵌套 include_router
                walk(r.original_router, out)
            elif isinstance(r, APIRoute):
                out.append(r)

    routes: list[APIRoute] = []
    walk(fastapi_app.router, routes)
    assert len(routes) >= 100  # 路由枚举失效时测试空转的兜底

    # 与 app.main.auth_guard 的豁免口径一一对应
    exempt_exact = {"/api/health"}
    exempt_prefixes = ("/api/auth/", "/api/share/")

    # /api/sync/* 点对点通道的凭据：经本机接口显式披露的同步令牌（= 全部数据读写权限）
    sync_token: str | None = None

    checked = 0
    for r in routes:
        path = r.path
        if not path.startswith("/api/"):
            continue  # 静态资源 / SPA 回退不走登录门禁
        if path in exempt_exact or path.startswith(exempt_prefixes):
            continue
        # 路径参数全部替换为占位值：中间件只关心令牌，参数合法性由后续校验负责
        url = re.sub(r"\{[^}]*\}", "1", path)
        is_sync = path.startswith("/api/sync/")
        for method in sorted(m for m in (r.methods or ())
                             if m not in ("OPTIONS", "HEAD")):
            resp = client.request(method, url)
            assert resp.status_code == 401, (
                f"{method} {path} 无令牌应为 401，实得 {resp.status_code}：{resp.text[:200]}"
            )
            assert resp.headers.get("www-authenticate") == "Bearer", f"{method} {path}"
            if is_sync:
                if path == "/api/sync/token/regenerate":
                    # 重新生成令牌会立即作废旧令牌：本路由用 Bearer 会话校验
                    # （仅本机 + 已登录），随后作废缓存的同步令牌，下一条
                    # sync 通道路由会重新披露新令牌。
                    resp_ok = client.request(method, url, headers=_bearer(token))
                    sync_token = None
                else:
                    if sync_token is None:
                        revealed = client.post(
                            "/api/sync/token/reveal", headers=_bearer(token)
                        )
                        assert revealed.status_code == 200, revealed.text
                        sync_token = revealed.json()["token"]
                    resp_ok = client.request(method, url, headers={"X-Sync-Token": sync_token})
            else:
                resp_ok = client.request(method, url, headers=_bearer(token))
            assert resp_ok.status_code != 401, (
                f"{method} {path} 持有效凭据不应 401，实得 {resp_ok.status_code}：{resp_ok.text[:200]}"
            )
            checked += 1
        # OPTIONS 预检放行（登录门禁对预检一律放行，CORS 中间件负责应答）
        opt = client.options(url)
        assert opt.status_code != 401, f"OPTIONS {path} 不应被登录门禁拦截"
    assert checked >= 100  # 覆盖足够多的「方法 × 路径」组合，防止枚举意外缩水
