import hashlib
import logging
import uuid
import argparse
import os
import sys
import textwrap
from pathlib import Path

from app.utils.resilience_metrics import write_resilience_metrics
from app.utils.pass_rate import write_pass_rate_report

# Permite executar este arquivo diretamente sem perder imports absolutos `app.*`.
if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

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


def get_initial_user_prompt() -> str:
    """
    Exibe o menu de casos de uso lendo os arquivos .txt do diretório specs,
    mostra o aviso padrão e retorna o prompt escolhido (ou digitado) pelo usuário.
    """
    mensagem_aviso = (
        "Os prompts abaixo foram utilizados para realizar os testes.\n\n"
        "ATENÇÃO: O agente analyzer pode perguntar mais detalhes sobre os requisitos, "
        "o que irá requerer mais interação e prompts provenientes do usuário. Para fins "
        "da pesquisa científica realizada, consideramos tentativas onde o agente Analyzer não "
        "perguntava nenhum detalhe a mais de implementação e considerava o prompt inicial "
        "suficiente para elicitar os requisitos. Por isso, recomendamos que, ao acionar "
        "alguma das opções utilizadas de prompt, descarte quaisquer tentativas "
        "de pedidos de detalhamento maior. Somente considere aquelas tentatiivas em que o prompt INICIAL é suficiente para elicitação de requisitos.\n"
    )
    print(mensagem_aviso)

    # Resolve o caminho para o diretório de specs
    specs_dir = Path("app/prompts/specs")
    
    if not specs_dir.exists() or not specs_dir.is_dir():
        print(f"⚠️ Aviso: Diretório de specs não encontrado em '{specs_dir}'.\n")
        spec_files = []
    else:
        # Puxa os arquivos .txt e ordena alfabeticamente
        spec_files = sorted([f for f in specs_dir.iterdir() if f.suffix == '.txt'])

    print("MENU DE CASO DE USOS (digite a opção desejada):\n")
    for i, file_path in enumerate(spec_files, start=1):
        print(f"{i}. {file_path.name}")
    
    custom_input_option = len(spec_files) + 1
    print(f"{custom_input_option}. Digite seu próprio prompt\n")

    while True:
        try:
            escolha_str = input("Selecione uma opção: ").strip()
            if not escolha_str:
                continue
            
            escolha = int(escolha_str)
            
            # Caso o usuário escolha um dos arquivos .txt
            if 1 <= escolha <= len(spec_files):
                selected_file = spec_files[escolha - 1]
                with open(selected_file, "r", encoding="utf-8") as f:
                    prompt = f.read().strip()
                print(f"\n✅ [Prompt carregado de '{selected_file.name}']\n")
                return prompt
                
            # Caso o usuário decida digitar o próprio prompt
            elif escolha == custom_input_option:
                prompt = input("\n👤 [Sua Solicitação Inicial]: ").strip()
                return prompt
                
            else:
                print("❌ Opção inválida. Tente novamente.")
        except ValueError:
            print("❌ Entrada inválida. Por favor, digite um número correspondente ao menu.")


def run_requirements_gathering() -> tuple[str, str, list[str]]:
    """Fase interativa de levantamento de requisitos."""
    print("\n" + "=" * 80)
    print("🤖  FASE 1: LEVANTAMENTO DE REQUISITOS (PRODUCT MANAGER)")
    print("=" * 80)
    print("Descreva a aplicação, framework ou funcionalidade que você deseja construir.")
    print("Seja detalhado. O Analista fará perguntas se algo estiver vago.\n")

    try:
        # Substituído o input manual pela função com o menu dinâmico
        initial_input = get_initial_user_prompt()
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


def format_dialogue_text(dialogue: str, width: int = 88) -> str:
    """Formata o diálogo para leitura em arquivo texto com quebra de linha."""
    if not dialogue or not dialogue.strip():
        return "(vazio)"

    formatted_lines: list[str] = []
    speaker_prefixes = ("[Usuário]:", "[Usuario]:", "[Analista]:")

    for raw_line in dialogue.splitlines():
        line = raw_line.strip()
        if not line:
            formatted_lines.append("")
            continue

        speaker = next((prefix for prefix in speaker_prefixes if line.startswith(prefix)), None)
        if speaker:
            content = line[len(speaker):].strip()
            if not content:
                formatted_lines.append(speaker)
                continue

            indent = " " * (len(speaker) + 1)
            wrapped = textwrap.fill(
                content,
                width=width,
                initial_indent=f"{speaker} ",
                subsequent_indent=indent,
                break_long_words=False,
                break_on_hyphens=False,
            )
            formatted_lines.append(wrapped)
            continue

        wrapped = textwrap.fill(
            line,
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
        )
        formatted_lines.append(wrapped)

    return "\n".join(formatted_lines)


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


