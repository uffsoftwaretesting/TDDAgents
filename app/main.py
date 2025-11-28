import os
import asyncio
import logging
from typing import List, Optional
from dotenv import load_dotenv

# --- AutoGen Imports ---
from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination

# --- LangGraph Orchestrator Imports ---
from app.orchestrator import TDDOrchestrator 

# ==============================================================================
# 🔧 CONFIGURAÇÃO DE LOGS (CORREÇÃO DO RUÍDO)
# ==============================================================================
# 1. Definimos o nível global para WARNING (esconde INFO de libs externas)
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

# 2. Silenciamos explicitamente bibliotecas conhecidas por serem barulhentas
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("autogen_core").setLevel(logging.ERROR)
logging.getLogger("autogen_agentchat").setLevel(logging.ERROR)

# 3. Criamos um logger específico para A NOSSA aplicação com nível INFO
logger = logging.getLogger("MeuApp")
logger.setLevel(logging.INFO)
# ==============================================================================

async def run_requirements_gathering() -> Optional[str]:
    """
    Executa a camada de refinamento de requisitos com AutoGen.
    Retorna a especificação refinada (prompt) ou None se falhar.
    """
    load_dotenv()
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise ValueError("API Key ausente no .env")

    model_client = OpenAIChatCompletionClient(model="gpt-4o", api_key=api_key)

    # ---------------------------------------------------------
    # 1. AGENTE: ANALISTA DE REQUISITOS
    # ---------------------------------------------------------
    analyst_system_msg = """
    Você é um Analista de Requisitos de Software Sênior. Siga estritamente este fluxo:
    
    1. Receba a solicitação inicial do usuário.
    2. NÃO assuma nada. NÃO invente requisitos.
    3. Analise a ambiguidade. Se houver qualquer dúvida (formato de entrada, tipos de dados, edge cases, limitações), FAÇA PERGUNTAS ao usuário.
    4. Faça perguntas curtas e diretas, uma ou duas por vez.
    5. Continue perguntando até ter certeza absoluta do que deve ser construído.
    6. Quando tiver detalhes suficientes, apresente uma lista numerada ("Checklist de Requisitos") para o usuário EXATAMENTE com a frase: "Posso prosseguir?".
    
    IMPORTANTE: 
    - NUNCA agradeça ou encerre a conversa.
    - Se o usuário confirmar, NÃO FALE MAIS NADA. O sistema passará a vez para o Engenheiro.
    """
    
    analyst = AssistantAgent(
        name="Analista",
        model_client=model_client,
        system_message=analyst_system_msg,
        description="Analista que faz perguntas para esclarecer requisitos vagos."
    )

    # ---------------------------------------------------------
    # 2. AGENTE: ENGENHEIRO DE SPEC (PROMPT ENGINEER)
    # ---------------------------------------------------------
    engineer_system_msg = """
    Você é um Engenheiro de Especificação Técnica e Prompt Engineer Especialista.
    Sua única função é receber os requisitos validados pelo Analista e transformá-los em um Prompt de Especificação Técnica altamente formal e estruturado para um sistema TDD.
    Sua vez chega IMEDIATAMENTE após o usuário confirmar que está satisfeito com os requisitos.

    FORMATO OBRIGATÓRIO DE SAÍDA (Use Markdown):
    
    # [Nome da Função]
    
    ⚙️ DEFINIÇÃO:
    [Descrição concisa e técnica do problema e do objetivo, em estilo acadêmico].
    
    ⚠️ REQUISITOS FUNCIONAIS & RESTRIÇÕES:
    1. [Requisito explícito]
    2. [Tratamento de erro ou edge case]
    3. [Restrição técnica, ex: complexidade O(n), bibliotecas permitidas]
    
    💡 CASOS DE TESTE (Doctest style):
    >>> [chamada_funcao]
    [resultado_esperado]
    
    REGRAS FINAIS:
    - Seja exaustivo nos requisitos.
    - Se o usuário não especificou tratamento de erro, defina o padrão mais seguro (ex: raise ValueError).
    - Após gerar a especificação, NÃO escreva mais nada.
    - Finalize sua resposta EXATAMENTE com a string exata: "TERMINATE_SPEC"
    """

    engineer = AssistantAgent(
        name="Engenheiro_Spec",
        model_client=model_client,
        system_message=engineer_system_msg,
        description="Engenheiro que cria o prompt formal final baseada nos requisitos aprovados."
    )

    # ---------------------------------------------------------
    # 3. AGENTE: USER PROXY
    # ---------------------------------------------------------
    user_proxy = UserProxyAgent(
        name="Usuario",
        input_func=lambda _: input(f"\n[Sua Resposta]: "),
        description="Usuário humano que fornece os requisitos e responde dúvidas."
    )

    # ---------------------------------------------------------
    # 4. ORQUESTRAÇÃO (SELECTOR)
    # ---------------------------------------------------------
    selector_prompt = """
    Você é o gerente de fluxo. Siga esta lógica de transição estrita:
    
    1. Início ou Dúvidas -> Selecione 'Analista'.
    2. Analista fez uma pergunta -> Selecione 'Usuario' (para responder).
    3. Analista propôs o Checklist de Requisitos -> Selecione 'Usuario' (para confirmar).
    4. Usuario disse "sim", "ok", "confirmado", "pode prosseguir" ou qualquer frase de confirmação ao receber a pergunta de "posso prosseguir?" do agente Analista para o Checklist -> Selecione 'Engenheiro_Spec'.
    5. Usuario adicionou novos detalhes ou negou -> Selecione 'Analista'.
    6. Engenheiro_Spec gerou a especificação final -> TERMINATE.
    """

    termination = TextMentionTermination("TERMINATE_SPEC") | MaxMessageTermination(50)

    team = SelectorGroupChat(
        [analyst, engineer, user_proxy],
        model_client=model_client,
        selector_prompt=selector_prompt,
        termination_condition=termination
    )

    print("\n" + "="*60)
    print("🤖 INICIANDO CAMADA DE LEVANTAMENTO DE REQUISITOS (AUTOGEN)")
    print("="*60)
    
    task_input = input("Descreva o que você deseja programar (ex: 'Quero um validador de CPF'): ")
    
    final_specification = ""
    
    async for message in team.run_stream(task=task_input):
        if isinstance(message, TextMessage):
            print(f"\n[{message.source}]: {message.content}")
            
            if message.source == "Engenheiro_Spec":
                final_specification = message.content.replace("TERMINATE_SPEC", "").strip()

    return final_specification

