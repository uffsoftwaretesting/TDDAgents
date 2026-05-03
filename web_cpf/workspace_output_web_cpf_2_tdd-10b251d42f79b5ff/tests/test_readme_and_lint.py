import pytest
from pathlib import Path


def test_readme_exists():
    readme = Path("README.md")
    assert readme.exists(), "README.md deve existir e documentar comandos de teste e lint"


def test_readme_contains_test_and_lint_commands():
    content = Path("README.md").read_text(encoding="utf-8")
    assert "pytest" in content, "README deve documentar o comando 'pytest'"
    # Verifica cobertura
    assert "cov" in content or "coverage" in content, "README deve mencionar pytest-cov ou coverage"
    assert "flake8" in content, "README deve documentar o comando 'flake8' para linting"


def test_flake8_config_exists():
    cfg = Path(".flake8")
    assert cfg.exists(), ".flake8 deve existir para configuração do lint"


def test_flake8_basic_rules():
    content = Path(".flake8").read_text(encoding="utf-8")
    # Regra mínima: definir limitação de tamanho de linha
    assert "max-line-length" in content or "max_line_length" in content, ".flake8 deve especificar 'max-line-length' ou similar"