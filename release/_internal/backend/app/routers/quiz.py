from fastapi import APIRouter,Depends,HTTPException,Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel,Field
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Quiz,Course,Material,Note
from ..quiz_engine import generate
from ..timeutil import iso_utc,utcnow

router=APIRouter(prefix='/api/quiz',tags=['quiz'])
class CreateIn(BaseModel):
    course_id:int|None=None
    material_id:int|None=None
    note_ids:list[int]=Field(default_factory=list,max_length=100)
    text:str=Field(default='',max_length=100000)
    qtype:str='mixed'
    n:int=6
    title_hint:str=Field(default='',max_length=300)
class AnswerIn(BaseModel):
    index:int=Field(ge=0)
    choice:str|None=None
    answer_text:str=Field(default='',max_length=10000)
    ok:bool|None=None


def out(db,row):
    course=db.get(Course,row.course_id) if row.course_id else None;material=db.get(Material,row.material_id) if row.material_id else None
    results=row.results or [];correct=sum(r['verdict'] in {'correct','self_ok'} for r in results)
    return {'id':row.id,'course_id':row.course_id,'course_name':course.name if course else None,'material_id':row.material_id,'material_title':material.title if material else '', 'note_ids':row.note_ids,'title':row.title,'qtype':row.qtype,'questions':row.questions,'results':results,'engine':row.engine,'created_at':iso_utc(row.created_at),'answered':len(results),'total':len(row.questions),'correct':correct,'wrong':len(results)-correct,'score_pct':round(100*correct/len(results),1) if results else 0}


def fetch(db,qid):
    row=db.get(Quiz,qid)
    if not row: raise HTTPException(404,'自测不存在')
    return row


@router.post('',status_code=201)
def create(body:CreateIn,db:Session=Depends(get_db)):
    if body.qtype not in {'mixed','choice','qa'}: raise HTTPException(422,'题型无效')
    course=body.course_id;material=None;notes=[];source=body.text.strip()
    if course and not db.get(Course,course): raise HTTPException(404,'课程不存在')
    if body.material_id:
        material=db.get(Material,body.material_id)
        if not material: raise HTTPException(404,'资料不存在')
        course=material.course_id;source=material.text or ''
    elif body.note_ids:
        notes=[db.get(Note,nid) for nid in dict.fromkeys(body.note_ids)]
        if any(note is None for note in notes): raise HTTPException(404,'笔记不存在')
    elif not source and course:
        mids=list(db.scalars(select(Material.id).where(Material.course_id==course)))
        notes=list(db.scalars(select(Note).where(Note.material_id.in_(mids)))) if mids else []
        if not notes: source='\n'.join(db.scalars(select(Material.text).where(Material.course_id==course)))
    if notes:
        source='\n'.join(note.summary+'\n'+'\n'.join(str(point.get('point','')) for point in note.points) for note in notes)
        if not course and notes[0].material_id:
            linked=db.get(Material,notes[0].material_id);course=linked.course_id if linked else None
    if not source.strip(): raise HTTPException(422,'没有可出题的全文或笔记，请补充素材')
    questions,engine=generate(source,body.qtype,max(1,min(20,body.n)))
    row=Quiz(course_id=course,material_id=material.id if material else None,note_ids=[n.id for n in notes],title=body.title_hint or (material.title if material else '学习自测'),qtype=body.qtype,questions=questions,results=[],engine=engine)
    db.add(row);db.commit();db.refresh(row);return out(db,row)


@router.get('')
def list_quizzes(response:Response,course_id:int|None=None,limit:int=50,offset:int=0,db:Session=Depends(get_db)):
    query=select(Quiz)
    if course_id is not None: query=query.where(Quiz.course_id==course_id)
    total=db.scalar(select(func.count()).select_from(query.subquery()));limit=min(200,max(1,limit));offset=max(0,offset)
    response.headers.update({'X-Total-Count':str(total),'X-Limit':str(limit),'X-Offset':str(offset),'X-Has-More':str(int(offset+limit<total))})
    return [out(db,row) for row in db.scalars(query.order_by(Quiz.id.desc()).offset(offset).limit(limit))]


