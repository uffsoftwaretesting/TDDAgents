import pytest
import pathlib


def test_github_ci_file_exists():
    """
    Verifica que o arquivo de configuração do GitHub Actions para CI exista.
    """
    project_root = pathlib.Path(__file__).parent.parent
    ci_file = project_root / ".github" / "workflows" / "ci.yml"
    assert ci_file.exists(), f"CI config file should exist at {ci_file}"


def test_ci_file_runs_pytest_and_flake8():
    """
    Verifica que o CI config inclua comandos para rodar pytest e flake8.
    """
    project_root = pathlib.Path(__file__).parent.parent
    ci_file = project_root / ".github" / "workflows" / "ci.yml"
    content = ci_file.read_text(encoding="utf-8").lower()
    assert "pytest" in content, "CI workflow must include pytest execution"
    assert "flake8" in content, "CI workflow must include flake8 linting"


def test_flake8_config_exists():
    """
    Verifica que exista arquivo de configuração do flake8.
    """
    project_root = pathlib.Path(__file__).parent.parent
    # common flake8 config files
    candidates = [project_root / ".flake8", project_root / "setup.cfg", project_root / "tox.ini"]
    found = False
    for cfg in candidates:
        if cfg.exists():
            text = cfg.read_text(encoding="utf-8").lower()
            if "flake8" in text or cfg.name == ".flake8":
                found = True
                break
    assert found, "A flake8 configuration file (.flake8, setup.cfg or tox.ini) must exist and include a [flake8] section"