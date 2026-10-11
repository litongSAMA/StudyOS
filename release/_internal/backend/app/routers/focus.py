from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import FocusSession, Task, Course, ReviewPlan, Material
from ..timeutil import UTCDateTime, iso_utc, local_date, utcnow

router=APIRouter(prefix='/api/focus',tags=['focus'])
class FocusIn(BaseModel):
    mode: str
    planned_min: int=Field(ge=1,le=1440)
    duration_min: float=Field(ge=0,le=1440,allow_inf_nan=False)
    completed:bool
    interrupt_count:int=Field(default=0,ge=0,le=10000)
    task_id:int|None=None
    course_id:int|None=None
    plan_id:int|None=None
    plan_date:str=''
    material_ids:list[int]=Field(default_factory=list,max_length=100)
    note:str=Field(default='',max_length=10000)
    started_at:UTCDateTime


def out(db,row):
    data={key:getattr(row,key) for key in ['id','mode','planned_min','duration_min','completed','interrupt_count','task_id','course_id','plan_id','plan_date','material_ids','note']}
    data.update(started_at=iso_utc(row.started_at),created_at=iso_utc(row.created_at))
    for model,key,label in [(Task,'task_id','task_title'),(Course,'course_id','course_name'),(ReviewPlan,'plan_id','plan_title')]:
        linked=db.get(model,getattr(row,key)) if getattr(row,key) else None
        data[label]=(linked.name if model==Course else linked.title) if linked else None
    return data


@router.post('/sessions',status_code=201)
def create(body:FocusIn,db:Session=Depends(get_db)):
    if body.mode not in {'focus','short','long'}: raise HTTPException(422,'专注模式无效')
    if body.started_at>utcnow()+timedelta(minutes=1): raise HTTPException(422,'开始时间不能在未来')
    for model,key in [(Task,'task_id'),(Course,'course_id'),(ReviewPlan,'plan_id')]:
        if getattr(body,key) and not db.get(model,getattr(body,key)): raise HTTPException(404,'关联对象不存在')
    if body.plan_date:
        try: date.fromisoformat(body.plan_date)
        except ValueError: raise HTTPException(422,'计划日期无效') from None
    row=FocusSession(**body.model_dump())
    row.material_ids=[mid for mid in dict.fromkeys(body.material_ids) if db.get(Material,mid)]
    db.add(row);db.commit();db.refresh(row)
    return out(db,row)


@router.get('/sessions')
def sessions(response:Response,limit:int=50,offset:int=0,db:Session=Depends(get_db)):
    from sqlalchemy import func
    response.headers['X-Total-Count']=str(db.scalar(select(func.count(FocusSession.id))))
    return [out(db,row) for row in db.scalars(select(FocusSession).order_by(FocusSession.started_at.desc(),FocusSession.id.desc()).offset(max(0,offset)).limit(min(200,max(1,limit))))]


@router.delete('/sessions/{session_id}',status_code=204)
def remove(session_id:int,db:Session=Depends(get_db)):
    row=db.get(FocusSession,session_id)
    if not row: raise HTTPException(404,'专注记录不存在')
    db.delete(row);db.commit()


def statistics(db,days=7):
    today=local_date(utcnow());days=min(365,max(1,days));rows=list(db.scalars(select(FocusSession).where(FocusSession.mode=='focus')))
    buckets={};courses={};plans={}
    for row in rows:
        day=local_date(row.started_at).isoformat();item=buckets.setdefault(day,{'count':0,'minutes':0})
        item['count']+=int(row.completed);item['minutes']+=row.duration_min
        cid=row.course_id or 0;course=courses.setdefault(cid,{'course_id':row.course_id,'course_name':db.get(Course,cid).name if cid and db.get(Course,cid) else '未分类','minutes':0,'count':0})
        course['minutes']+=row.duration_min;course['count']+=int(row.completed)
        if row.plan_id and row.plan_date:
            item=plans.setdefault((row.plan_id,row.plan_date),{'plan_id':row.plan_id,'date':row.plan_date,'minutes':0,'count':0})
            item['minutes']+=row.duration_min;item['count']+=int(row.completed)
    streak=0;day=today if buckets.get(today.isoformat(),{}).get('minutes',0)>0 else today-timedelta(days=1)
    while buckets.get(day.isoformat(),{}).get('minutes',0)>0:
        streak+=1;day-=timedelta(days=1)
    current=buckets.get(today.isoformat(),{'count':0,'minutes':0})
    return {'today':{'date':today.isoformat(),**current},'streak':streak,'total_count':sum(int(r.completed) for r in rows),'total_minutes':sum(r.duration_min for r in rows),'week':[{'date':(today-timedelta(days=i)).isoformat(),**buckets.get((today-timedelta(days=i)).isoformat(),{'count':0,'minutes':0})} for i in reversed(range(days))],'by_course':list(courses.values()),'by_plan_date':[{**row,'plan_date':row['date']} for row in plans.values()]}


@router.get('/stats')
def stats(days:int=7,db:Session=Depends(get_db)): return statistics(db,days)


class LegacyDay(BaseModel):
    date:date
    count:int=Field(ge=0,le=1000)
    minutes:float=Field(ge=0,le=1440,allow_inf_nan=False)
class LegacyIn(BaseModel):
    days:list[LegacyDay]=Field(max_length=3650)


@router.post('/import-legacy')
def import_legacy(body:LegacyIn,db:Session=Depends(get_db)):
    from sqlalchemy import text
    from ..models import AppSetting
    import json
    if sum(max(1,d.count) for d in body.days)>10000 or len({d.date for d in body.days})!=len(body.days): raise HTTPException(422,'历史记录数量超限或日期重复')
    if any(d.date>local_date(utcnow()) for d in body.days): raise HTTPException(422,'历史日期不能在未来')
    db.execute(text('BEGIN IMMEDIATE'))
    imported=days=0
    for day in body.days:
        key='focus_legacy_imported_'+day.date.isoformat();value=json.dumps([day.count,day.minutes]);prior=db.get(AppSetting,key)
        if prior:
            if prior.value!=value: raise HTTPException(409,'该日期已导入其它历史值，请先核对已有专注记录')
            continue
        db.add(AppSetting(key=key,value=value));days+=1
        if not day.minutes and not day.count: continue
        for i in range(max(1,day.count)):
            duration=day.minutes/max(1,day.count)
            db.add(FocusSession(mode='focus',planned_min=max(1,int(duration+.999)),duration_min=duration,completed=day.count>0,started_at=datetime.combine(day.date,datetime.min.time()).astimezone().astimezone(timezone.utc).replace(tzinfo=None)+timedelta(hours=12),note='从本机旧专注统计导入'))
            imported+=1
    db.commit();return {'imported':imported,'days':days}
