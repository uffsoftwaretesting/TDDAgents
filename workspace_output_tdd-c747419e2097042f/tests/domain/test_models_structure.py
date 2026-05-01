import importlib
from pathlib import Path

def test_models_directory_exists():
    assert Path('src/domain/models').is_dir(), "src/domain/models directory should exist"


def test_module_cpf_importable():
    module = importlib.import_module('src.domain.models.cpf')
    assert hasattr(module, 'CPF'), "CPF class should be defined in src.domain.models.cpf"
    assert hasattr(module, 'CpfInvalidError'), "CpfInvalidError should be defined in src.domain.models.cpf"