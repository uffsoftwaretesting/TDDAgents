class ValidationRecordDTO:
    def __init__(self, id: int, cpf: str, valid: bool, timestamp):
        self.id = id
        self.cpf = cpf
        self.valid = valid
        self.timestamp = timestamp

class PaginatedValidationsDTO:
    def __init__(self, items: list[ValidationRecordDTO], page: int, size: int, total: int):
        self.items = items
        self.page = page
        self.size = size
        self.total = total

class ListValidationsUseCase:
    def __init__(self, repo):
        self.repo = repo

    async def execute(self, page: int, size: int) -> PaginatedValidationsDTO:
        # Validate page and size
        if page < 1:
            raise ValueError("page must be >= 1")
        if size < 1:
            raise ValueError("size must be >= 1")
        offset = (page - 1) * size
        items, total = await self.repo.list(offset, size)
        # Map domain entities to DTOs
        mapped = []
        for rec in items:
            mapped.append(
                ValidationRecordDTO(
                    id=rec.id,
                    cpf=rec.cpf.value,
                    valid=rec.valid,
                    timestamp=rec.timestamp
                )
            )
        return PaginatedValidationsDTO(items=mapped, page=page, size=size, total=total)
