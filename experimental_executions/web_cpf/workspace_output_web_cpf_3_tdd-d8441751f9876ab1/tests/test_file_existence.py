import os


def test_requirements_exists() -> None:
    assert os.path.isfile("requirements.txt"), "requirements.txt não encontrado"


def test_readme_exists() -> None:
    assert os.path.isfile("README.md"), "README.md não encontrado"


def test_env_example_exists() -> None:
    assert os.path.isfile(".env.example"), ".env.example não encontrado"


def test_mypy_ini_exists() -> None:
    assert os.path.isfile("mypy.ini"), "mypy.ini não encontrado"


def test_flake8_exists() -> None:
    assert os.path.isfile(".flake8"), ".flake8 não encontrado"
