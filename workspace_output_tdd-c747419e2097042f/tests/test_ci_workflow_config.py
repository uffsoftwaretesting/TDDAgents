import pytest
from pathlib import Path

def test_ci_workflow_file_exists():
    """
    The GitHub Actions CI workflow should exist at .github/workflows/ci.yml
    """
    path = Path('.github/workflows/ci.yml')
    assert path.is_file(), ".github/workflows/ci.yml file should exist"


def test_ci_workflow_triggers_on_push_and_pull_request():
    """
    The workflow should be triggered on push and pull_request for main branch
    """
    content = Path('.github/workflows/ci.yml').read_text()
    assert 'push:' in content, "Workflow must trigger on push"
    assert 'pull_request:' in content, "Workflow must trigger on pull_request"
    assert 'branches:' in content, "Workflow must specify branches"
    assert 'main' in content, "Workflow should target 'main' branch"


def test_ci_workflow_jobs_defined():
    """
    Workflow must define lint, type-check, test, and build jobs
    """
    content = Path('.github/workflows/ci.yml').read_text()
    jobs_section = content.split('jobs:')[1]
    assert 'lint:' in jobs_section, "lint job must be defined"
    assert 'type-check:' in jobs_section or 'type-check' in jobs_section, "type-check job must be defined"
    assert 'test:' in jobs_section, "test job must be defined"
    assert 'build:' in jobs_section, "build job must be defined"


def test_ci_workflow_contains_docker_build_step():
    """
    The build job should run a docker build command
    """
    content = Path('.github/workflows/ci.yml').read_text()
    # locate build job
    build_index = content.find('build:')
    assert build_index != -1, "build job not found"
    build_section = content[build_index:]
    assert 'docker build' in build_section, "build job must include 'docker build' step"