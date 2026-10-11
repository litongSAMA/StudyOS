import json
from sqlalchemy import text
from agent_core.receipts import pack
from agent_core.common import canonical


def export_receipts(db):
    from .sync import get_device_id
    tables=set(db.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars())
    if 'agent_operations' not in tables: return []
    own=[{'scope':row.scope,'request_key':row.request_key,'request_hash':row.request_hash,'response':pack(json.loads(row.response)),'created_at':row.created_at,'origin_device':get_device_id(db)} for row in db.execute(text("SELECT scope,request_key,request_hash,response,created_at FROM agent_operations WHERE kind='ingest' AND undone_at IS NULL"))]
    remote=[{**dict(row._mapping),'response':json.loads(row.response)} for row in db.execute(text('SELECT * FROM agent_remote_receipts'))]
    # Keep a bounded transfer window, while local durable receipts remain intact.
    return sorted(own+remote,key=lambda row:row['created_at'],reverse=True)[:1000]


def merge_receipts(db,items):
    if not isinstance(items,list) or len(items)>1000:
        from .sync import SyncError
        raise SyncError('跨设备回执格式或条数无效')
    from agent_core.receipts import SCHEMA
    db.execute(text(SCHEMA.strip()))
    has_local=bool(db.execute(text("SELECT 1 FROM sqlite_master WHERE name='agent_operations'")).scalar())
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('response'),dict): continue
        if any(not isinstance(item.get(key),str) or len(item[key])>256 for key in ['scope','request_key','request_hash','origin_device','created_at']): continue
        response=item['response']
        if any(not isinstance(response.get(key,[]),list) or len(response.get(key,[]))>100 or any(not isinstance(uid,str) or len(uid)>60 for uid in response.get(key,[])) for key in ['created_tasks','reused_tasks','created_materials','reused_materials']): continue
        own=db.execute(text('SELECT request_hash FROM agent_operations WHERE scope=:s AND request_key=:k'),{'s':item['scope'],'k':item['request_key']}).first() if has_local else None
        old=db.execute(text('SELECT request_hash FROM agent_remote_receipts WHERE scope=:s AND request_key=:k'),{'s':item['scope'],'k':item['request_key']}).first()
        if own or old:
            if (own or old)[0]!=item['request_hash']:
                from .sync import SyncError
                raise SyncError('同步回执存在幂等键冲突')
            continue
        db.execute(text('INSERT INTO agent_remote_receipts(scope,request_key,request_hash,response,created_at,origin_device) VALUES (:scope,:request_key,:request_hash,:response,:created_at,:origin_device)'),{**item,'response':canonical(response)})
