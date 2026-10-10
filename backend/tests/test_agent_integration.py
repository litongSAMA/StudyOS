"""Host DB/index/auth, two devices, conversation and push integration."""
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import text
from app.infra.db import SessionLocal
from app.infra.models import Task, PushDelivery, PushSubscription
from app.domain.core_bridge import HostDatabase, update_indexes
from app.domain.sync import build_snapshot, apply_snapshot, SyncError
from app.infra.timeutil import utcnow, iso_utc
from agent_core import AgentService, AgentError
from test_sync import _device


def payload():
    return {'tasks':[{'title':'期末考试','kind':'exam','course':'操作系统','due_at':iso_utc(utcnow()+timedelta(days=10)),'confidence':1}], 'materials':[{'title':'内存管理讲义','type':'handout','course':'操作系统','summary':'虚拟内存页面置换和缺页中断。','confidence':1}]}


def apply(client,body=None,key=None,headers=None):
    response=client.post('/api/agent/apply',json=body or payload(),headers={'Idempotency-Key':key or str(uuid4()),**(headers or {})})
    assert response.status_code==200,response.text
    return response.json()


def register(client,name='测试用户'):
    response=client.post('/api/auth/register',json={'username':name,'password':'testing-123'})
    assert response.status_code==200,response.text
    return {'Authorization':'Bearer '+response.json()['token']}


