import importlib
import pytest
from pathlib import Path

# Lista de módulos que devem existir sob src/
MODULE_PATHS = [
    'domain.entities',
    'domain.ports',
    'application.ports',
    'application.use_cases',
    'infrastructure.cpf_validator',
    'interfaces.api.models',
    'interfaces.api.routes',
    'config',
]

@pytest.mark.parametrize('module_path', MODULE_PATHS)
def test_module_importable(module_path):
    """
    Testa se o módulo src.<module_path> é importável, garantindo diretórios e __init__.py.
    """
    full_module = f"src.{module_path}"
    try:
        importlib.import_module(full_module)
    except ModuleNotFoundError as e:
        pytest.fail(f"Módulo {full_module} não encontrado: {e}")

@pytest.mark.parametrize('package_path', MODULE_PATHS)
def test_init_py_exists(package_path):
    """
    Verifica existência de __init__.py em cada pacote src/<package_path>.
    """
    # Converte package_path para caminho de diretório
    dir_path = Path(__file__).resolve().parent.parent / 'src' / Path(package_path.replace('.', '/'))
    init_file = dir_path / '__init__.py'
    assert init_file.exists(), f"Arquivo __init__.py não encontrado em {init_file}"