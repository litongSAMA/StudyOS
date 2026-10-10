"""Learning reports reconstructed from retained actual progress, with bounded history."""
from datetime import timedelta
from fastapi import APIRouter,Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.infra.db import get_db
from app.infra.models import Course,Material,MaterialReview,Task,WrongQuestion
from app.infra.timeutil import iso_utc,utcnow,local_date,parse_iso

router=APIRouter(prefix='/api/report',tags=['report'])


def report_data(db):
    courses=list(db.scalars(select(Course)));materials=list(db.scalars(select(Material)));states={row.material_id:row for row in db.scalars(select(MaterialReview))};tasks=list(db.scalars(select(Task)));wrong=list(db.scalars(select(WrongQuestion)))
    def view(semester,label,selected):
        cids={c.id for c in selected};all_data=semester is None
        mats=[m for m in materials if all_data or m.course_id in cids];ts=[t for t in tasks if all_data or t.course_id in cids];ws=[w for w in wrong if all_data or w.course_id in cids]
        reviewed=sum(bool(states.get(m.id) and states[m.id].history) for m in mats);mastered=sum(bool(states.get(m.id) and states[m.id].mastery>=.85) for m in mats)
        mastery=sum(states[m.id].mastery if m.id in states else 0 for m in mats)/len(mats) if mats else 0
        reviews=sum(len(states[m.id].history or []) for m in mats if m.id in states);done=sum(w.status=='done' for w in ws);tasks_done=sum(t.done for t in ts)
        stats={'courses':len(selected),'materials':len(mats),'materials_reviewed':reviewed,'materials_mastered':mastered,'mastery':mastery,'reviews':reviews,'wrong_total':len(ws),'wrong_done':done,'wrong_rate':100*done/len(ws) if ws else 0,'tasks_total':len(ts),'tasks_done':tasks_done,'tasks_rate':100*tasks_done/len(ts) if ts else 0}
        history=[]
        for m in mats:
            for item in states[m.id].history if m.id in states else []:
                try: history.append((parse_iso(item['at']),m.id,float(item.get('mastery',0))))
                except (ValueError,KeyError,TypeError): continue
        dates=[local_date(row.created_at) for row in mats+ts+ws]+[local_date(item[0]) for item in history]
        series=[];today=local_date(utcnow())
        if dates:
            start=min(dates);start-=timedelta(days=start.weekday());lower=today-timedelta(weeks=104);lower-=timedelta(days=lower.weekday());start=max(start,lower);current=start;cum=conquered=0;last={}
            while current<=today:
                end=current+timedelta(days=7);events=sorted([r for r in history if current<=local_date(r[0])<end])
                for _,mid,value in events: last[mid]=value
                cum+=len(events);new=sum(current<=local_date(w.created_at)<end for w in ws);won=sum(w.status=='done' and current<=local_date(w.updated_at)<end for w in ws);conquered+=won
                series.append({'date':current.isoformat(),'mastery':sum(last.values())/len(mats) if mats else 0,'reviews':len(events),'reviews_cum':cum,'new_wrong':new,'conquered':won,'conquered_cum':conquered,'wrong_rate':100*conquered/len(ws) if ws else 0,'wrong_total':len(ws)})
                current=end
        return {'semester':semester or '', 'label':label,'course_names':[c.name for c in selected],'stats':stats,'series':series,'span_from':min(dates).isoformat() if dates else '', 'span_to':today.isoformat() if dates else ''}
    semesters=sorted({c.semester or '' for c in courses})
    return {'now':iso_utc(utcnow()),'semesters':[view(sem,sem or '未分学期',[c for c in courses if (c.semester or '')==sem]) for sem in semesters],'overview':view(None,'全部学期',courses),'history_note':'趋势依据保留的评分历史（每份资料最近 20 次），复习总次数不等同于全部历史次数。'}


@router.get('')
def get(db:Session=Depends(get_db)): return report_data(db)
@router.get('/md')
def markdown(db:Session=Depends(get_db)):
    data=report_data(db);lines=['# StudyOS 学习报告',data['now'],data['history_note']]
    for item in [data['overview']]+data['semesters']:
        stats=item['stats'];lines += ['## '+item['label'],f"资料 {stats['materials']} 份，平均掌握度 {stats['mastery']:.0%}，任务完成 {stats['tasks_done']}/{stats['tasks_total']}，攻克错题 {stats['wrong_done']}/{stats['wrong_total']}。"]
    return PlainTextResponse('\n\n'.join(lines),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename="studyos-report.md"'})
