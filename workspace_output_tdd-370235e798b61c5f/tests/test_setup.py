import os
import pytest

def test_src_taylor2_directory_exists():
    assert os.path.isdir("src/taylor_2"), \
        "Diretório 'src/taylor_2' deve existir"
    init_file = os.path.join("src/taylor_2", "__init__.py")
    assert os.path.isfile(init_file), \
        "Arquivo '__init__.py' deve existir dentro de 'src/taylor_2' para tornar o pacote importável"


def test_pyproject_toml_has_sections():
    pyproject = "pyproject.toml"
    assert os.path.isfile(pyproject), f"Arquivo '{pyproject}' deve existir"
    content = open(pyproject, "r", encoding="utf-8").read()
    for section in ["[tool.black]", "[tool.flake8]", "[tool.mypy]"]:
        assert section in content, f"Seção '{section}' deve estar presente em '{pyproject}'"


def test_setup_or_tox_exists():
    setup_cfg = "setup.cfg"
    tox_ini = "tox.ini"
    assert os.path.isfile(setup_cfg) or os.path.isfile(tox_ini), \
        "Deve existir pelo menos um dos arquivos de configuração: 'setup.cfg' ou 'tox.ini'"