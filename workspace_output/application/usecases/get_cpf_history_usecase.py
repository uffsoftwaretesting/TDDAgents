class ValidationEntryDTO:
    def __init__(self, timestamp, valid):
        self.timestamp = timestamp
        self.valid = valid

class CPFHistoryDTO:
    def __init__(self, cpf: str, results: list[ValidationEntryDTO]):
        self.cpf = cpf
        self.results = results

class GetCPFHistoryUseCase:
    def __init__(self, repo):
        self.repo = repo

    async def execute(self, cpf: str) -> CPFHistoryDTO:
        # Retrieve all validations for the given CPF
        records = await self.repo.get_by_cpf(cpf)
        # Sort by timestamp ascending
        sorted_records = sorted(records, key=lambda rec: rec.timestamp)
        # Map to DTO entries
        entries = [ValidationEntryDTO(timestamp=rec.timestamp, valid=rec.valid) for rec in sorted_records]
        return CPFHistoryDTO(cpf=cpf, results=entries)