def _write_token_report(artifacts_dir: str, orchestrator: TDDOrchestrator) -> None:
    """Logs token usage and persists a report to artifacts_dir/token_usage.txt."""
    summary = orchestrator.token_tracker.summary()
    totals = summary["totals"]

    os.makedirs(artifacts_dir, exist_ok=True)

    logger.info(
        "📊 Token Usage — total: %d | prompt: %d | completion: %d | cached: %d",
        totals["total_tokens"],
        totals["prompt_tokens"],
        totals["completion_tokens"],
        totals["cached_tokens"],
    )

    token_report_path = os.path.join(artifacts_dir, "token_usage.txt")
    with open(token_report_path, "w", encoding="utf-8") as f:
        f.write("TOKEN USAGE REPORT\n")
        f.write("=" * 80 + "\n\n")
        f.write("BY MODEL:\n")
        for model, stats in summary["by_model"].items():
            f.write(f"  {model}:\n")
            for k, v in stats.items():
                f.write(f"    {k}: {v}\n")
        f.write("\nTOTALS:\n")
        for k, v in totals.items():
            f.write(f"  {k}: {v}\n")


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
    workspace_dir_name = f"workspace_output_{thread_id}"
    workspace_dir = os.path.abspath(workspace_dir_name)
    os.makedirs(workspace_dir, exist_ok=True)

    artifacts_dir = os.path.join(workspace_dir, "metrics_and_logging")
    os.makedirs(artifacts_dir, exist_ok=True)

    log_file_path = os.path.join(artifacts_dir, "execution_logs.txt")
    file_handler = logging.FileHandler(log_file_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    formatted_requirements = format_dialogue_text(requirements)

    prompts_path = os.path.join(artifacts_dir, "user_analyst_dialogue.txt")
    with open(prompts_path, "w", encoding="utf-8") as f:
        f.write("PROMPTS ENVIADOS PELO USUARIO VIA TERMINAL\n")
        f.write("=" * 80 + "\n")
        for idx, prompt in enumerate(user_prompts, start=1):
            wrapped_prompt = textwrap.fill(
                prompt,
                width=88,
                initial_indent=f"{idx}. ",
                subsequent_indent=" " * (len(f"{idx}. ")),
                break_long_words=False,
                break_on_hyphens=False,
            )
            f.write(wrapped_prompt + "\n")
        f.write("=" * 80 + "\n\n")
        f.write("DIALOGO COMPLETO ENTRE USUARIO E ANALISTA\n")
        f.write("=" * 80 + "\n")
        f.write(formatted_requirements + "\n")
        f.write("=" * 80 + "\n")

    initial_prompt_path = os.path.join(artifacts_dir, "initial_user_prompt.txt")
    initial_prompt = user_prompts[0] if user_prompts else ""
    wrapped_initial_prompt = "\n".join(
        textwrap.fill(line, width=80) if line.strip() else ""
        for line in (initial_prompt.splitlines() or [initial_prompt])
    )
    with open(initial_prompt_path, "w", encoding="utf-8") as f:
        f.write("PROMPT INICIAL ENVIADO PELO USUARIO\n")
        f.write("=" * 80 + "\n")
        f.write((wrapped_initial_prompt or "(vazio)") + "\n")
        f.write("=" * 80 + "\n")

    # ── Fase 2: Execução TDD ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("🚀  FASE 2: ORQUESTRAÇÃO TDD MULTI-AGENTE (SANDBOX E2B)")
    print("=" * 80)
    print(f"📂 Output Directory: ./{workspace_dir_name}/")
    print("📜 Prévia da Especificação Técnica que guiará os agentes:")
    print("-" * 80)
    print(f"{specification[:500]}...\n\n[VEJA O ARQUIVO engineer_specifications.txt...]\n")
    print("-" * 80 + "\n")

    orchestrator = TDDOrchestrator(task_key=thread_id)

    try:
        final_state = orchestrator.run(specification=specification, requirements=requirements)
    except KeyboardInterrupt:
        print("\n🛑 Execução TDD interrompida pelo usuário de forma segura.")
        sys.exit(0)

    # ── Token Usage Report ────────────────────────────────────────────────────
    _write_token_report(artifacts_dir, orchestrator)

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
        plan_path = os.path.join(artifacts_dir, "planner.txt")
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write("PLANO DE SUB-REQUISITOS TDD\n")
            f.write("=" * 80 + "\n")
            for i, item in enumerate(plan):
                f.write(f"{i+1}. {item}\n")
            f.write("=" * 80 + "\n")

    # 3. Extrai as especificações do Engenheiro
    spec_path = os.path.join(artifacts_dir, "engineer_specifications.txt")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write("ESPECIFICAÇÕES TÉCNICAS DO ENGENHEIRO\n")
        f.write("=" * 80 + "\n")
        f.write(specification)

    # 4. Extrai o histórico dos requisitos validados
    reqs_path = os.path.join(artifacts_dir, "confirmed_user_requirements.txt")
    with open(reqs_path, "w", encoding="utf-8") as f:
        f.write("REQUISITOS ENVIADOS PELO USUÁRIO\n")
        f.write("=" * 80 + "\n")
        f.write(formatted_requirements + "\n")
        f.write("=" * 80 + "\n")

    # 5. gerar relatório de métricas de resiliência
    total_failures = final_state.get("total_detected_failures", 0)
    corrected_failures = final_state.get("autonomously_corrected_failures", 0)
    test_faults = final_state.get("test_faults", 0)
    implementation_faults = final_state.get("implementation_faults", 0)
    write_resilience_metrics(artifacts_dir, total_failures, corrected_failures, test_faults, implementation_faults)

    # 6. gerar relatório de sucesso/falha por sub-requisito
    subreq_success = final_state.get("subreq_success_count", 0)
    subreq_failure = final_state.get("subreq_failure_count", 0)
    subreq_results = final_state.get("subreq_results", [])
    write_pass_rate_report(artifacts_dir, plan, subreq_results, subreq_success, subreq_failure, failed)

    print("📁 Todos os artefatos e logs foram salvos com sucesso!")

    if failed:
        print(f"\n⚠️  {len(failed)} requisito(s) não puderam ser satisfeitos:")
        for req in failed:
            print(f"   • {req.get('requirement', req)}")

    print(f"\n🔑 Thread ID desta execução: {thread_id}")
    print("   (Para inspecionar o estado ou continuar futuramente, use --thread-id)\n")


if __name__ == "__main__":
    main()