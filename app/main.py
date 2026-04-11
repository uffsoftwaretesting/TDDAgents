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


def run_requirements_gathering() -> tuple[str, str, list[str]]:
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
    reqs = final_state.get("conversation_history", "")
    user_prompts = final_state.get("user_prompts", [initial_input])
    
    if not spec:
        print("\n❌ Falha crítica: O Engenheiro não conseguiu gerar a especificação.")
        sys.exit(1)
        
    return spec, reqs, user_prompts


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
    specification, requirements, user_prompts = run_requirements_gathering()

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

    # ── Configuração de Workspace e Logs ──────────────────────────────────────
    workspace_dir = f"workspace_output_{thread_id}"
    os.makedirs(workspace_dir, exist_ok=True)
    
    # Exporta todo o tráfego do logger para o .txt no novo diretório
    log_file_path = os.path.join(workspace_dir, "execution_logs.txt")
    file_handler = logging.FileHandler(log_file_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    prompts_path = os.path.join(workspace_dir, "user_prompts.txt")
    with open(prompts_path, "w", encoding="utf-8") as f:
        f.write("PROMPTS ENVIADOS PELO USUARIO VIA TERMINAL\n")
        f.write("================================================================================\n")
        for idx, prompt in enumerate(user_prompts, start=1):
            f.write(f"{idx}. {prompt}\n")
        f.write("================================================================================\n")

    # ── Fase 2: Execução TDD ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("🚀  FASE 2: ORQUESTRAÇÃO TDD MULTI-AGENTE (SANDBOX E2B)")
    print("=" * 80)
    print(f"📂 Output Directory: ./{workspace_dir}/")
    print("📜 Prévia da Especificação Técnica que guiará os agentes:")
    print("-" * 80)
    print(f"{specification[:500]}...\n\n[CONTINUA NA MEMÓRIA DOS AGENTES...]\n")
    print("-" * 80 + "\n")

    orchestrator = TDDOrchestrator(task_key=thread_id)
    
    try:
        final_state = orchestrator.run(specification=specification, requirements=requirements)
    except KeyboardInterrupt:
        print("\n🛑 Execução TDD interrompida pelo usuário de forma segura.")
        sys.exit(0)

    # ── Resultado e Extração ──────────────────────────────────────────────────
    final_status = final_state.get("status", "unknown")
    failed = final_state.get("failed_requirements", [])
    file_system = final_state.get("file_system", {})
    plan = final_state.get("plan", [])

    if final_status in ("plan_complete", "completed_with_review", "completed_successfully"):
        print("\n✅  PIPELINE CONCLUÍDO COM SUCESSO!")
    else:
        print(f"\n⚠️  PIPELINE FINALIZADO COM STATUS: '{final_status}'")

    print(f"\n💾 Extraindo artefatos para a máquina local ({workspace_dir}/)...")

    # 1. Extrai o código-fonte
    if file_system:
        for filepath, content in file_system.items():
            clean_path = filepath.lstrip("/")
            if clean_path.startswith("home/user/"):
                clean_path = clean_path.replace("home/user/", "", 1)
            
            full_path = os.path.join(workspace_dir, clean_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

    # 2. Extrai o planner.txt
    if plan:
        plan_path = os.path.join(workspace_dir, "planner.txt")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write("PLANO DE SUB-REQUISITOS TDD\n")
            f.write("================================================================================\n")
            for i, item in enumerate(plan):
                f.write(f"{i+1}. {item}\n")
            f.write("================================================================================\n")

    # 3. Extrai as especificações do Engenheiro
    spec_path = os.path.join(workspace_dir, "engineer_specifications.txt")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write("ESPECIFICAÇÕES TÉCNICAS DO ENGENHEIRO\n")
        f.write("================================================================================\n")
        f.write(specification)

    # 4. Extrai o histórico dos requisitos validados
    reqs_path = os.path.join(workspace_dir, "confirmed_user_requirements.txt")
    with open(reqs_path, "w", encoding="utf-8") as f:
        f.write("REQUISITOS ENVIADOS PELO USUÁRIO\n")
        f.write("================================================================================\n")
        for req in requirements:
            f.write(f"{req}")
        f.write("================================================================================\n")

    print(f"📁 Todos os artefatos e logs foram salvos com sucesso!")

    if failed:
        print(f"\n⚠️  {len(failed)} requisito(s) não puderam ser satisfeitos:")
        for req in failed:
            print(f"   • {req.get('requirement', req)}")

    print(f"\n🔑 Thread ID desta execução: {thread_id}")
    print("   (Para inspecionar o estado ou continuar futuramente, use --thread-id)\n")

if __name__ == "__main__":
    main()