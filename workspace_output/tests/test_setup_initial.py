import os

def get_base_dir():
    # project root is one level up from tests/
    return os.path.dirname(os.path.dirname(__file__))


def test_directories_exist():
    base = get_base_dir()
    dirs = [
        'src',
        os.path.join('src', 'domain'),
        os.path.join('src', 'interfaces'),
        os.path.join('src', 'usecases'),
        os.path.join('src', 'adapters'),
        os.path.join('src', 'schemas'),
        os.path.join('src', 'api'),
        os.path.join('src', 'infra'),
        'tests',
        'docs',
    ]
    for d in dirs:
        path = os.path.join(base, d)
        assert os.path.isdir(path), f"Directory '{d}' should exist at {path}"


def test_requirements_contains_base_dependencies():
    base = get_base_dir()
    req_file = os.path.join(base, 'requirements.txt')
    assert os.path.isfile(req_file), "requirements.txt should exist"
    with open(req_file, 'r') as f:
        lines = f.read().splitlines()

    base_deps = [
        'fastapi',
        'uvicorn',
        'validate-docbr',
        'sqlalchemy[asyncio]',
        'asyncpg',
        'alembic',
        'pydantic',
        'python-dotenv',
        'pytest',
        'pytest-asyncio',
        'httpx',
        'ruff',
        'black',
        'isort',
        'mypy',
        'bandit',
        'pytest-cov',
    ]
    for dep in base_deps:
        assert any(dep in line for line in lines), f"Dependency '{dep}' should be listed in requirements.txt"


def test_pre_commit_configured():
    base = get_base_dir()
    config_file = os.path.join(base, '.pre-commit-config.yaml')
    assert os.path.isfile(config_file), ".pre-commit-config.yaml should exist"
    content = open(config_file, 'r').read()
    for hook in ['black', 'isort', 'ruff']:
        assert hook in content, f"Hook '{hook}' should be configured in .pre-commit-config.yaml"


def test_mypy_strict_config():
    base = get_base_dir()
    ini_path = os.path.join(base, 'mypy.ini')
    toml_path = os.path.join(base, 'pyproject.toml')
    assert os.path.isfile(ini_path) or os.path.isfile(toml_path), "A mypy configuration (mypy.ini or pyproject.toml) must exist"
    if os.path.isfile(ini_path):
        content = open(ini_path, 'r').read()
    else:
        content = open(toml_path, 'r').read()
    # Check that strict mode is enabled
    assert (
        'strict = True' in content
        or 'strict = true' in content
        or 'strict=' in content
    ), 'mypy must be configured in strict mode (strict=True)'
