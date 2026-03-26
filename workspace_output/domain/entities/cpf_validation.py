from datetime import datetime, timedelta
from .cpf import CPF

class CPFValidation:
    def __init__(self, cpf: CPF, valid: bool, timestamp: datetime, id: int = None):
        # Validate id type
        if id is not None and not isinstance(id, int):
            raise TypeError("id must be int or None")
        # Validate cpf type
        if not isinstance(cpf, CPF):
            raise TypeError("cpf must be CPF instance")
        # Validate valid type
        if not isinstance(valid, bool):
            raise TypeError("valid must be bool")
        # Validate timestamp type
        if not isinstance(timestamp, datetime):
            raise TypeError("timestamp must be datetime")
        # Must be timezone-aware
        if timestamp.tzinfo is None:
            raise TypeError("timestamp must be timezone-aware UTC datetime")
        # Must be UTC
        if timestamp.tzinfo.utcoffset(timestamp) != timedelta(0):
            raise ValueError("timestamp must be in UTC")
        # Assign
        self.id = id
        self.cpf = cpf
        self.valid = valid
        self.timestamp = timestamp
