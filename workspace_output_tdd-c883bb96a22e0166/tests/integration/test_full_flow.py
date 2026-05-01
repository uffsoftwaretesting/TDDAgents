import pytest
from fastapi.testclient import TestClient
from src.config.app import create_app

@ pytest.fixture(scope="module")
def client():
    """
    Fixture que retorna um TestClient para a aplicação.
    """
    app = create_app()
    return TestClient(app)

@ pytest.mark.parametrize(
    "cpf_input", [
        # CPFs válidos (mascarados e sem máscara)
        "529.982.247-25",
        "52998224725",
        "168.995.350-09",
        "16899535009",
    ]
)
def test_full_flow_valid_cpfs(client, cpf_input):
    """
    Testa fluxo completo retornando valid=True para CPFs válidos.
    """
    response = client.post("/validate-cpf", json={"cpf": cpf_input})
    assert response.status_code == 200
    assert response.json() == {"valid": True}

@ pytest.mark.parametrize(
    "cpf_input", [
        # CPFs inválidos (sequência uniforme ou dígitos verificadores incorretos)
        "111.111.111-11",
        "11111111111",
        "529.982.247-24",
        "168.995.350-08",
    ]
)
def test_full_flow_invalid_cpfs(client, cpf_input):
    """
    Testa fluxo completo retornando valid=False para CPFs inválidos.
    """
    response = client.post("/validate-cpf", json={"cpf": cpf_input})
    assert response.status_code == 200
    assert response.json() == {"valid": False}