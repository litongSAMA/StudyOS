from sqlalchemy import select
from app.infra.models import Quiz,Course,Material,Note
from app.domain.quiz_engine import clean_questions
from app.infra.timeutil import iso_utc


def export_quizzes(db,local_ids=False):
    ids={key:{row.id:row.sync_uuid for row in db.scalars(select(cls))} for key,cls in [('course',Course),('material',Material),('note',Note)]}
    result=[]
    for row in db.scalars(select(Quiz)):
        value={key:getattr(row,key) for key in ['sync_uuid','title','qtype','questions','results','engine']}
        for key in ['created_at','updated_at']: value[key]=iso_utc(getattr(row,key))
        value.update(course_uuid=ids['course'].get(row.course_id),material_uuid=ids['material'].get(row.material_id),note_uuids=[ids['note'][nid] for nid in row.note_ids if nid in ids['note']])
        if local_ids: value['id']=row.id
        result.append(value)
    return result


def merge_quizzes(db,items,tomb_map,stats):
    from app.domain.sync import SyncError,_parse_dt,_tombstone_newer
    if not isinstance(items,list) or len(items)>10000: raise SyncError('自测同步格式或数量无效')
    ids={key:{row.sync_uuid:row.id for row in db.scalars(select(cls))} for key,cls in [('course',Course),('material',Material),('note',Note)]}
    local={row.sync_uuid:row for row in db.scalars(select(Quiz))}
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('sync_uuid'),str) or not 1<=len(item['sync_uuid'])<=60: raise SyncError('自测编号无效')
        uid=item['sync_uuid'];updated=_parse_dt(item.get('updated_at'));row=local.get(uid)
        if _tombstone_newer(tomb_map,'quiz',uid,updated) or row and row.updated_at>=updated:
            stats['skipped']+=1;continue
        questions=clean_questions(item.get('questions'))
        if not questions or len(questions)!=len(item.get('questions',[])) or item.get('qtype') not in {'mixed','qa','choice'}: raise SyncError('自测题目无效')
        results=item.get('results',[])
        if not isinstance(results,list) or len(results)>len(questions): raise SyncError('自测作答记录无效')
        seen=set()
        for answer in results:
            if not isinstance(answer,dict) or isinstance(answer.get('index'),bool) or not isinstance(answer.get('index'),int) or not 0<=answer['index']<len(questions) or answer['index'] in seen or answer.get('verdict') not in {'correct','wrong','self_ok','self_wrong'}: raise SyncError('自测作答记录无效')
            if any(not isinstance(answer.get(key),str) or len(answer[key])>10000 for key in ['chosen','answer_text','answered_at']): raise SyncError('自测作答内容无效')
            seen.add(answer['index'])
        if row is None:
            row=Quiz(sync_uuid=uid);db.add(row);local[uid]=row;stats['created']+=1
        else: stats['updated']+=1
        row.title=str(item.get('title','自测'))[:300];row.qtype=item['qtype'];row.questions=questions;row.results=results;row.engine='llm' if item.get('engine')=='llm' else 'rule'
        row.course_id=ids['course'].get(item.get('course_uuid'));row.material_id=ids['material'].get(item.get('material_uuid'));row.note_ids=[ids['note'][nid] for nid in item.get('note_uuids',[]) if nid in ids['note']]
        row.created_at=_parse_dt(item.get('created_at'));row.updated_at=updated;db.flush()
