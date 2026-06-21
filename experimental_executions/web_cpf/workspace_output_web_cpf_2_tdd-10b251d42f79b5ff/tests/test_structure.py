import importlib.util
import pytest

@pytest.mark.parametrize("package_name", [
    "domain",
    "application",
    "application.ports",
    "application.use_cases",
    "infrastructure",
    "interfaces"
])
def test_package_exists(package_name):
    """
    Garante que o pacote exista e seja importável.
    Falhará (RED) até que o diretório e __init__.py sejam criados.
    """
    spec = importlib.util.find_spec(package_name)
    assert spec is not None, f"Pacote '{package_name}' deve existir e ser importável"