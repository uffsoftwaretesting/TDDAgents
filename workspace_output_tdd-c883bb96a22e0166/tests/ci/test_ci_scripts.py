from pathlib import Path
import pytest
import yaml

# Diretório raiz do projeto (duas pastas acima de tests/ci)
ROOT = Path(__file__).resolve().parents[2]


def test_ci_workflow_file_exists():
    """
    Verifica se o workflow do GitHub Actions existe em .github/workflows/ci.yml
    """
    workflow = ROOT / '.github' / 'workflows' / 'ci.yml'
    assert workflow.exists(), f"Arquivo de workflow não encontrado em {workflow}"


def test_dockerfile_exists():
    """
    Verifica se o Dockerfile existe na raiz do projeto
    """
    dockerfile = ROOT / 'Dockerfile'
    assert dockerfile.exists(), f"Dockerfile não encontrado em {dockerfile}"


def test_ci_workflow_valid_yaml_and_jobs():
    """
    Carrega o YAML do workflow e verifica a presença dos jobs necessários
    """
    workflow = ROOT / '.github' / 'workflows' / 'ci.yml'
    content = workflow.read_text(encoding='utf-8')
    # Faz o parse do YAML
    config = yaml.safe_load(content)
    assert isinstance(config, dict), "CI workflow YAML não foi parseado como um dict"
    assert 'jobs' in config, "O workflow deve definir a chave 'jobs'"
    jobs = config['jobs']
    # Espera ao menos lint, pytest (ou test) e build
    assert 'lint' in jobs, "O workflow deve ter um job 'lint'"
    assert 'pytest' in jobs or 'test' in jobs, "O workflow deve ter um job 'pytest' ou 'test'"
    assert 'build' in jobs or 'docker-build' in jobs, "O workflow deve ter um job 'build' ou 'docker-build'"


def test_dockerfile_starts_with_from():
    """
    Verifica se o Dockerfile inicia com uma instrução FROM válida
    """
    dockerfile = ROOT / 'Dockerfile'
    content = dockerfile.read_text(encoding='utf-8').lstrip()
    assert content.upper().startswith('FROM '), "Dockerfile deve iniciar com uma instrução FROM"