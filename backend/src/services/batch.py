from sqlalchemy import select,text
from fastapi import HTTPException
from redis import Redis
from ..config.settings import REDIS_URL
from ..models.batch import Batch
from ..models.sample import Sample
from ..models.quota import SettlementGroup
from .query_engine import validate_sql
from . import quota
queue=Redis.from_url(REDIS_URL)


def _group_for(db,user):
    """Every batch belongs to a settlement group; unassigned users join the
    default group once instead of being blocked."""
    if user.group_id:
        return user.group_id
    group=quota.default_group(db)
    db.execute(text('UPDATE users SET group_id=:g WHERE id=:u'),{'g':group.id,'u':user.id})
    db.commit()
    user.group_id=group.id
    return group.id


def serialize(x,db=None):
    data={'id':x.id,'name':x.name,'sample_id':x.sample_id,'candidate_sql':x.candidate_sql,'status':x.status,'group_id':x.group_id,'created_at':str(x.created_at)}
    if db is not None:
        group=db.get(SettlementGroup,x.group_id) if x.group_id else None
        data['group']=group.name if group else None
        data['quota']=quota.quota_state(db,x.id,x.status)
    return data


def list_batches(db):return [serialize(x,db) for x in db.scalars(select(Batch).order_by(Batch.id.desc()))]
def get_batch(db,batch_id):
    x=db.get(Batch,batch_id)
    if not x:raise HTTPException(404,'批次不存在')
    return serialize(x,db)


def create_batch(db,data,user=None):
    validate_sql(data.candidate_sql)
    if not db.get(Sample,data.sample_id):raise HTTPException(404,'样本不存在')
    if db.scalar(select(Batch).where(Batch.name==data.name)):raise HTTPException(409,'批次名称已存在')
    if len(list(db.scalars(select(Batch).where(Batch.status.in_(['queued','running'])))))>=20:raise HTTPException(429,'待执行任务达到20个，请稍后重试')
    group_id=_group_for(db,user) if user is not None else None
    obj=Batch(**data.model_dump(),group_id=group_id);db.add(obj);db.commit()
    queue.rpush('query-review:jobs',obj.id)
    return serialize(obj,db)


def top_up_batch_quota(db,batch_id,user,amount_ms=None,amount_seconds=None):
    batch=db.get(Batch,batch_id)
    if not batch:raise HTTPException(404,'批次不存在')
    group_id=batch.group_id
    if group_id is None:
        group_id=_group_for(db,user)
        batch.group_id=group_id;db.commit()
    # An operator may only replenish their own group's allowance; an admin
    # manages every group.  Neither can move usage between groups.
    if user.role != 'admin' and user.group_id not in (None, group_id):
        raise HTTPException(403,'只能为本组补充额度')
    if amount_ms is None:
        if amount_seconds is None:raise ValueError('请提供补额数量')
        amount_ms=int(round(amount_seconds*1000))
    view=quota.top_up(db,group_id,amount_ms)
    view['batch_id']=batch_id
    view['group']=db.get(SettlementGroup,group_id).name
    return view


def group_quota(db,user,day=None):
    group_id=user.group_id or _group_for(db,user)
    view=quota.quota_view(db,group_id,day)
    view['group_id']=group_id
    view['group']=db.get(SettlementGroup,group_id).name
    view['batch_ids']=[x for x in db.scalars(select(Batch.id).where(
        Batch.group_id==group_id).order_by(Batch.id.desc()).limit(50))]
    return view
