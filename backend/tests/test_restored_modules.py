from datetime import timedelta
from sqlalchemy import text
from app.infra.db import SessionLocal
from app.infra.models import Quiz,Task,Material
from app.domain.sync import build_snapshot,apply_snapshot
from app.infra.timeutil import utcnow,iso_utc
from test_sync import _device

SOURCE='进程是执行中的程序。死锁是多个进程互相等待资源。分页是固定大小的内存管理方式。中断是响应外部事件的机制。缓存是保存近期常用数据的存储。'


def test_quiz_choices_answer_override_reset_and_stats(client):
    created=client.post('/api/quiz',json={'text':SOURCE,'qtype':'choice','n':4})
    assert created.status_code==201,created.text
    quiz=created.json();qid=quiz['id'];assert len(quiz['questions'])==4
    for q in quiz['questions']: assert len(set(q['options']))==4 and q['answer']==chr(65+q['answer_index']) and q['source'] in SOURCE
    answered=client.post(f'/api/quiz/{qid}/answer',json={'index':0,'choice':quiz['questions'][0]['answer']}).json()
    assert answered['correct']==1
    wrong=next(chr(65+i) for i in range(4) if i!=quiz['questions'][0]['answer_index'])
    replaced=client.post(f'/api/quiz/{qid}/answer',json={'index':0,'choice':wrong}).json()
    assert replaced['answered']==1 and replaced['wrong']==1
    assert client.post(f'/api/quiz/{qid}/answer',json={'index':50,'choice':'A'}).status_code==422
    assert client.get('/api/quiz/stats').json()['choice_answered']==1
    assert client.get(f'/api/quiz/{qid}/md').status_code==200
    assert client.post(f'/api/quiz/{qid}/reset').json()['answered']==0


def test_qa_requires_self_assessment_and_empty_source_rejected(client):
    assert client.post('/api/quiz',json={'text':''}).status_code==422
    quiz=client.post('/api/quiz',json={'text':SOURCE,'qtype':'qa','n':2}).json()
    assert client.post(f"/api/quiz/{quiz['id']}/answer",json={'index':0,'answer_text':'回忆'}).status_code==422
    assert client.post(f"/api/quiz/{quiz['id']}/answer",json={'index':0,'ok':True,'answer_text':'回忆'}).json()['correct']==1


def test_quiz_sync_remaps_sources_answers_and_tombstones(client):
    course=client.post('/api/courses',json={'name':'操作系统'}).json();mat=client.post('/api/materials',json={'title':'课件','course_id':course['id'],'text':SOURCE}).json()
    quiz=client.post('/api/quiz',json={'material_id':mat['id'],'qtype':'qa','n':2}).json()
    client.post(f"/api/quiz/{quiz['id']}/answer",json={'index':0,'ok':True})
    with SessionLocal() as db: snapshot=build_snapshot(db)
    factory=_device()
    with factory() as db:
        db.add(Material(title='本机资料'));db.commit();apply_snapshot(db,snapshot)
        row=db.query(Quiz).one();assert row.material_id!=mat['id'] and row.results[0]['verdict']=='self_ok'
    client.delete(f"/api/quiz/{quiz['id']}")
    with SessionLocal() as db: snapshot=build_snapshot(db)
    with factory() as db: apply_snapshot(db,snapshot);assert db.query(Quiz).count()==0


def test_report_empty_and_real_progress(client):
    assert client.get('/api/report').json()['overview']['stats']['materials']==0
    course=client.post('/api/courses',json={'name':'操作系统','semester':'2026秋'}).json()
    mat=client.post('/api/materials',json={'title':'讲义','text':SOURCE,'course_id':course['id']}).json()
    client.post('/api/review/rate',json={'material_id':mat['id'],'rating':'easy'})
    client.post('/api/review/rate',json={'material_id':mat['id'],'rating':'again'})
    report=client.get('/api/report').json();assert report['overview']['stats']['reviews']==2 and report['semesters'][0]['semester']=='2026秋'
    state=client.get('/api/review/states').json()[0]
    assert report['overview']['series'][-1]['mastery']==round(state['mastery'],2)
    assert '最近 20 次' in client.get('/api/report/md').text


def test_legacy_focus_import_is_atomic_idempotent_and_visible_in_dashboard(client):
    from app.infra.timeutil import local_date
    body={'days':[{'date':local_date(utcnow()).isoformat(),'count':2,'minutes':50}]}
    assert client.post('/api/focus/import-legacy',json=body).json()['imported']==2
    assert client.post('/api/focus/import-legacy',json=body).json()['imported']==0
    changed={'days':[{**body['days'][0],'minutes':60}]}
    assert client.post('/api/focus/import-legacy',json=changed).status_code==409
    stats=client.get('/api/stats').json();assert stats['focus_count_today']==2 and stats['focus_minutes_today']==50


def test_llm_quiz_failure_falls_back_to_source_and_filters_invalid_questions(client,monkeypatch):
    import app.domain.quiz_engine as engine
    monkeypatch.setattr(engine,'chat_json',lambda *args,**kwargs:{'questions':[{'type':'choice','stem':'错题','answer':'Z','options':['a','b','c']} ]})
    questions,kind=engine.generate(SOURCE,'mixed',4)
    assert kind=='rule' and len(questions)==4 and all(q['source'] in SOURCE for q in questions)


def test_quiz_material_notes_and_course_sources(client):
    course=client.post('/api/courses',json={'name':'操作系统'}).json()
    material=client.post('/api/materials',json={'title':'讲义','text':SOURCE,'course_id':course['id']}).json()
    note=client.post('/api/notes',json={'title':'复习笔记','material_id':material['id'],'summary':'进程是执行中的程序','points':[{'point':'死锁是循环等待','importance':'high'}]}).json()
    from_notes=client.post('/api/quiz',json={'note_ids':[note['id']],'qtype':'qa'}).json()
    from_course=client.post('/api/quiz',json={'course_id':course['id'],'qtype':'qa'}).json()
    assert from_notes['course_id']==course['id'] and from_course['note_ids']==[note['id']]
    assert all(q['source']!='' for q in from_notes['questions'])
