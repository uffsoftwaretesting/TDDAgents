import hashlib
import logging
import uuid
import argparse
import os
import sys

from dotenv import load_dotenv

from app.graph.subgraphs.requirements_orchestrator_subgraph import RequirementsOrchestrator
from app.graph.orchestrator import TDDOrchestrator

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Silencia logs verbosos de bibliotecas base
for noisy_logger in ("httpx", "httpcore", "anthropic", "e2b"):
    logging.getLogger(noisy_logger).setLevel(logging.ERROR)

logger = logging.getLogger("TDDMain")
logger.setLevel(logging.INFO)


def make_thread_id(specification: str) -> str:
    """Deriva um thread_id estável puramente a partir do texto da especificação."""
    payload = f"sandbox::{specification}"
    return "tdd-" + hashlib.sha1(payload.encode()).hexdigest()[:16]


def run_requirements_gathering() -> str:
    """Fase interativa de levantamento de requisitos."""
    print("\n" + "=" * 80)
    print("🤖  FASE 1: LEVANTAMENTO DE REQUISITOS (PRODUCT MANAGER)")
    print("=" * 80)
    print("Descreva a aplicação, framework ou funcionalidade que você deseja construir.")
    print("Seja detalhado. O Analista fará perguntas se algo estiver vago.\n")

    try:
        initial_input = input("👤 [Sua Solicitação Inicial]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n👋 Operação cancelada pelo usuário.")
        sys.exit(0)

    if not initial_input:
        print("❌ A solicitação inicial não pode estar vazia.")
        sys.exit(1)

    orchestrator = RequirementsOrchestrator()
    final_state = orchestrator.run(initial_input)
    
    spec = final_state.get("final_specification", "")
    if not spec:
        print("\n❌ Falha crítica: O Engenheiro não conseguiu gerar a especificação.")
        sys.exit(1)
        
    return spec


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa o pipeline de agentes TDD Enterprise (E2B Sandbox Edition).",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Força uma execução nova gerando um thread_id aleatório.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Define explicitamente o thread_id.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    # ── Fase 1: Levantamento de Requisitos ────────────────────────────────────
    specification = run_requirements_gathering()

    # ── Resolução do thread_id ────────────────────────────────────────────────
    if args.thread_id:
        thread_id = args.thread_id
        logger.info(f"🔑 Usando thread_id explícito: {thread_id}")
    elif args.fresh:
        thread_id = "tdd-" + uuid.uuid4().hex[:16]
        logger.info(f"🆕 Execução nova — thread_id gerado: {thread_id}")
    else:
        thread_id = make_thread_id(specification)
        logger.info(f"🔑 thread_id derivado da especificação: {thread_id}")

    # ── Fase 2: Execução TDD ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("🚀  FASE 2: ORQUESTRAÇÃO TDD MULTI-AGENTE (SANDBOX E2B)")
    print("=" * 80)
    print(f"🔑 Thread ID: {thread_id}")
    print("📜 Prévia da Especificação Técnica que guiará os agentes:")
    print("-" * 80)
    print(f"{specification[:500]}...\n\n[CONTINUA NA MEMÓRIA DOS AGENTES...]\n")
    print("-" * 80 + "\n")

    orchestrator = TDDOrchestrator(task_key=thread_id)
    
    try:
        final_state = orchestrator.run(specification=specification)
    except KeyboardInterrupt:
        print("\n🛑 Execução TDD interrompida pelo usuário de forma segura.")
        sys.exit(0)

    # ── Resultado ─────────────────────────────────────────────────────────────
    final_status = final_state.get("status", "unknown")
    failed = final_state.get("failed_requirements", [])

    if final_status in ("plan_complete", "completed_with_review"):
        print("\n✅  PIPELINE CONCLUÍDO COM SUCESSO!")

        file_system = final_state.get("file_system", {})
        if file_system:
            print("\n💾 Extraindo arquivos da Sandbox para a sua máquina local...")
            workspace_dir = "workspace_output"

            for filepath, content in file_system.items():
                clean_path = filepath.lstrip("/")
                if clean_path.startswith("home/user/"):
                    clean_path = clean_path.replace("home/user/", "", 1)
                
                full_path = os.path.join(workspace_dir, clean_path)

                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
            print(f"📁 Todos os arquivos foram salvos na pasta: ./{workspace_dir}/")

    else:
        print(f"\n⚠️  PIPELINE FINALIZADO COM STATUS: '{final_status}'")

        file_system = final_state.get("file_system", {})
        
        if file_system:
            print("\n💾 Extraindo arquivos da Sandbox para a sua máquina local...")
            workspace_dir = "workspace_output"

            for filepath, content in file_system.items():
                clean_path = filepath.lstrip("/")
                if clean_path.startswith("home/user/"):
                    clean_path = clean_path.replace("home/user/", "", 1)
                
                full_path = os.path.join(workspace_dir, clean_path)

                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
            print(f"\n📁 Todos os arquivos foram salvos na pasta: ./{workspace_dir}/")

    if failed:
        print(f"\n⚠️  {len(failed)} requisito(s) não puderam ser satisfeitos:")
        for req in failed:
            print(f"   • {req.get('requirement', req)}")

    print(f"\n🔑 Thread ID desta execução: {thread_id}")
    print("   (Para inspecionar o estado ou continuar futuramente, use --thread-id)\n")


if __name__ == "__main__":
    main()