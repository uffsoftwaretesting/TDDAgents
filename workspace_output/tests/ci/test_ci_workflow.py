import pathlib
import yaml
import pytest

CI_FILE = pathlib.Path('.github/workflows/ci.yml')

@pytest.fixture(scope='module')
def ci_workflow():
    assert CI_FILE.exists(), f"CI workflow file not found at {CI_FILE}"
    content = CI_FILE.read_text(encoding='utf-8')
    data = yaml.safe_load(content)
    return data


def test_jobs_present(ci_workflow):
    jobs = ci_workflow.get('jobs')
    assert jobs is not None, "No 'jobs' key found in CI workflow"
    expected = {'lint', 'type-check', 'test'}
    assert expected.issubset(jobs.keys()), f"Expected jobs {expected}, found {set(jobs.keys())}"


def test_lint_job(ci_workflow):
    lint = ci_workflow['jobs']['lint']
    assert lint.get('name', '').lower().startswith('lint'), "Lint job must be named accordingly"
    steps = lint.get('steps', [])
    assert any(step.get('uses', '').startswith('actions/checkout') for step in steps), "Checkout missing in lint job"
    assert any('ruff' in step.get('run', '') for step in steps), "ruff run missing in lint job"


def test_type_check_job(ci_workflow):
    tc = ci_workflow['jobs']['type-check']
    assert tc.get('name', '').lower().startswith('type'), "Type-check job must be named accordingly"
    steps = tc.get('steps', [])
    assert any(step.get('uses', '').startswith('actions/checkout') for step in steps), "Checkout missing in type-check job"
    assert any('mypy' in step.get('run', '') for step in steps), "mypy run missing in type-check job"


def test_test_job_contains_fail_fast_and_matrix(ci_workflow):
    test_job = ci_workflow['jobs']['test']
    strategy = test_job.get('strategy')
    assert strategy is not None, "Strategy missing in test job"
    assert strategy.get('fail-fast') is True, "fail-fast must be true in test job"
    matrix = strategy.get('matrix', {})
    pv = matrix.get('python-version')
    assert isinstance(pv, list) and pv, "python-version matrix must be a non-empty list"
    # Check at least one supported version
    assert any(v in ['3.9', '3.10', '3.11'] for v in pv), "Matrix must include supported Python versions"
    steps = test_job.get('steps', [])
    assert any(step.get('uses', '').startswith('actions/setup-python') for step in steps), "setup-python missing in test job"
    assert any('pytest' in step.get('run', '') for step in steps), "pytest run missing in test job"