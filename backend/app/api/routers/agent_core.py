from urllib.parse import parse_qs
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from agent_core import AgentError
from agent_core.api import AgentAPI
from app.domain.core_bridge import get_core

router=APIRouter(prefix='/api/agent',tags=['agent-core'])

@router.api_route('/{path:path}',methods=['GET','POST'])
async def core_endpoint(path:str,request:Request):
    body={}
    if request.method=='POST':
        if not request.headers.get('content-type','').startswith('application/json'): raise HTTPException(415,'需要 JSON 请求')
        raw=await request.body()
        if len(raw)>2*1024*1024: raise HTTPException(413,'请求超过 2 MB')
        import json
        try:
            body=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(body,dict): raise ValueError()
        except (ValueError,UnicodeDecodeError): raise HTTPException(422,'JSON 请求无效') from None
    def dispatch():
        service=get_core()
        scope=request.state.agent_scope
        api=AgentAPI(service,authorize=lambda _:scope)
        from agent_core.common import digest
        key=request.headers.get('idempotency-key')
        if not key and path=='apply':
            if not isinstance(body.get('tasks',[]),list): raise AgentError(422,'任务建议必须是数组')
            key='legacy:'+digest(body)
            from app.infra.timeutil import iso_utc, parse_iso
            # Preserve the original API's naive-UTC convention only for old clients.
            body_copy={**body,'tasks':[dict(item) if isinstance(item,dict) else item for item in body.get('tasks',[])]}
            for item in body_copy['tasks']:
                if isinstance(item,dict) and isinstance(item.get('due_at'),str):
                    try: item['due_at']=iso_utc(parse_iso(item['due_at']))
                    except ValueError: pass
            body.clear();body.update(body_copy)
        result=api.dispatch(request.method,'/api/agent/'+path,body,parse_qs(request.url.query),scope,key)
        if path=='capabilities':
            from app.infra.db import SessionLocal
            from app.domain.sync import get_device_id
            with SessionLocal() as db: device_id=get_device_id(db)
            result.update(device_id=device_id,actor_id=scope,conversations=True,semantic=True,portable_queue=True)
        return result
    try:
        result=await run_in_threadpool(dispatch)
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except AgentError as error:
        raise HTTPException(error.status,error.detail) from None
