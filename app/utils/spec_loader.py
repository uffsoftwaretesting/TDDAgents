import os
from app.config.config import Config

def load_spec(filename: str) -> str:
    """Carrega apenas o texto cru (para as specs em markdown)"""
    path = os.path.join(Config.PROMPTS_DIR, 'specs', filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()
