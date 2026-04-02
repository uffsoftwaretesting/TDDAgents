from dataclasses import dataclass
from uuid import UUID
from datetime import datetime

@dataclass
class CPFValidation:
    id: UUID
    cpf: str
    is_valid: bool
    created_at: datetime
