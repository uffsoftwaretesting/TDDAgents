# Dockerfile mínimo para executar testes com pytest
FROM python:3.9-slim

WORKDIR /app

# Instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do projeto
COPY . .

# Comando padrão para rodar testes
CMD ["pytest", "--maxfail=1", "--disable-warnings", "-q"]