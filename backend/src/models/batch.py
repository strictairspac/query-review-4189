from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,Text,ForeignKey,DateTime,func
from datetime import datetime
from ..config.database import Base
class Batch(Base):
    __tablename__='batches'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(120),unique=True)
    sample_id:Mapped[int]=mapped_column(ForeignKey('samples.id'))
    candidate_sql:Mapped[str]=mapped_column(Text)
    status:Mapped[str]=mapped_column(String(20),default='queued')
    group_id:Mapped[int|None]=mapped_column(ForeignKey('groups.id'),nullable=True,index=True)
    lease_owner:Mapped[str|None]=mapped_column(String(80),nullable=True)
    lease_until:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
