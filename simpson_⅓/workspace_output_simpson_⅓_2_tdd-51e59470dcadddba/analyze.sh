#!/bin/bash
set -euo pipefail

if [ -z "${SONAR_TOKEN:-}" ]; then
  echo "Erro: SONAR_TOKEN não definido."
  exit 1
fi

PROJECT_KEY=$(grep '^sonar.projectKey=' sonar-project.properties | cut -d= -f2-)
SONAR_API_URL="${SONAR_API_URL:-http://localhost:9000}"
SONAR_HOST_URL="${SONAR_HOST_URL:-http://localhost:9000}"
METRICS_REPORT_FILE="${METRICS_REPORT_FILE:-sonar_metrics_report.txt}"

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
  PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
elif [ -x "../.venv/bin/python" ]; then
  PYTHON_BIN="../.venv/bin/python"
elif [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  echo "🐍 Criando ambiente virtual local em .venv..."
  python3 -m venv .venv
  PYTHON_BIN=".venv/bin/python"
fi

PYTHON_BIN_DIR=$(dirname "$PYTHON_BIN")
export PATH="$PYTHON_BIN_DIR:$PATH"

echo "📦 1. Preparando instalador Python (pip)..."
if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  echo "ℹ️  pip não encontrado neste ambiente. Executando ensurepip..."
  "$PYTHON_BIN" -m ensurepip --upgrade
fi

echo "📦 2. Instalando dependências do projeto (quando disponíveis)..."
if [ -f "requirements.txt" ]; then
  "$PYTHON_BIN" -m pip install --disable-pip-version-check -r requirements.txt
elif [ -f "setup.py" ]; then
  "$PYTHON_BIN" -m pip install --disable-pip-version-check -e .
elif [ -f "pyproject.toml" ] && grep -Eq '^\[(project|tool\.poetry|tool\.setuptools)\]' pyproject.toml; then
  "$PYTHON_BIN" -m pip install --disable-pip-version-check -e .
else
  echo "ℹ️  Nenhum requirements.txt ou metadado de pacote encontrado. Pulando instalação de dependências."
fi

echo "🧰 2.1 Garantindo ferramentas de qualidade necessárias..."
if ! "$PYTHON_BIN" -m flake8 --version >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install --disable-pip-version-check flake8
fi
if ! "$PYTHON_BIN" -m mypy --version >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install --disable-pip-version-check mypy
fi
if ! "$PYTHON_BIN" -m pylint --version >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install --disable-pip-version-check pylint
fi
if ! "$PYTHON_BIN" -m pytest --version >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install --disable-pip-version-check pytest
fi
if ! "$PYTHON_BIN" -c "import pytest_cov" >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install --disable-pip-version-check pytest-cov
fi

echo "🧪 3. Rodando a suíte TDD e gerando o relatório de coverage..."
PYTHONPATH=src "$PYTHON_BIN" -m pytest tests/ --cov=src --cov-report=xml

echo "🔧 4. Corrigindo caminhos do XML para o Docker entender..."
sed -i "s|$(pwd)|/usr/src|g" coverage.xml

echo "🔍 5. Enviando os resultados para o SonarQube..."
SCANNER_HOST_URL="$SONAR_HOST_URL"
ADD_HOST_ARGS=()
DOCKER_NET_ARGS=()
if [ "$(uname -s)" = "Linux" ]; then
  DOCKER_NET_ARGS=(--network host)
elif [[ "$SONAR_HOST_URL" =~ ^https?://(localhost|127\.0\.0\.1)(:([0-9]+))?(/.*)?$ ]]; then
  SCANNER_HOST_URL="${SONAR_HOST_URL/localhost/host.docker.internal}"
  SCANNER_HOST_URL="${SCANNER_HOST_URL/127.0.0.1/host.docker.internal}"
  ADD_HOST_ARGS=(--add-host host.docker.internal:host-gateway)
fi

docker run --rm \
  "${DOCKER_NET_ARGS[@]}" \
  "${ADD_HOST_ARGS[@]}" \
  -e SONAR_HOST_URL="$SCANNER_HOST_URL" \
  -e SONAR_TOKEN="$SONAR_TOKEN" \
  -v "$(pwd):/usr/src" \
  sonarsource/sonar-scanner-cli

echo "📊 6. Extraindo Relatório Oficial de Métricas do SonarQube..."
sleep 5

METRICS_JSON=""
LAST_HTTP_CODE=""
LAST_BODY=""
for i in {1..30}; do
  # NOTA: ncloc foi adicionado na lista de metricKeys abaixo
  RESPONSE=$(curl -sS -u "$SONAR_TOKEN:" \
    -w $'\n%{http_code}' \
    "${SONAR_API_URL}/api/measures/component?component=${PROJECT_KEY}&metricKeys=coverage,duplicated_lines_density,sqale_debt_ratio,code_smells,complexity,cognitive_complexity,ncloc" || true)

  HTTP_CODE="${RESPONSE##*$'\n'}"
  BODY="${RESPONSE%$'\n'*}"

  if [ "$HTTP_CODE" = "200" ] && printf '%s' "$BODY" | "$PYTHON_BIN" -c "import json,sys; json.load(sys.stdin)" >/dev/null 2>&1; then
    METRICS_JSON="$BODY"
    break
  fi

  LAST_HTTP_CODE="$HTTP_CODE"
  LAST_BODY="$BODY"
  sleep 2
done

if [ -z "$METRICS_JSON" ]; then
  echo "Erro: não foi possível obter métricas do SonarQube."
  echo "Status HTTP da última tentativa: ${LAST_HTTP_CODE:-sem resposta}"
  if [ -n "$LAST_BODY" ]; then
    echo "Última resposta recebida (primeiros 300 caracteres):"
    printf "%s\n" "$LAST_BODY" | head -c 300
    echo
  fi
  exit 1
fi

METRICS_JSON="$METRICS_JSON" METRICS_REPORT_FILE="$METRICS_REPORT_FILE" "$PYTHON_BIN" - <<'PY'
import json
import os
from datetime import datetime

raw = os.environ.get("METRICS_JSON", "")
if not raw.strip():
  raise SystemExit("Erro: resposta de métricas vazia.")

data = json.loads(raw)
measures = {m["metric"]: m["value"] for m in data.get("component", {}).get("measures", [])}
project_key = data.get("component", {}).get("key", "desconhecido")

def to_float(value):
  if value is None:
    return None
  text = str(value).strip()
  if not text:
    return None
  text = text.replace(",", ".")
  try:
    return float(text)
  except ValueError:
    return None

def evaluate(value, operator, threshold):
  if value is None:
    return False
  if operator == "<=":
    return value <= threshold
  if operator == ">=":
    return value >= threshold
  return False

def format_value(value, suffix=""):
  if value is None:
    return "N/A"
  if float(value).is_integer():
    rendered = str(int(value))
  else:
    rendered = f"{value:.2f}"
  return f"{rendered}{suffix}"

# --- INÍCIO DO CÁLCULO DE DENSIDADE ---
code_smells = to_float(measures.get("code_smells", 0)) or 0.0
ncloc = to_float(measures.get("ncloc", 0)) or 0.0

if ncloc > 0:
    code_smells_density = (code_smells / ncloc) * 100
else:
    code_smells_density = 0.0

measures["code_smells_density"] = code_smells_density
# --- FIM DO CÁLCULO ---

rules = [
  ("cognitive_complexity", "Complexidade Cognitiva", "<=", 15.0, ""),
  ("complexity", "Complexidade Ciclomatica", "<=", 10.0, ""),
  ("code_smells_density", "Densidade Code Smells", "<=", 5.0, ""),
  ("duplicated_lines_density", "Duplicacao de Codigo", "<=", 3.0, "%"),
  ("coverage", "Cobertura de Testes", ">=", 80.0, "%"),
  ("sqale_debt_ratio", "Debt Ratio", "<=", 5.0, "%"),
]

rows = []
all_passed = True
for metric, label, operator, threshold, suffix in rules:
  value = to_float(measures.get(metric))
  passed = evaluate(value, operator, threshold)
  all_passed = all_passed and passed
  status = "PASSED" if passed else "FAILED"
  threshold_txt = f"{int(threshold) if threshold.is_integer() else threshold}{suffix}"
  rows.append(
    f"{label:<28} Valor: {format_value(value, suffix):>8} | Meta: {operator} {threshold_txt:<6} | {status}"
  )

summary = "PASSED" if all_passed else "FAILED"
timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

report_lines = [
  "======================================================",
  "      RELATORIO DE METRICAS DE QUALIDADE (SONAR)      ",
  "======================================================",
  f"Projeto: {project_key}",
  f"Gerado em: {timestamp}",
  f"Linhas de Código Úteis (NCLOC): {int(ncloc)}",
  f"Total de Code Smells:           {int(code_smells)}",
  "",
  *rows,
  "",
  f"STATUS GERAL: {summary}",
  "======================================================",
]

report_text = "\n".join(report_lines)
report_path = os.environ.get("METRICS_REPORT_FILE", "sonar_metrics_report.txt")

with open(report_path, "w", encoding="utf-8") as handle:
  handle.write(report_text + "\n")

print("\n" + report_text)
print(f"\nRelatorio salvo em: {report_path}\n")
PY

echo "✅ Análise concluída!"
