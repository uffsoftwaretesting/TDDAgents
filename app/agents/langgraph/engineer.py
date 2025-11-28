import os
import logging
from openai import OpenAI
from app.utils.prompt_loader import load_prompt


def generate_specification(requirements: str) -> str:
    """
    Engenheiro que transforma requisitos validados em especificação técnica formal.
    
    Args:
        requirements: Requisitos validados pelo analista
        
    Returns:
        str: Especificação técnica formal
    """
    logging.info("=" * 70)
    logging.info("⚙️ ENGENHEIRO - Gerando especificação técnica formal")
    logging.info("=" * 70)
    
    logging.info(f"📋 Requisitos recebidos: {len(requirements)} caracteres")
    logging.info(f"🔧 Processando com modelo: gpt-4o")
    
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    
    system_prompt = load_prompt(
        template_name='agents/langgraph/engineer/sys_prompt_1.jinja2'
    )
    
    human_prompt = load_prompt(
        template_name='agents/langgraph/engineer/hum_prompt_1.jinja2',
        requirements=requirements
    )
    
    logging.info("🚀 Enviando requisição para OpenAI...")
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": human_prompt}
        ],
        temperature=0,
        max_tokens=1500
    )
    
    content = str(response.choices[0].message.content)
    
    # Remove o token de terminação se presente
    if "TERMINATE_SPEC" in content:
        content = content.replace("TERMINATE_SPEC", "").strip()
        logging.info("✂️ Token TERMINATE_SPEC removido")
    
    logging.info("=" * 70)
    logging.info("📄 ESPECIFICAÇÃO TÉCNICA GERADA:")
    logging.info("=" * 70)
    
    # Exibir a especificação de forma formatada
    lines = content.split('\n')
    for line in lines:
        if line.strip():
            if line.startswith('#'):
                logging.info(f"🏷️  {line}")
            elif line.startswith('⚙️') or line.startswith('⚠️') or line.startswith('💡'):
                logging.info(f"📌 {line}")
            elif line.startswith('>>>'):
                logging.info(f"🧪 {line}")
            elif line.strip().isdigit() or line.strip().startswith(('1.', '2.', '3.', '4.', '5.')):
                logging.info(f"   {line}")
            else:
                logging.info(f"   {line}")
    
    logging.info("=" * 70)
    logging.info(f"✅ Especificação gerada com sucesso! Total: {len(content)} caracteres")
    logging.info("=" * 70)
    
    return content
