"""Full production app against synthetic temporary data, for Web/Mobile UI QA."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timedelta,timezone
import httpx

BACKEND=Path(__file__).resolve().parents[1]
ROOT=BACKEND.parent
PORTS=[8892,8893]


def main():
    for port in PORTS:
        with socket.socket() as sock: sock.bind(('127.0.0.1',port))
    processes=[]
    with tempfile.TemporaryDirectory(prefix='studyos-formal-app-') as directory:
        env={**os.environ,'DATA_DIR':directory,'DB_PATH':str(Path(directory)/'studyos.db'),'VAPID_KEYS_FILE':str(Path(directory)/'vapid_keys.json'),'OCR_ENABLED':'0','PUSH_TICK_SECONDS':'3600','EMBED_AUTO_OLLAMA':'0','LLM_API_KEY':'','LLM_BASE_URL':'https://api.deepseek.com/v1','EMBED_BASE_URL':''}
        def stop(*args): raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,stop)
        try:
            for port,front in zip(PORTS,['web','mobile']):
                child=subprocess.Popen([sys.executable,'-m','uvicorn','app.api.main:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],cwd=BACKEND,env={**env,'WEB_DIST':str(ROOT/front/'dist')})
                processes.append(child)
                for attempt in range(100):
                    if child.poll() is not None: raise RuntimeError('测试服务启动失败')
                    try:
                        if httpx.get(f'http://127.0.0.1:{port}/api/health',timeout=1).status_code==200: break
                    except httpx.HTTPError: pass
                    time.sleep(.1)
                else: raise RuntimeError('测试服务启动超时')
                if port==PORTS[0]:
                    with httpx.Client(base_url=f'http://127.0.0.1:{port}') as client:
                        account=client.post('/api/auth/register',json={'username':'fixture_student','password':'Fixture-test-123'});account.raise_for_status()
                        client.headers['Authorization']='Bearer '+account.json()['token']
                        due=(datetime.now(timezone.utc)+timedelta(days=10)).isoformat()
                        body={'tasks':[{'title':'操作系统期末考试','kind':'exam','course':'操作系统','due_at':due,'confidence':1}],'materials':[{'title':'虚拟内存讲义','type':'handout','course':'操作系统','summary':'分页管理将内存划分成固定大小的页。缺页中断触发页面调入。LRU 页面置换淘汰最久未使用的页面。','confidence':1}]}
                        created=client.post('/api/agent/apply',json=body,headers={'Idempotency-Key':'fixture-seed'});created.raise_for_status()
                        client.post('/api/auth/enable',json={'enabled':True}).raise_for_status()
            print('Synthetic full-app fixtures ready: Web :8892 / Mobile :8893',flush=True)
            while all(p.poll() is None for p in processes): time.sleep(1)
        except KeyboardInterrupt: pass
        finally:
            for child in processes: child.terminate()
            for child in processes:
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired: child.kill();child.wait()


if __name__=='__main__': main()
