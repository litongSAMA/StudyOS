"""Portable ingestion receipts use entity UUIDs, never another device's integer IDs."""
import json
from .common import AgentError, canonical

SCHEMA='''
CREATE TABLE IF NOT EXISTS agent_remote_receipts(scope TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,response TEXT NOT NULL,created_at TEXT NOT NULL,origin_device TEXT NOT NULL,PRIMARY KEY(scope,request_key));
'''
FIELDS={'created_tasks':'tasks','reused_tasks':'tasks','created_materials':'materials','reused_materials':'materials'}


def pack(response):
    value={key:response.get(key) for key in ['operation_id','replayed']}
    for key,table in FIELDS.items():
        value[key]=[item['sync_uuid'] for item in response.get(key,[])]
    value['matched_courses']={}
    return value


def materialize(database,con,response):
    value=json.loads(response) if isinstance(response,str) else dict(response)
    for key,table in FIELDS.items():
        rows=[]
        for sync_uuid in value.get(key,[]):
            row=con.execute(f'SELECT id FROM {table} WHERE sync_uuid=?',(sync_uuid,)).fetchone()
            if not row: raise AgentError(409,'原请求对象已被删除或尚未同步，请先完成数据同步')
            rows.append(database.public(con,table,row['id']))
        value[key]=rows
    return {**value,'replayed':True,'remote_receipt':True}
