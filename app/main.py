"""
Ponto de entrada do pipeline de agentes TDD.

Estratégia de Thread ID
────────────────────────
Cada execução do LangGraph é identificada por um thread_id armazenado no Postgres.
O ID determina se uma nova execução começa do zero ou retoma uma anterior:

  • Mesmo thread_id  → LangGraph carrega o último checkpoint do Postgres e
                       continua de onde o processo anterior parou.
                       Use isto para recuperação de falhas.

  • Novo thread_id   → AgentState completamente em branco, sem contaminação de histórico.
                       Use isto para cada tarefa genuinamente nova.

Derivamos o thread_id a partir de um hash SHA-1 de (function_name + specification).
Isso significa:
  - Re-executar a mesma tarefa após uma falha → mesmo hash → retomada automática.
  - Uma spec ou function_name diferente → hash diferente → estado limpo.
  - Passe --fresh na CLI para forçar um UUID aleatório independente do conteúdo.
"""

import hashlib
import logging
import os
import uuid
import argparse

from dotenv import load_dotenv

from app.requirements_orchestrator import RequirementsOrchestrator
from app.orchestrator import TDDOrchestrator

# ── Configuração de logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
for noisy_logger in ("httpx", "httpcore", "openai", "autogen_core", "autogen_agentchat"):
    logging.getLogger(noisy_logger).setLevel(logging.ERROR)

logger = logging.getLogger("TDDMain")
logger.setLevel(logging.INFO)


# ── Funções auxiliares ────────────────────────────────────────────────────────

def make_thread_id(function_name: str, specification: str) -> str:
    """
    Deriva um thread_id estável e resistente a colisões a partir do conteúdo da tarefa.

    O mesmo par (function_name, specification) sempre gera o mesmo ID,
    permitindo que uma execução interrompida seja retomada automaticamente
    apenas re-executando main.py com os mesmos inputs.
    """
    payload = f"{function_name}::{specification}"
    return "tdd-" + hashlib.sha1(payload.encode()).hexdigest()[:16]


def detect_function_name(specification: str) -> str:
    """
    Extração do nome da função em snake_case a partir da especificação.

    Procura a primeira linha de título (começa com #) e converte.
    Retorna 'generated_function' como fallback.
    """
    for line in specification.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            words = stripped.lstrip("#").strip().split()
            if words:
                return "_".join(w.lower() for w in words[:4])
    return "generated_function"


def run_requirements_gathering() -> str:
    """Fase interativa de levantamento de requisitos."""
    print("\n" + "=" * 60)
    print("🤖  LEVANTAMENTO DE REQUISITOS")
    print("=" * 60)
    print("Descreva o que você gostaria de implementar:\n")

    initial_input = input("[Sua solicitação]: ").strip()
    if not initial_input:
        raise ValueError("A solicitação não pode estar vazia.")

    orchestrator = RequirementsOrchestrator()
    final_state = orchestrator.run(initial_input)
    return final_state["final_specification"]


# ── Interface de linha de comando ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa o pipeline de agentes TDD.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "Força uma execução completamente nova gerando um thread_id aleatório. "
            "Sem esta flag a execução é retomável: se o processo falhar e for "
            "reiniciado com os mesmos inputs, o LangGraph continua a partir do "
            "último checkpoint salvo no Postgres."
        ),
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help=(
            "Define explicitamente o thread_id (substitui --fresh e o hash "
            "derivado automaticamente). Útil para retomar ou inspecionar uma "
            "execução específica pelo seu ID."
        ),
    )
    return parser.parse_args()


# ── Função principal ──────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()
    args = parse_args()

    # ── Fase 1: levantamento de requisitos ────────────────────────────────────
    try:
        specification = run_requirements_gathering()
    except KeyboardInterrupt:
        print("\nOperação cancelada pelo usuário.")
        return

    if not specification:
        logger.error("❌ Falha ao gerar a especificação.")
        return

    function_name = detect_function_name(specification)
    logger.info(f"Nome de função detectado: '{function_name}'")

    # ── Resolução do thread_id ────────────────────────────────────────────────
    if args.thread_id:
        # Sobrescrita explícita — o usuário sabe exatamente qual execução retomar.
        thread_id = args.thread_id
        logger.info(f"🔑 Usando thread_id explícito: {thread_id}")

    elif args.fresh:
        # Usuário quer uma execução garantidamente limpa, independente do conteúdo.
        thread_id = "tdd-" + uuid.uuid4().hex[:16]
        logger.info(f"🆕 Execução nova — thread_id gerado: {thread_id}")

    else:
        # Padrão: hash derivado do conteúdo.
        # Re-executar com inputs idênticos após uma falha retoma a execução.
        # Uma spec ou function_name diferente gera um hash diferente → estado limpo.
        thread_id = make_thread_id(function_name, specification)
        logger.info(
            f"🔑 thread_id derivado do conteúdo: {thread_id}  "
            f"(use --fresh para forçar uma nova execução)"
        )

    # ── Fase 2: execução TDD ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("🔄  INICIANDO FASE TDD")
    print("=" * 60)
    print(f"📜 Prévia da especificação:\n{specification[:300]}...\n")
    print(f"🔑 Thread ID: {thread_id}\n")

    orchestrator = TDDOrchestrator(task_key=thread_id)
    final_state = orchestrator.run(
        specification=specification,
        function_name=function_name,
    )

    # ── Resultado ─────────────────────────────────────────────────────────────
    final_status = final_state.get("status", "unknown")
    failed = final_state.get("failed_requirements", [])

    if final_status in ("plan_complete", "completed_with_review"):
        print("\n✅  Pipeline concluído com sucesso.")
    else:
        print(f"\n⚠️  Pipeline finalizado com status: '{final_status}'")

    if failed:
        print(f"\n⚠️  {len(failed)} requisito(s) não puderam ser satisfeitos:")
        for req in failed:
            print(f"   • {req}")

    # Exibe o thread_id ao final para que o usuário possa copiá-lo e usar com --thread-id
    print(f"\n🔑 Thread ID desta execução: {thread_id}")
    print("   (use --thread-id para retomar ou inspecionar esta execução)\n")


if __name__ == "__main__":
    main()