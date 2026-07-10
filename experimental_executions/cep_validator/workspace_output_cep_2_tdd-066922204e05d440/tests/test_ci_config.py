import pathlib
import pytest
import toml


def test_pyproject_exists():
    """
    Verifica que o arquivo de configuração 'pyproject.toml' exista na raiz do projeto.
    """
    path = pathlib.Path('pyproject.toml')
    assert path.exists(), "Arquivo 'pyproject.toml' está ausente."


def test_pyproject_black_config():
    """
    Carrega o toml e checa se a seção [tool.black] está presente com pelo menos uma chave essencial.
    """
    data = toml.load('pyproject.toml')
    assert 'tool' in data and 'black' in data['tool'], "Configuração do Black não encontrada em [tool.black]."
    black_cfg = data['tool']['black']
    assert isinstance(black_cfg, dict), "Seção [tool.black] deve ser um dicionário."  
    assert 'line-length' in black_cfg, "Chave 'line-length' ausente em [tool.black]."


def test_pyproject_flake8_config():
    """
    Carrega o toml e checa se a seção [tool.flake8] está presente com pelo menos uma chave essencial.
    """
    data = toml.load('pyproject.toml')
    assert 'tool' in data and 'flake8' in data['tool'], "Configuração do Flake8 não encontrada em [tool.flake8]."
    flake8_cfg = data['tool']['flake8']
    assert isinstance(flake8_cfg, dict), "Seção [tool.flake8] deve ser um dicionário."
    assert 'max-line-length' in flake8_cfg, "Chave 'max-line-length' ausente em [tool.flake8]."


def test_readme_has_format_and_lint_scripts():
    """
    Verifica que o README.md contenha instruções/scripts para formatação e lint.
    """
    readme_path = pathlib.Path('README.md')
    assert readme_path.exists(), "Arquivo README.md está ausente."
    content = readme_path.read_text(encoding='utf-8')
    assert 'black .' in content, "Comando 'black .' não encontrado no README.md para formatação automática."
    assert 'flake8 .' in content, "Comando 'flake8 .' não encontrado no README.md para linting."
