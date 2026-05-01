from pathlib import Path

def test_mypy_ini_exists():
    assert Path('mypy.ini').is_file(), "mypy.ini file should exist in project root"

def test_pytest_ini_exists():
    assert Path('pytest.ini').is_file(), "pytest.ini file should exist in project root"

def test_ruff_toml_exists():
    assert Path('.ruff.toml').is_file(), ".ruff.toml file should exist in project root"

def test_mypy_ini_has_mypy_section():
    content = Path('mypy.ini').read_text()
    assert '[mypy]' in content, "mypy.ini must contain [mypy] section"

def test_pytest_ini_has_pytest_section():
    content = Path('pytest.ini').read_text()
    assert '[pytest]' in content, "pytest.ini must contain [pytest] section"

def test_ruff_toml_has_tool_section():
    content = Path('.ruff.toml').read_text()
    assert '[tool.ruff]' in content, ".ruff.toml must contain [tool.ruff] section"
