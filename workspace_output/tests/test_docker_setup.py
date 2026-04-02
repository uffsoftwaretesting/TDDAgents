import os
import re
import pytest


def get_base_dir():
    # project root is one level up from tests/
    return os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="module")
def base_dir():
    return get_base_dir()


def test_dockerfile_exists_and_content(base_dir):
    path = os.path.join(base_dir, 'Dockerfile')
    assert os.path.isfile(path), "Dockerfile must exist at project root"
    content = open(path, 'r').read()
    # Check multi-stage keywords
    assert re.search(r"FROM python:3\.?\d*-slim as builder", content, re.IGNORECASE), "Dockerfile should use a slim Python image as builder stage"
    assert re.search(r"FROM python:3\.?\d*-slim", content.strip().splitlines()[-1], re.IGNORECASE), "Dockerfile should use a slim Python image in final stage"
    assert 'COPY --from=builder' in content, "Dockerfile should copy artifacts from builder stage"
    assert re.search(r"pip install.*-r requirements.txt", content), "Dockerfile should install dependencies from requirements.txt"
    assert 'uvicorn' in content, "Dockerfile should run uvicorn to start the API"


def test_docker_compose_yml_exists_and_services(base_dir):
    path = os.path.join(base_dir, 'docker-compose.yml')
    assert os.path.isfile(path), "docker-compose.yml must exist at project root"
    content = open(path, 'r').read()
    # Check for services declaration
    assert 'services:' in content, "docker-compose.yml should define services"
    assert re.search(r"^\s*api:\s*$", content, re.MULTILINE), "Service 'api' must be defined"
    assert re.search(r"^\s*postgres:\s*$", content, re.MULTILINE), "Service 'postgres' must be defined"
    # Check for healthcheck under postgres
    assert 'healthcheck' in content, "Postgres service should include a healthcheck"
    # Check environment variables
    assert 'DATABASE_URL' in content, "docker-compose.yml should set DATABASE_URL environment variable"
    assert 'ENV' in content, "docker-compose.yml should set ENV environment variable"


def test_docker_compose_override_exists_and_mount(base_dir):
    path = os.path.join(base_dir, 'docker-compose.override.yml')
    assert os.path.isfile(path), "docker-compose.override.yml must exist at project root"
    content = open(path, 'r').read()
    # Check for volume mount (source: . to container)
    assert 'volumes:' in content, "Override file should declare volumes"
    assert re.search(r"\.:/.*", content), "Override should mount project directory into container"
    # Check command with auto-reload
    assert 'uvicorn' in content and '--reload' in content, "Override should use uvicorn with --reload for development"


def test_dockerignore_exists(base_dir):
    path = os.path.join(base_dir, '.dockerignore')
    assert os.path.isfile(path), ".dockerignore must exist at project root"
    content = open(path, 'r').read()
    # Typical exclusions
    assert '__pycache__' in content, ".dockerignore should exclude __pycache__"
    assert 'tests/' in content or 'tests' in content, ".dockerignore should exclude test directories"


def test_makefile_contains_test_docker(base_dir):
    path = os.path.join(base_dir, 'Makefile')
    assert os.path.isfile(path), "Makefile must exist at project root"
    content = open(path, 'r').read()
    # Check for test-docker target
    assert re.search(r"^test-docker:\s*$", content, re.MULTILINE), "Makefile should define a 'test-docker' target"
    # Ensure this target uses docker-compose and pytest
    # Find lines under test-docker
    lines = content.splitlines()
    in_target = False
    target_lines = []
    for line in lines:
        if re.match(r"^test-docker:\s*$", line):
            in_target = True
            continue
        if in_target:
            if re.match(r"^[^ \t]", line):
                break
            target_lines.append(line.strip())
    assert any('docker-compose' in l for l in target_lines), "test-docker target should invoke docker-compose commands"
    assert any('pytest' in l for l in target_lines), "test-docker target should run pytest inside the containers or host"