from datetime import datetime, timedelta, timezone
import re
import sqlite3
from fastapi import APIRouter, HTTPException, Request
from .. import auth

router=APIRouter(prefix='/api/auth',tags=['auth'])


@router.get('/status')
def status(request: Request):
    with auth.connection() as con:
        protected=auth.enabled(con)
        user=auth.session_user(con,request.headers.get('authorization'))
        users=[auth.user_out(row) for row in con.execute('SELECT * FROM users ORDER BY id')] if user or not protected else None
        return {'enabled':protected,'user':user,'users':users}


@router.post('/register')
def register(request: Request, body:dict):
    name=body.get('username','')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff .\-]{1,31}',name.strip()):
        raise HTTPException(422,'用户名格式无效')
    name=name.strip(); password=body.get('password'); auth.validate_password(password,name)
    if 'password_confirm' in body and body['password_confirm'] != password:
        raise HTTPException(422,'两次输入的密码不一致')
    if any(body.get(kind) for kind in ['phone','wechat','qq']):
        raise HTTPException(422,'恢复版本未配置联系渠道验证，请先仅使用用户名和密码注册')
    hashed=auth.hash_password(password)
    with auth.connection() as con:
        protected=auth.enabled(con)
        if protected: auth.require_user(request,con)
        ip=request.client.host if request.client else ''
        key='register:'+ip
        previous=con.execute('SELECT * FROM auth_attempts WHERE key=?',(key,)).fetchone()
        count=previous['failures'] if previous and previous['window_at'] > (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat() else 0
        if not protected and count>=10:
            raise HTTPException(429,'注册过于频繁，请稍后重试',headers={'Retry-After':'3600'})
        if con.execute('SELECT 1 FROM users WHERE username_key=?',(name.casefold(),)).fetchone():
            raise HTTPException(409,'用户名已存在')
        uid=con.execute('INSERT INTO users(username,username_key,password_hash,created_at) VALUES (?,?,?,?)',(name,name.casefold(),hashed,auth.now())).lastrowid
        con.execute('INSERT OR REPLACE INTO auth_attempts VALUES (?,?,NULL,?)',(key,count+1,auth.now()))
        token=auth.new_session(con,uid) if not protected else None
        auth.event(con,'register',name,ip)
        return {'ok':True,'user':auth.user_out(con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':token}


@router.post('/login')
def login(request: Request, body:dict):
    name=body.get('username',''); password=body.get('password','')
    if not isinstance(name,str) or not isinstance(password,str) or len(name)>128 or len(password)>128:
        raise HTTPException(422,'登录信息格式无效')
    ip=request.client.host if request.client else ''
    failed=False
    with auth.connection() as con:
        key='login:'+name.strip().casefold()+':'+ip
        attempt=con.execute('SELECT * FROM auth_attempts WHERE key=?',(key,)).fetchone()
        if attempt and attempt['locked_until'] and attempt['locked_until']>auth.now():
            seconds=max(1,int((datetime.fromisoformat(attempt['locked_until'])-datetime.now(timezone.utc)).total_seconds()))
            raise HTTPException(429,'登录暂时锁定，请稍后重试',headers={'Retry-After':str(seconds)})
        row=con.execute('SELECT * FROM users WHERE username_key=?',(name.strip().casefold(),)).fetchone()
        # Missing usernames take the same PBKDF2 work, without revealing existence.
        encoded=row['password_hash'] if row else f"{auth.ROUNDS}$"+'00'*16+'$'+'00'*32
        if not auth.verify(password,encoded):
            failed=True; failures=(attempt['failures'] if attempt else 0)+1
            delay=min(3600,30*2**min(7,max(0,failures-5))) if failures>=5 else 0
            locked=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat() if delay else None
            con.execute('INSERT OR REPLACE INTO auth_attempts VALUES (?,?,?,?)',(key,failures,locked,auth.now()))
            auth.event(con,'login_failed',name.strip(),ip)
        else:
            con.execute('DELETE FROM auth_attempts WHERE key=?',(key,))
            con.execute('UPDATE users SET last_login_at=? WHERE id=?',(auth.now(),row['id']))
            token=auth.new_session(con,row['id']);auth.event(con,'login',row['username'],ip)
            result={'token':token,'user':auth.user_out(con.execute('SELECT * FROM users WHERE id=?',(row['id'],)).fetchone())}
    if failed: raise HTTPException(401,'用户名或密码错误',headers={'WWW-Authenticate':'Bearer'})
    return result


@router.post('/logout')
def logout(request:Request):
    with auth.connection() as con:
        header=request.headers.get('authorization','')
        if header.startswith('Bearer '): con.execute('DELETE FROM auth_sessions WHERE id=?',(auth.token_id(header[7:]),))
    return {'ok':True}


@router.post('/enable')
def enable(request:Request, body:dict):
    if not isinstance(body.get('enabled'),bool): raise HTTPException(422,'enabled 必须为布尔值')
    with auth.connection() as con:
        user=auth.require_user(request,con)
        con.execute("INSERT OR REPLACE INTO auth_flags VALUES ('enabled',?)",('1' if body['enabled'] else '0',))
        auth.event(con,'protection',user['username'],detail=str(body['enabled']))
    return {'ok':True,'enabled':body['enabled']}


@router.post('/password')
def password(request:Request,body:dict):
    with auth.connection() as con:
        user=auth.require_user(request,con);row=con.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        if not auth.verify(body.get('current_password',''),row['password_hash']): raise HTTPException(403,'当前密码错误')
        auth.validate_password(body.get('new_password'),user['username'])
        con.execute('UPDATE users SET password_hash=? WHERE id=?',(auth.hash_password(body['new_password']),user['id']))
        sid=auth.token_id(request.headers['authorization'][7:])
        con.execute('DELETE FROM auth_sessions WHERE user_id=? AND id<>?',(user['id'],sid));auth.event(con,'password',user['username'])
    return {'ok':True}


@router.get('/users')
def users(request:Request):
    with auth.connection() as con:
        auth.require_user(request,con)
        return [auth.user_out(row) for row in con.execute('SELECT * FROM users ORDER BY id')]


@router.delete('/users/{user_id}')
def remove_user(user_id:int,request:Request):
    with auth.connection() as con:
        user=auth.require_user(request,con)
        if auth.enabled(con) and con.execute('SELECT count(*) FROM users').fetchone()[0]<=1: raise HTTPException(409,'请先关闭登录保护，再删除最后一个账号')
        con.execute('DELETE FROM users WHERE id=?',(user_id,));auth.event(con,'delete_user',user['username'])
    return {'ok':True}


@router.get('/sessions')
def sessions(request:Request):
    with auth.connection() as con:
        user=auth.require_user(request,con);sid=auth.token_id(request.headers['authorization'][7:])
        return [{**dict(row),'current':row['id']==sid} for row in con.execute('SELECT id,created_at,last_seen_at,expires_at FROM auth_sessions WHERE user_id=? AND expires_at>? ORDER BY last_seen_at DESC',(user['id'],auth.now()))]


@router.delete('/sessions/others')
def others(request:Request):
    with auth.connection() as con:
        user=auth.require_user(request,con);sid=auth.token_id(request.headers['authorization'][7:])
        con.execute('DELETE FROM auth_sessions WHERE user_id=? AND id<>?',(user['id'],sid))
    return {'ok':True}


@router.delete('/sessions/{session_id}')
def revoke(session_id:str,request:Request):
    with auth.connection() as con:
        user=auth.require_user(request,con);con.execute('DELETE FROM auth_sessions WHERE user_id=? AND id=?',(user['id'],session_id))
    return {'ok':True}


@router.get('/events')
def events(request:Request,limit:int=50):
    with auth.connection() as con:
        auth.require_user(request,con)
        return [dict(row) for row in con.execute('SELECT * FROM auth_events ORDER BY id DESC LIMIT ?',(min(500,max(1,limit)),))]


@router.get('/contacts')
def contacts(request:Request):
    with auth.connection() as con: auth.require_user(request,con)
    return [{'kind':kind,'label':label,'bound':False,'value':None,'masked':'','verified_at':None} for kind,label in [('phone','手机'),('wechat','微信'),('qq','QQ')]]


@router.post('/verification/request')
def verification():
    raise HTTPException(503,'当前恢复版本没有真实渠道发送器，联系渠道验证暂不可用')
