import os
import re
import pytest


def get_base_dir():
    # project root is one level up from tests/
    return os.path.dirname(os.path.dirname(__file__))


def test_ci_workflow_file_exists():
    base = get_base_dir()
    workflow_path = os.path.join(base, '.github', 'workflows', 'ci.yml')
    assert os.path.isfile(workflow_path), f"GitHub Actions workflow file must exist at {workflow_path}"


def test_ci_workflow_includes_required_steps():
    base = get_base_dir()
    workflow_path = os.path.join(base, '.github', 'workflows', 'ci.yml')
    content = open(workflow_path, 'r').read()
    # Required GitHub Actions steps
    assert 'uses: actions/checkout@' in content, "Workflow must include actions/checkout"
    assert 'uses: actions/setup-python@' in content, "Workflow must include actions/setup-python"
    assert 'uses: actions/cache@' in content, "Workflow must include actions/cache for pip or dependencies"
    # Install dependencies
    assert re.search(r'pip install.*-r\s*requirements\.txt', content), "Workflow must install dependencies via 'pip install -r requirements.txt'"
    # Pre-commit hooks
    assert 'pre-commit' in content, "Workflow must run pre-commit (black/isort/ruff)"
    # Type checking
    assert 'mypy' in content, "Workflow must run mypy"
    # Tests with coverage threshold
    assert 'pytest' in content and 'cov' in content, "Workflow must run pytest with coverage"
    # Ensure coverage failure threshold is set to at least 90%
    assert (
        '--cov-fail-under=90' in content
        or '--cov-fail-under 90' in content
        or 'fail-under=90' in content
    ), "Workflow must enforce coverage minimum of 90% via --cov-fail-under"
    # Security scan
    assert 'bandit' in content, "Workflow must run bandit security checks"


def test_readme_contains_ci_badge():
    base = get_base_dir()
    readme_path = os.path.join(base, 'README.md')
    assert os.path.isfile(readme_path), "README.md must exist at project root"
    content = open(readme_path, 'r').read()
    # Look for GitHub Actions badge referring to our ci.yml workflow
    assert 'actions/workflows/ci.yml/badge.svg' in content, \
        "README.md must include a badge for the CI workflow (.github/workflows/ci.yml/badge.svg)"