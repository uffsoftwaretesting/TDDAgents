import pytest
from pathlib import Path
import yaml


def test_dockerfile_exists():
    assert Path('Dockerfile').is_file(), "Dockerfile should exist in project root"


def test_dockerfile_multi_stage_build():
    content = Path('Dockerfile').read_text()
    # Check presence of at least two stages with aliases
    assert 'AS builder' in content, "Dockerfile must have a builder stage with alias"
    assert ('AS runtime' in content) or ('AS production' in content), \
        "Dockerfile must have a runtime/production stage with alias"


def test_dockerfile_dev_stage_hot_reload():
    content = Path('Dockerfile').read_text()
    # Ensure uvicorn --reload appears for hot-reload in dev stage
    assert '--reload' in content, "Dockerfile dev stage should enable hot-reload with uvicorn --reload"


def test_dockerfile_prod_optimizes_layers():
    lines = Path('Dockerfile').read_text().splitlines()
    # Production stage should install dependencies in a separate layer
    has_requirements_install = any('pip install' in line.lower() for line in lines)
    assert has_requirements_install, \
        "Dockerfile prod stage should install dependencies separately to optimize layers"


def test_docker_compose_dev_exists():
    assert Path('docker-compose.dev.yml').is_file(), "docker-compose.dev.yml should exist"

@pytest.fixture

def compose():
    path = Path('docker-compose.dev.yml')
    return yaml.safe_load(path.read_text())


def test_docker_compose_dev_services(compose):
    assert 'services' in compose, "docker-compose.dev.yml must define services"
    services = compose['services']
    assert ('app' in services) or ('web' in services), \
        "docker-compose.dev.yml should define an 'app' or 'web' service"


def test_docker_compose_dev_volume_mount(compose):
    services = compose['services']
    service = services.get('app') or services.get('web')
    volumes = service.get('volumes', [])
    # Expect at least one volume mounting project root into container
    mount_exists = any(isinstance(vol, str) and vol.startswith('./') and ':' in vol for vol in volumes)
    assert mount_exists, \
        "docker-compose.dev.yml should mount project directory into container for hot-reload"


def test_docker_compose_dev_command_uses_uvicorn_reload(compose):
    services = compose['services']
    service = services.get('app') or services.get('web')
    command = service.get('command') or service.get('entrypoint')
    assert command is not None, "docker-compose.dev.yml should define a command or entrypoint for the service"
    cmd_str = command if isinstance(command, str) else ' '.join(command)
    assert 'uvicorn' in cmd_str and '--reload' in cmd_str, \
        "docker-compose.dev.yml command must run uvicorn with --reload"