@router.get('/stats')
def stats(db:Session=Depends(get_db)):
    result={key:0 for key in ['total','questions','answered','correct','choice_total','choice_answered','choice_correct','choice_pct','qa_total','qa_answered','qa_ok','qa_pct']};courses={}
    for row in db.scalars(select(Quiz)):
        data=out(db,row);result['total']+=1;result['questions']+=data['total'];result['answered']+=data['answered'];result['correct']+=data['correct']
        group=courses.setdefault(row.course_id,{'course_id':row.course_id,'course_name':data['course_name'] or '未分类','total':0,'answered':0,'correct':0});group['total']+=1;group['answered']+=data['answered'];group['correct']+=data['correct']
        answered={r['index']:r for r in row.results}
        for index,q in enumerate(row.questions):
            kind=q['type'];result[kind+'_total']+=1
            if index in answered:
                result[kind+'_answered']+=1
                result['choice_correct' if kind=='choice' else 'qa_ok']+=int(answered[index]['verdict'] in {'correct','self_ok'})
    for kind,key in [('choice','choice_correct'),('qa','qa_ok')]: result[kind+'_pct']=round(100*result[key]/result[kind+'_answered'],1) if result[kind+'_answered'] else 0
    result['by_course']=list(courses.values());return result


def markdown(db,rows):
    sections=[]
    for row in rows:
        sections.append('# '+row.title)
        for index,q in enumerate(row.questions): sections.append(f"\n{index+1}. {q['stem']}\n"+'\n'.join(f"{chr(65+i)}. {o}" for i,o in enumerate(q['options']))+f"\n参考答案：{q['answer']}\n依据：{q['source']}")
    return PlainTextResponse('\n\n'.join(sections),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename="studyos-quizzes.md"'})


@router.get('/export/md')
def export_all(db:Session=Depends(get_db)): return markdown(db,list(db.scalars(select(Quiz).order_by(Quiz.id))))
@router.get('/{quiz_id}/md')
def export_one(quiz_id:int,db:Session=Depends(get_db)): return markdown(db,[fetch(db,quiz_id)])
@router.get('/{quiz_id}')
def get(quiz_id:int,db:Session=Depends(get_db)): return out(db,fetch(db,quiz_id))
@router.delete('/{quiz_id}',status_code=204)
def remove(quiz_id:int,db:Session=Depends(get_db)):
    db.delete(fetch(db,quiz_id));db.commit()
@router.post('/{quiz_id}/reset')
def reset(quiz_id:int,db:Session=Depends(get_db)):
    row=fetch(db,quiz_id);row.results=[];db.commit();return out(db,row)
@router.post('/{quiz_id}/answer')
def answer(quiz_id:int,body:AnswerIn,db:Session=Depends(get_db)):
    # Serialize read-modify-write so concurrent answers cannot overwrite each other.
    from sqlalchemy import text
    db.execute(text('BEGIN IMMEDIATE'))
    row=fetch(db,quiz_id)
    if body.index>=len(row.questions): raise HTTPException(422,'题号越界')
    q=row.questions[body.index]
    if q['type']=='choice':
        choice=(body.choice or '').strip().upper()
        if len(choice)!=1 or not 0<=ord(choice)-65<len(q['options']): raise HTTPException(422,'选择项无效')
        verdict='correct' if choice==q['answer'] else 'wrong'
    else:
        if body.ok is None: raise HTTPException(422,'请提交问答自评')
        choice='';verdict='self_ok' if body.ok else 'self_wrong'
    row.results=[r for r in row.results if r['index']!=body.index]+[{'index':body.index,'verdict':verdict,'chosen':choice,'answer_text':body.answer_text,'answered_at':iso_utc(utcnow())}]
    db.commit();return out(db,row)