def test_auth_protects_agent_and_scopes_history(client):
    headers=register(client)
    assert client.post('/api/auth/enable',json={'enabled':True},headers=headers).status_code==200
    assert client.get('/api/agent/capabilities').status_code==401
    result=apply(client,headers=headers)
    assert client.get('/api/agent/operations',headers=headers).json()[0]['id']==result['operation_id']
    assert client.post('/api/agent/apply',json=payload(),headers={**headers,'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/auth/logout',headers=headers).status_code==200
    assert client.get('/api/tasks',headers=headers).status_code==401


def test_password_change_revokes_other_session(client):
    first=register(client)
    second={'Authorization':'Bearer '+client.post('/api/auth/login',json={'username':'测试用户','password':'testing-123'}).json()['token']}
    assert client.post('/api/auth/password',headers=first,json={'current_password':'testing-123','new_password':'changed-123'}).status_code==200
    assert client.get('/api/auth/status',headers=second).json()['user'] is None
    assert client.get('/api/auth/status',headers=first).json()['user']['username']=='测试用户'


def test_host_orm_index_and_undo(client):
    result=apply(client);task=result['created_tasks'][0]
    assert client.get('/api/tasks').json()[0]['due_at']==task['due_at'].replace('+00:00','Z')
    found=client.get('/api/search',params={'q':'缺页','mode':'hybrid'}).json()
    assert found['total']==1 and found['materials'][0]['id']==result['created_materials'][0]['id']
    assert client.post(f"/api/agent/operations/{result['operation_id']}/undo",json={},headers={'Idempotency-Key':'undo-host'}).status_code==200
    assert client.get('/api/search',params={'q':'缺页'}).json()['total']==0
    with SessionLocal() as db: assert set(db.execute(text('SELECT entity FROM tombstones')).scalars())=={'course','material','task'}


def test_focus_reference_blocks_undo(client):
    result=apply(client);row=result['created_tasks'][0]
    focus=client.post('/api/focus/sessions',json={'mode':'focus','planned_min':25,'duration_min':10,'completed':False,'started_at':iso_utc(utcnow()),'task_id':row['id'],'course_id':row['course_id']})
    assert focus.status_code==201,focus.text
    response=client.post(f"/api/agent/operations/{result['operation_id']}/undo",json={},headers={'Idempotency-Key':'undo-with-focus'})
    assert response.status_code==409
    assert client.get('/api/focus/sessions').json()[0]['task_id']==row['id']
    stats=client.get('/api/focus/stats').json()
    assert stats['today']['minutes']==10 and stats['total_minutes']==10 and len(stats['week'])==7


def test_multiturn_retry_revision_and_scope(client):
    apply(client);cid=str(uuid4());body={'conversation_id':cid,'turn_id':str(uuid4()),'expected_revision':0,'message':'帮我找缺页的资料','course':'操作系统'}
    first=client.post('/api/agent/chat',json=body)
    assert first.status_code==200,first.text
    assert first.json()['context']['retrieval_engine'].startswith('hybrid') and first.json()['context']['materials_total']==1
    replay=client.post('/api/agent/chat',json=body).json()
    assert replay['turn_replayed'] and replay['revision']==1
    second=client.post('/api/agent/chat',json={**body,'turn_id':str(uuid4()),'message':'再详细一些','expected_revision':1})
    assert second.status_code==200 and second.json()['context']['materials_total']==1,second.text
    assert len(client.get('/api/agent/conversations/'+cid).json()['messages'])==4
    assert client.post('/api/agent/chat',json={**body,'turn_id':str(uuid4())}).status_code==409
    headers=register(client)
    assert client.get('/api/agent/conversations/'+cid,headers=headers).status_code==404
    assert client.post('/api/agent/chat',json=body,headers=headers).status_code==404


def test_exam_delay_linked_replan_requires_confirmation(client):
    result=apply(client);task=result['created_tasks'][0]
    preview=client.post('/api/agent/review/generate',json={'exam_task_ids':[task['id']]}).json()
    assert 'actions' not in preview and 'checks' not in preview
    saved=client.post(f"/api/agent/plans/{preview['plan_id']}/apply",json={},headers={'Idempotency-Key':'gen-plan'})
    assert saved.status_code==200,saved.text
    with SessionLocal() as db:
        row=db.execute(text('SELECT id,sessions FROM review_plans')).first();plan_id=row.id;before=row.sessions
    delayed=client.post('/api/agent/deadline/preview',json={'task_id':task['id'],'message':'期末考试延期','new_due_at':iso_utc(utcnow()+timedelta(days=15))}).json()
    done=client.post(f"/api/agent/plans/{delayed['plan_id']}/apply",json={},headers={'Idempotency-Key':'delay-exam'})
    assert done.status_code==200 and done.json()['followups'][0]['review_plan_id']==plan_id,done.text
    with SessionLocal() as db: assert db.execute(text('SELECT sessions FROM review_plans WHERE id=:id'),{'id':plan_id}).scalar()==before
    replanned=client.post('/api/agent/review/replan',json={'plan_id':plan_id}).json()
    assert replanned['result']['sessions']
    assert client.post(f"/api/agent/plans/{replanned['plan_id']}/apply",json={},headers={'Idempotency-Key':'linked-replan'}).status_code==200


def test_sync_receipts_remap_device_ids(client):
    body=payload();result=apply(client,body,key='portable-ingest')
    with SessionLocal() as db: snap=build_snapshot(db)
    factory=_device()
    with factory() as db:
        db.add(Task(title='本机旧任务',kind='other',due_at=utcnow()));db.commit();apply_snapshot(db,snap);path=db.get_bind().url.database
    core=AgentService(path,database=HostDatabase(path),change_hook=update_indexes)
    retry=core.apply(body,request_key='portable-ingest')
    assert retry['remote_receipt'] and retry['created_tasks'][0]['sync_uuid']==result['created_tasks'][0]['sync_uuid']
    assert retry['created_tasks'][0]['id']!=result['created_tasks'][0]['id']
    with factory() as db: assert db.query(Task).count()==2
    with pytest.raises(AgentError) as conflict: core.apply({**body,'text':'changed'},request_key='portable-ingest')
    assert conflict.value.status==409


def test_receipt_conflict_rolls_back_sync(client):
    apply(client,key='collision')
    with SessionLocal() as db: snap=build_snapshot(db)
    snap['agent_receipts'][0]['request_hash']='different';snap['courses'][0]['name']='不应被保存'
    with SessionLocal() as db:
        with pytest.raises(SyncError): apply_snapshot(db,snap)
        assert db.execute(text('SELECT name FROM courses')).scalar()=='操作系统'


def test_webpush_transport_ttl(client,monkeypatch):
    import app.infra.push as push
    captured={};monkeypatch.setattr(push,'webpush',lambda *args,**kw:captured.update(kw))
    push.send_to_subscription(PushSubscription(endpoint='https://push.example/x',p256dh='key',auth='secret'),{'title':'test'})
    assert captured['ttl']==21600 and captured['vapid_private_key']


def test_inflight_push_cannot_mark_changed_deadline(client,monkeypatch):
    import app.infra.push as push
    with SessionLocal() as db: db.add(PushSubscription(endpoint='https://push.example/x',p256dh='k',auth='a'));db.commit()
    body=payload();body['tasks'][0]['due_at']=iso_utc(utcnow()+timedelta(hours=1));result=apply(client,body)
    def during_send(sub,message):
        proposal=client.post('/api/agent/deadline/preview',json={'task_id':result['created_tasks'][0]['id'],'message':'考试延期','new_due_at':iso_utc(utcnow()+timedelta(days=15))}).json()
        assert client.post(f"/api/agent/plans/{proposal['plan_id']}/apply",json={},headers={'Idempotency-Key':'inflight-delay'}).status_code==200
    monkeypatch.setattr(push,'send_to_subscription',during_send)
    assert push.push_tick()['sent']==0
    with SessionLocal() as db: assert db.query(PushDelivery).count()==0


def test_two_offline_devices_apply_same_portable_queue_then_converge(client):
    body=payload();first=apply(client,body,key='shared-offline-queue')
    factory=_device()
    with factory() as db: path=db.get_bind().url.database
    other=AgentService(path,database=HostDatabase(path),change_hook=update_indexes)
    second=other.apply(body,request_key='shared-offline-queue')
    assert first['created_tasks'][0]['sync_uuid']==second['created_tasks'][0]['sync_uuid']
    assert first['created_materials'][0]['sync_uuid']==second['created_materials'][0]['sync_uuid']
    with SessionLocal() as db: source=build_snapshot(db)
    with factory() as db: apply_snapshot(db,source);back=build_snapshot(db)
    with SessionLocal() as db:
        apply_snapshot(db,back)
        assert db.query(Task).count()==1
        assert db.execute(text('SELECT count(*) FROM materials')).scalar()==1
    with factory() as db: assert db.query(Task).count()==1


def test_attachment_apply_indexes_full_content_beyond_preview(client):
    text_body='开头讲义。'*400+'独有的伙伴分配算法终章。'
    upload=client.post('/api/materials/upload',files={'file':('long.txt',text_body.encode(),'text/plain')})
    assert upload.status_code==200,upload.text
    file=upload.json()
    result=apply(client,{'tasks':[],'materials':[{'title':'长篇讲义','type':'handout','confidence':1}], 'file_path':file['stored_path'],'file_name':file['file_name'],'source':'file','text':file['extracted_text_preview']})
    assert '伙伴分配' in result['created_materials'][0]['text']
    assert client.get('/api/search',params={'q':'伙伴分配'}).json()['total']==1


def test_focus_progress_sync_remaps_links_and_propagates_deletion(client):
    from app.infra.models import FocusSession, Course
    result=apply(client);task=result['created_tasks'][0]
    created=client.post('/api/focus/sessions',json={'mode':'focus','planned_min':25,'duration_min':20,'completed':False,'started_at':iso_utc(utcnow()),'task_id':task['id'],'course_id':task['course_id'],'material_ids':[result['created_materials'][0]['id']]}).json()
    with SessionLocal() as db: snap=build_snapshot(db)
    factory=_device()
    with factory() as db:
        db.add(Course(name='原有课程'));db.add(Task(title='原有任务',due_at=utcnow()));db.commit()
        apply_snapshot(db,snap)
        row=db.query(FocusSession).one();assert row.duration_min==20 and row.task_id!=task['id']
        assert db.get(Task,row.task_id).title=='期末考试' and db.get(Course,row.course_id).name=='操作系统'
    assert client.delete(f"/api/focus/sessions/{created['id']}").status_code==204
    with SessionLocal() as db: deleted=build_snapshot(db)
    with factory() as db: apply_snapshot(db,deleted);assert db.query(FocusSession).count()==0


def test_remote_deadline_resets_local_reminder_markers(client):
    result=apply(client);tid=result['created_tasks'][0]['id']
    with SessionLocal() as db:
        db.add(PushDelivery(task_id=tid,subscription_id=123,offset_h=24));db.commit();snap=build_snapshot(db)
        snap['tasks'][0]['due_at']=iso_utc(utcnow()+timedelta(days=20));snap['tasks'][0]['updated_at']=iso_utc(utcnow()+timedelta(seconds=1))
        apply_snapshot(db,snap);assert db.query(PushDelivery).count()==0


def test_manual_task_edit_resets_markers(client):
    result=apply(client);tid=result['created_tasks'][0]['id']
    with SessionLocal() as db: db.add(PushDelivery(task_id=tid,subscription_id=123,offset_h=24));db.commit()
    assert client.patch(f'/api/tasks/{tid}',json={'due_at':iso_utc(utcnow()+timedelta(days=20))}).status_code==200
    with SessionLocal() as db: assert db.query(PushDelivery).count()==0


def test_failed_sync_preserves_old_attachment_and_removes_partial_new_file(client):
    import base64
    from pathlib import Path
    from app.infra.filestore import files_root
    from app.infra.models import Material
    upload=client.post('/api/materials/upload',files={'file':('proof.txt',b'old content','text/plain')}).json()
    result=apply(client,{'tasks':[],'materials':[{'title':'附件','type':'handout','confidence':1}],'file_path':upload['stored_path'],'source':'file'})
    old=Path(upload['stored_path']);before=set(files_root().iterdir())
    with SessionLocal() as db: snap=build_snapshot(db)
    snap['materials'][0]['file_b64']=base64.b64encode(b'new content').decode();snap['materials'][0]['updated_at']=iso_utc(utcnow()+timedelta(seconds=1))
    snap['quizzes']=[{'sync_uuid':'invalid','updated_at':iso_utc(utcnow()),'questions':[]}]
    with SessionLocal() as db:
        with pytest.raises(SyncError): apply_snapshot(db,snap)
        assert db.query(Material).one().file_path==str(old)
    assert old.read_bytes()==b'old content' and set(files_root().iterdir())==before


def test_explicit_empty_reminders_remain_disabled_after_sync(client):
    from app.infra import push
    body=payload();body['tasks'][0]['remind_offsets_h']=[];body['tasks'][0]['due_at']=iso_utc(utcnow()+timedelta(hours=1));result=apply(client,body)
    with SessionLocal() as db:
        assert push._offsets_of(db.get(Task,result['created_tasks'][0]['id']))==[]
        snap=build_snapshot(db)
    factory=_device()
    with factory() as db:
        apply_snapshot(db,snap)
        assert db.query(Task).one().remind_offsets_h==[]


def test_grounded_model_receives_history_but_cannot_issue_actions(client,monkeypatch):
    import app.domain.agent.llm as llm
    import json
    apply(client);captured=[]
    def answer(system,body,**kwargs):
        captured.append(json.loads(body))
        return {'reply':'根据虚拟内存讲义，缺页中断触发页面调入。','actions':[{'op':'delete','table':'tasks'}]}
    monkeypatch.setattr(llm,'chat_json',answer)
    cid=str(uuid4());body={'conversation_id':cid,'turn_id':str(uuid4()),'expected_revision':0,'message':'找缺页的资料'}
    first=client.post('/api/agent/chat',json=body).json()
    second=client.post('/api/agent/chat',json={**body,'turn_id':str(uuid4()),'expected_revision':1,'message':'再详细一些'}).json()
    assert first['engine']==second['engine']=='llm'
    assert len(captured[1]['history'])==2 and captured[1]['sources'][0]['title']=='内存管理讲义'
    assert 'actions' not in second and 'proposal' not in second
    with SessionLocal() as db: assert db.query(Task).count()==1


def test_device_sync_token_works_under_login_protection(client):
    headers=register(client);apply(client,headers=headers)
    client.post('/api/auth/enable',json={'enabled':True},headers=headers)
    token=client.post('/api/sync/token/reveal',headers=headers).json()['token']
    result=client.get('/api/sync/snapshot',headers={'X-Sync-Token':token})
    assert result.status_code==200 and len(result.json()['tasks'])==1
    assert client.get('/api/agent/capabilities',headers={'X-Sync-Token':token}).status_code==401
