from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,Field
from ..config.database import get_db
from ..schemas.batch import BatchInput
from ..services.auth import current_user,writer
from ..services.batch import list_batches,get_batch,create_batch,top_up_batch_quota,group_quota
router=APIRouter(prefix='/batches')

class TopUp(BaseModel):
    amount_ms:int|None=Field(default=None,gt=0)
    amount_seconds:float|None=Field(default=None,gt=0,max=600)

@router.get('')
def listing(db=Depends(get_db),user=Depends(current_user)):return list_batches(db)
@router.get('/quota')
def my_quota(day:str|None=Query(default=None),db=Depends(get_db),user=Depends(current_user)):
    return group_quota(db,user,day)
@router.get('/{batch_id}')
def detail(batch_id:int,db=Depends(get_db),user=Depends(current_user)):return get_batch(db,batch_id)
@router.post('',status_code=202)
def create(data:BatchInput,db=Depends(get_db),user=Depends(writer)):return create_batch(db,data,user)
@router.post('/{batch_id}/quota/topup')
def topup(batch_id:int,data:TopUp,db=Depends(get_db),user=Depends(writer)):
    return top_up_batch_quota(db,batch_id,user,amount_ms=data.amount_ms,amount_seconds=data.amount_seconds)
