"""Local recovery tool. No remote password reset endpoint is exposed."""
import argparse
from getpass import getpass
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description='StudyOS 本机账号恢复')
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('action',choices=['list-users','reset-password','disable-protection','delete-user'])
    parser.add_argument('username',nargs='?')
    args=parser.parse_args()
    if args.data_dir:
        from .config import settings
        settings.data_dir=args.data_dir.resolve();settings.db_path=settings.data_dir/'studyos.db'
    from . import auth
    auth.init_auth()
    if args.action=='reset-password':
        if not args.username: parser.error('需要用户名')
        password=getpass('新密码：');auth.validate_password(password,args.username)
        if getpass('再次输入：')!=password: parser.error('两次密码不一致')
        hashed=auth.hash_password(password)
    with auth.connection() as con:
        if args.action=='list-users':
            for row in con.execute('SELECT username FROM users ORDER BY id'): print(row[0])
            return
        if args.action=='disable-protection':
            con.execute("INSERT OR REPLACE INTO auth_flags VALUES ('enabled','0')")
        else:
            user=con.execute('SELECT id FROM users WHERE username_key=?',((args.username or '').casefold(),)).fetchone()
            if not user: parser.error('账号不存在')
            if args.action=='reset-password':
                con.execute('UPDATE users SET password_hash=? WHERE id=?',(hashed,user['id']))
                con.execute('DELETE FROM auth_sessions WHERE user_id=?',(user['id'],))
            else:
                if auth.enabled(con) and con.execute('SELECT count(*) FROM users').fetchone()[0]<=1: parser.error('请先关闭保护再删除最后一个账号')
                con.execute('DELETE FROM users WHERE id=?',(user['id'],))
        auth.event(con,'cli_'+args.action,args.username or '',source_ip='localhost')
    print('已完成')


if __name__=='__main__': main()
