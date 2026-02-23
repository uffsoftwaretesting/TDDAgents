from pydantic import BaseModel, Field

class FileToWrite(BaseModel):
    filepath: str = Field(
        description="O caminho relativo para o arquivo, ex: 'app/main.py', 'app/models/models.py' ou 'tests/unit_tests/test_api.py'. Diretórios são criados automaticamente."
    )
    content: str = Field(
        description="O código COMPLETO e totalmente atualizado para este arquivo. NUNCA use reticências (...) ou placeholders. Você deve gerar o arquivo inteiro."
    )

class AgentAction(BaseModel):
    thoughts: str = Field(
        description="Seu raciocínio passo a passo sobre a arquitetura, separação de componentes e como fazer os testes passarem."
    )
    dependencies: list[str] = Field(
        default_factory=list, 
        description="Quaisquer pacotes do pip necessários (ex: ['fastapi', 'sqlalchemy', 'pytest-asyncio', 'httpx'])."
    )
    bash_commands: list[str] = Field(
        default_factory=list, 
        description="Comandos de terminal para executar DEPOIS de escrever os arquivos, mas ANTES dos testes (ex: ['alembic upgrade head', 'export DATABASE_URL=...']). NÃO coloque o comando do pytest aqui."
    )
    files_to_write: list[FileToWrite] = Field(
        description="Os arquivos que precisam ser criados ou modificados para implementar os componentes do framework (Router, Schema, Model, CRUD, etc.)."
    )