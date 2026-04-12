import shutil
import subprocess
import time
import requests
import pytest


def test_docker_compose_build_and_smoke():
    """
    Smoke test para containerização:
    1. Constrói a imagem via plugin Docker Compose (`docker compose build`)
    2. Sobe o serviço em background (`docker compose up -d`)
    3. Aguarda disponibilidade e faz POST em /validate-cpf
    4. Verifica HTTP 200
    5. Destrói o ambiente (`docker compose down`)
    """
    # Pula o teste se não houver CLI Docker nem docker-compose
    if not shutil.which('docker') and not shutil.which('docker-compose'):
        pytest.skip('Docker CLI não encontrado; pulando smoke test de container')

    # Build da imagem
    subprocess.run(["docker", "compose", "build"], check=True)
    # Sobe o serviço em detached mode
    subprocess.run(["docker", "compose", "up", "-d"], check=True)
    try:
        # Aguarda alguns segundos para o container iniciar
        time.sleep(5)
        # Requisição mínima para obter 200 (payload válido mesmo que cpf vazio)
        response = requests.post(
            "http://localhost:8000/validate-cpf",
            json={"cpf": ""},
            timeout=10
        )
        assert response.status_code == 200, (
            f"Esperado status 200, mas recebeu {response.status_code}: {response.text}"
        )
    finally:
        # Desmonta o serviço
        subprocess.run(["docker", "compose", "down"], check=True)
