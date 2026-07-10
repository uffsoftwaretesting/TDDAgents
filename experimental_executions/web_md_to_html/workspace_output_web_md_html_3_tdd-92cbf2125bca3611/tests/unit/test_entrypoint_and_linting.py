import os
import inspect
import pytest
import yaml

# Import the FastAPI application module to detect uvicorn entrypoint
import app.main as main_module


def test_main_imports_uvicorn_and_uvicorn_run_call():
    """
    The main module should import uvicorn and call uvicorn.run() with the app.
    """
    source = inspect.getsource(main_module)
    assert 'import uvicorn' in source, \
        "main.py must import uvicorn"
    assert 'uvicorn.run' in source, \
        "main.py must call uvicorn.run(...) in the entrypoint block"


def test_flake8_config_exists():
    """
    A .flake8 configuration file must be present at the project root.
    """
    assert os.path.isfile('.flake8'), \
        ".flake8 configuration file is missing"


def test_precommit_config_exists():
    """
    A .pre-commit-config.yaml file must be present at the project root.
    """
    assert os.path.isfile('.pre-commit-config.yaml'), \
        ".pre-commit-config.yaml is missing"


def test_precommit_includes_black_and_flake8_hooks():
    """
    The pre-commit configuration should define Black and Flake8 hooks.
    """
    with open('.pre-commit-config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    repos = config.get('repos', [])
    assert repos, "pre-commit-config.yaml must define at least one repo"

    # Collect all hook ids across all repos
    hook_ids = []
    for repo in repos:
        hooks = repo.get('hooks', []) or []
        for hook in hooks:
            hook_id = hook.get('id')
            if hook_id:
                hook_ids.append(hook_id)

    assert 'black' in hook_ids, \
        "pre-commit-config.yaml must include the 'black' hook"
    assert 'flake8' in hook_ids, \
        "pre-commit-config.yaml must include the 'flake8' hook"