#!/bin/bash
set -euo pipefail

if [ -z "${SONAR_TOKEN:-}" ]; then
  echo "Erro: SONAR_TOKEN não definido."
  exit 1
fi

PROJECT_KEY=$(grep '^sonar.projectKey=' sonar-project.properties | cut -d= -f2-)
SONAR_API_URL="${SONAR_API_URL:-http://localhost:9000}"
METRICS_REPORT_FILE="${METRICS_REPORT_FILE:-sonar_metrics_report.txt}"

echo "🧪 1. Rodando a suíte TDD e gerando relatórios (Coverage e Execução)..."
# Ativa o virtualenv para garantir que pytest e dependências estão disponíveis
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi
# O '|| true' garante que o script não aborta se os testes falharem (Red phase do TDD)
PYTHONPATH=. pytest tests/ --cov=src --cov-report=xml --junitxml=test-results.xml || true

echo "🔧 2. Corrigindo caminhos do XML para o Docker entender..."
sed -i "s|$(pwd)|/usr/src|g" coverage.xml
sed -i "s|$(pwd)|/usr/src|g" test-results.xml

echo "🔍 3. Enviando os resultados para o SonarQube..."
docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -e SONAR_HOST_URL="http://host.docker.internal:9000" \
  -e SONAR_TOKEN="$SONAR_TOKEN" \
  -v "$(pwd):/usr/src" \
  sonarsource/sonar-scanner-cli \
  -Dsonar.python.xunit.reportPath=test-results.xml

echo "📊 4. Extraindo Relatório Oficial de Métricas do SonarQube..."
sleep 5

METRICS_JSON=""
LAST_HTTP_CODE=""
LAST_BODY=""
for i in {1..30}; do
  RESPONSE=$(curl -sS -u "$SONAR_TOKEN:" \
    -w $'\n%{http_code}' \
    "${SONAR_API_URL}/api/measures/component?component=${PROJECT_KEY}&metricKeys=coverage,duplicated_lines_density,sqale_debt_ratio,code_smells,complexity,cognitive_complexity,ncloc,test_success_density" || true)

  HTTP_CODE="${RESPONSE##*$'\n'}"
  BODY="${RESPONSE%$'\n'*}"

  if [ "$HTTP_CODE" = "200" ] && printf '%s' "$BODY" | python3 -c "import json,sys; json.load(sys.stdin)" >/dev/null 2>&1; then
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

METRICS_JSON="$METRICS_JSON" METRICS_REPORT_FILE="$METRICS_REPORT_FILE" python3 - <<'PY'
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
unit_tests_success = to_float(measures.get("test_success_density"))

if ncloc > 0:
    code_smells_density = (code_smells / ncloc) * 100
else:
    code_smells_density = 0.0

measures["code_smells_density"] = code_smells_density
# --- FIM DO CÁLCULO ---

rules = [
  ("test_success_density", "Sucesso dos Testes", ">=", 100.0, "%"),
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
  "      RELATÓRIO DE MÉTRICAS DE QUALIDADE (SONAR)      ",
  "======================================================",
  f"Projeto: {project_key}",
  f"Gerado em: {timestamp}",
  f"Linhas de Código Úteis (NCLOC): {int(ncloc)}",
  f"Total de Code Smells:           {int(code_smells)}",
  f"Sucesso dos Testes Unitários:   {format_value(unit_tests_success, '%')}",
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
print(f"\nRelatório salvo em: {report_path}\n")
PY

echo "✅ Análise concluída!"
