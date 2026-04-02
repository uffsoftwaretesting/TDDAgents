from sqlalchemy import Column, String, Boolean
from sqlalchemy.types import TypeDecorator, DateTime as SADateTime
from datetime import timezone
from src.infra.database import Base


class UTCDateTime(TypeDecorator):
    """
    TypeDecorator to store naive UTC datetime and re-attach UTC tzinfo on retrieval.
    """
    impl = SADateTime

    def process_bind_param(self, value, dialect):
        if value is not None:
            # ensure UTC and drop tzinfo for storage
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return None

    def process_result_value(self, value, dialect):
        if value is not None:
            # re-attach UTC tzinfo
            return value.replace(tzinfo=timezone.utc)
        return None


class CPFValidationModel(Base):
    __tablename__ = 'cpf_validations'

    id = Column(String(length=36), primary_key=True)
    cpf = Column(String(length=11), nullable=False)
    is_valid = Column(Boolean, nullable=False)
    created_at = Column(UTCDateTime(timezone=True), nullable=False)
