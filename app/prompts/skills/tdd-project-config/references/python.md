# Python: pytest and pytest-cov

## Example

A flat layout: the package `calculator/` at the project root, tests in `tests/`, dependencies in `requirements.txt` (which lists `pytest` and `pytest-cov` next to the project's own dependencies).

```json
{
  "version": {{PROJECT_CONFIG_VERSION}},
  "setup": "python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt",
  "test": ".venv/bin/python -m pytest -o junit_family=xunit1 --junitxml=\"${{JUNIT_REPORT_ENV}}\" --cov=calculator --cov-branch --cov-report=xml:\"${{COVERAGE_REPORT_ENV}}\"",
  "coverageReport": {"format": "cobertura"},
  "testFiles": ["tests/", "conftest.py", "pytest.ini"],
  "testSupportFiles": ["conftest.py", "tests/**/__init__.py", "tests/fixtures/", "pytest.ini"],
  "manifestFiles": ["requirements.txt"],
  "exclude": [".venv/", "__pycache__/", ".pytest_cache/", ".mypy_cache/", "*.egg-info/", "build/", "dist/"]
}
```

## Options

- `--junitxml=<path>` writes the JUnit report. `-o junit_family=xunit1` keeps the `file` and `line` attributes on each `<testcase>`; the default `xunit2` family drops them, and TDDAgents then cannot tell which test files ran.
- `python -m pytest` puts the project root on `sys.path`, so `import calculator` works without installing the package.
- `--cov=<package>` measures the production package only (not the tests); `--cov-branch` adds branch coverage; `--cov-report=xml:<path>` writes Cobertura XML.
- Run everything through the virtual environment's interpreter (`.venv/bin/python`), never a bare `python` or `pytest` that might resolve to another installation.

## Other layouts and tools

- **`src/` layout:** install the package in the environment (`.venv/bin/python -m pip install -e .` in `setup`, which needs a build backend in `pyproject.toml`) or set `pythonpath = src` in the pytest configuration, and pass `--cov=src/<package>` or the package name.
- **`pyproject.toml` with optional test dependencies:** `setup` installs `-e ".[test]"`; declare `pyproject.toml` in `manifestFiles`.
- **uv:** `setup` is `uv sync --frozen` (installs the `dev` dependency group by default), `test` is `uv run pytest …`; declare `pyproject.toml` and `uv.lock`. **Poetry:** `poetry install`, `poetry run pytest …`; declare `pyproject.toml` and `poetry.lock`. Check the tool's docs for your version.

## Pitfalls

- pytest exit 5 (no tests collected) and 4 (usage error, such as an unknown option) are failed runs: check the command's options once the environment exists.
- `pytest.ini` (or `[tool.pytest.ini_options]`) options like `addopts = -x` or `-k` change which tests run; avoid them.
- `tests/__init__.py` files and `conftest.py` are support files: they never appear in the report.
