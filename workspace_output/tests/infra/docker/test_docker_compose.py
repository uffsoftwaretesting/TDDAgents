import subprocess
import shutil
import time
import pytest


def is_tool_installed(name):
    return shutil.which(name) is not None


@pytest.fixture(scope="module", autouse=True)
def check_docker_tools():
    # Skip all tests in this module if docker or docker-compose is not installed
    if not is_tool_installed("docker"):
        pytest.skip("Docker CLI not found, skipping Docker infrastructure tests")
    if not is_tool_installed("docker-compose"):
        pytest.skip("docker-compose CLI not found, skipping Docker infrastructure tests")


def test_dockerfile_build(tmp_path):
    """
    Test that the Dockerfile can build an image without errors.
    """
    image_tag = "cpf_api_test:latest"
    # Build the image from the project root
    result = subprocess.run(
        ["docker", "build", "-f", "Dockerfile", "-t", image_tag, "."],
        capture_output=True,
        text=True
    )
    # Output logs on failure for diagnostics
    if result.returncode != 0:
        pytest.fail(
            f"Docker build failed with exit code {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    # Remove the test image to keep the environment clean
    subprocess.run(["docker", "rmi", "-f", image_tag], check=False)


def test_docker_compose_api_health():
    """
    Test that `docker-compose up` starts the API service and its healthcheck passes.
    """
    # Spin up services in detached mode
    up = subprocess.run(["docker-compose", "up", "-d"], capture_output=True, text=True)
    if up.returncode != 0:
        pytest.skip(
            f"docker-compose up failed: exit code {up.returncode}\n"
            f"STDOUT:\n{up.stdout}\nSTDERR:\n{up.stderr}"
        )
    try:
        # Allow some time for the healthchecks to run
        time.sleep(10)
        # Retrieve the container ID for the 'api' service
        ps = subprocess.run(
            ["docker-compose", "ps", "-q", "api"],
            capture_output=True,
            text=True,
            check=True
        )
        container_id = ps.stdout.strip()
        assert container_id, "Could not find API container via docker-compose ps"
        # Inspect the container health status
        inspect = subprocess.run(
            [
                "docker", "inspect",
                "--format={{.State.Health.Status}}",
                container_id
            ],
            capture_output=True,
            text=True,
            check=True
        )
        status = inspect.stdout.strip()
        assert status == "healthy", f"Expected container health 'healthy', got '{status}'"
    finally:
        # Tear down the compose environment
        subprocess.run(["docker-compose", "down", "--volumes"], capture_output=True)
