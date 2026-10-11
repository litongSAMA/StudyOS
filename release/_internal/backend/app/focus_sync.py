"""UUID-based focus progress transport; account/session credentials remain local."""
from sqlalchemy import select
from .models import FocusSession, Course, Task, Material, ReviewPlan
from .timeutil import iso_utc

FIELDS=['mode','planned_min','duration_min','completed','interrupt_count','plan_date','note']


def maps(db):
    return {name:{row.sync_uuid:row.id for row in db.scalars(select(cls))} for name,cls in [('course',Course),('task',Task),('material',Material),('plan',ReviewPlan)]}


def export_focus(db,local_ids=False):
    ids={key:{rid:uid for uid,rid in values.items()} for key,values in maps(db).items()}
    result=[]
    for row in db.scalars(select(FocusSession)):
        value={key:getattr(row,key) for key in FIELDS}
        value.update(sync_uuid=row.sync_uuid,created_at=iso_utc(row.created_at),updated_at=iso_utc(row.updated_at),started_at=iso_utc(row.started_at),material_uuids=[ids['material'][mid] for mid in row.material_ids if mid in ids['material']])
        for name in ['course','task','plan']: value[name+'_uuid']=ids[name].get(getattr(row,name+'_id'))
        if local_ids: value['id']=row.id
        result.append(value)
    return result


def merge_focus(db,items,tomb_map,stats):
    from .sync import SyncError, _parse_dt, _tombstone_newer
    from .routers.focus import FocusIn
    if not isinstance(items,list) or len(items)>10000: raise SyncError('专注记录同步格式或数量无效')
    ids=maps(db);local={row.sync_uuid:row for row in db.scalars(select(FocusSession))}
    for item in items:
        if not isinstance(item,dict) or not isinstance(item.get('sync_uuid'),str) or not 1<=len(item['sync_uuid'])<=60: raise SyncError('专注记录编号无效')
        updated=_parse_dt(item.get('updated_at'));uid=item['sync_uuid'];row=local.get(uid)
        if _tombstone_newer(tomb_map,'focussession',uid,updated) or row and row.updated_at>=updated:
            stats['skipped']+=1;continue
        try:
            values={key:item.get(key) for key in FIELDS}
            values['started_at']=item.get('started_at')
            values['material_ids']=[ids['material'][uid] for uid in item.get('material_uuids',[]) if uid in ids['material']]
            for name in ['course','task','plan']: values[name+'_id']=ids[name].get(item.get(name+'_uuid'))
            parsed=FocusIn.model_validate(values)
        except (ValueError,TypeError,KeyError): raise SyncError('专注记录内容无效') from None
        if row is None:
            row=FocusSession(sync_uuid=uid);db.add(row);local[uid]=row;stats['created']+=1
        else: stats['updated']+=1
        for key,value in parsed.model_dump().items(): setattr(row,key,value)
        row.updated_at=updated;row.created_at=_parse_dt(item.get('created_at'))
        db.flush()