def main():
    try:
        refined_spec = asyncio.run(run_requirements_gathering())
    except KeyboardInterrupt:
        print("\nOperação cancelada pelo usuário.")
        return

    if not refined_spec:
        logger.error("❌ Falha ao gerar especificação ou fluxo interrompido.")
        return

    print("\n" + "="*60)
    print("🔄 TRANSFERINDO CONTEXTO: AUTOGEN --> LANGGRAPH")
    print("="*60)
    print(f"📜 Especificação Gerada:\n{refined_spec[:200]}...\n(truncada para visualização)")
    
    function_name = "generated_function"
    for line in refined_spec.split('\n'):
        if line.strip().startswith("#"):
            parts = line.replace("#", "").strip().split()
            if parts:
                function_name = parts[0].lower().replace(" ", "_")
                break
    
    logger.info(f"Nome da função detectado para TDD: {function_name}")

    # 3. Fase de LangGraph (Execução TDD)
    orchestrator = TDDOrchestrator()
    
    final_state = orchestrator.run(
        specification=refined_spec,
        function_name=function_name,
        resume=False
    )
    
    if final_state.get("status") == "plan_complete":
        print("\n✅ Fluxo Completo com Sucesso!")
    else:
        print(f"\n⚠️ O fluxo terminou com status: {final_state.get('status')}")

if __name__ == "__main__":
    main()
