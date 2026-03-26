from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class CPFValidation(Base):
    __tablename__ = 'cpf_validation'

    id = Column(Integer, primary_key=True)
    cpf = Column(String(11), nullable=False)
    valid = Column(Boolean, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
