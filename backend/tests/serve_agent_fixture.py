"""本机浏览器集成夹具：真实 Agent 核心、临时库、固定时钟和测试授权。
不用于真实数据库，不提供原应用的其它后端功能。
"""
from pathlib import Path
from datetime import datetime, timezone
from wsgiref.simple_server import make_server, WSGIRequestHandler
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer
import json, tempfile, threading, sys, mimetypes
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_core import AgentService
from agent_core.api import AgentAPI
ROOT = Path(__file__).resolve().parents[2]
temp = tempfile.TemporaryDirectory(prefix='studyos-agent-integration-')
service = AgentService(Path(temp.name)/'demo.sqlite', bootstrap=True, clock=lambda: datetime(2026,10,3,4,0,tzinfo=timezone.utc))
service.apply({'tasks':[{'title':'实验报告','kind':'lab','course':'操作系统','due_at':'2026-10-05T15:59:00Z','confidence':.95,'note':'报告需提交PDF','source_text':'10月5日提交实验报告'}, {'title':'期末考试','kind':'exam','course':'操作系统','due_at':'2026-10-15T02:00:00Z','confidence':1,'note':'','source_text':'期末考试通知'}], 'materials':[{'title':'第3章 内存管理','type':'courseware','course':'操作系统','chapter':'第3章','difficulty':'medium','exam_focus':True,'tags':['内存','考点'],'summary':'内存管理包括分页、分段和虚拟内存。缺页中断需要换入目标页。','confidence':1},{'title':'第4章 死锁','type':'notes','course':'操作系统','chapter':'第4章','difficulty':'hard','exam_focus':True,'tags':['死锁'],'summary':'死锁条件包括互斥、持有并等待、不剥夺、循环等待。','confidence':1}]},request_key='fixture-seed',scope='seed')
with service.db.transaction() as con:
 service.db.write(con,'push_deliveries',dict(task_id=1,subscription_id=1,offset_h=24,created_at=service.now()))
api = AgentAPI(service,authorize=lambda environ:'demo')
state={'drop_next':False}
class ThreadServer(ThreadingMixIn,WSGIServer): daemon_threads=True
class Quiet(WSGIRequestHandler):
 def log_message(self,*args): pass

def app_for(name):
 def app(environ,start):
  path=environ.get('PATH_INFO','/')
  def output(data,status='200 OK'):
   body=json.dumps(data,ensure_ascii=False).encode();start(status,[('Content-Type','application/json'),('Content-Length',str(len(body))),('Cache-Control','no-store')]);return[body]
  if path=='/api/test/drop-next-response':state['drop_next']=True;return output({'armed':True})
  if path=='/api/test/state':
   with service.db.snapshot() as con:return output({'tasks':[service.db.public(con,'tasks',r[0]) for r in con.execute('SELECT id FROM tasks')],'plans':[service.db.public(con,'review_plans',r[0]) for r in con.execute('SELECT id FROM review_plans')],'deliveries':con.execute('SELECT count(*) FROM push_deliveries').fetchone()[0]})
  if path.startswith('/api/agent/'):
   response=[]
   body=api(environ,lambda s,h:response.append((s,h)))
   if state['drop_next'] and path.endswith('/apply') and response[0][0].startswith('200'):
    state['drop_next']=False;return output({'detail':'模拟响应丢失：服务端已经提交，请查询原回执'},'503 Service Unavailable')
   start(*response[0]);return body
  if path=='/api/health':return output({'status':'ok','llm':'offline','ocr':'unavailable'})
  if path=='/api/auth/status':return output({'enabled':False,'user':None,'users':[]})
  if path=='/api/courses':
   with service.db.snapshot() as con:return output([service.db.public(con,'courses',r[0]) for r in con.execute('SELECT id FROM courses')])
  if path=='/api/tasks/upcoming':return output([])
  if path=='/api/push/info':return output({'enabled':False,'health':{'degraded':False}})
  if path.startswith('/api/'):return output({'detail':'原应用后端未恢复，此测试站只接入真实 Agent 核心'},'404 Not Found')
  if path=='/sw.js':start('404 Not Found',[]);return[b'']
  root=ROOT/name/'dist';file=(root/path.lstrip('/')).resolve()
  if not file.is_relative_to(root) or not file.is_file():file=root/'index.html'
  body=file.read_bytes();start('200 OK',[('Content-Type',mimetypes.guess_type(file)[0] or 'application/octet-stream'),('Content-Length',str(len(body))),('Cache-Control','no-store')]);return[body]
 return app
servers=[]
for name,port in [('web',8890),('mobile',8891)]:
 server=make_server('127.0.0.1',port,app_for(name),server_class=ThreadServer,handler_class=Quiet);servers.append(server);threading.Thread(target=server.serve_forever,daemon=True).start();print(f'{name} agent integration: http://127.0.0.1:{port}/assistant',flush=True)
print('All agent endpoints use the real core and temporary SQLite database.',flush=True)
try:threading.Event().wait()
except KeyboardInterrupt:
 for server in servers:server.shutdown()
 temp.cleanup()
