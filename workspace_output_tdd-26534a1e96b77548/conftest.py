import sys
import os

# Insere src no início do PYTHONPATH para que 'import core.*' funcione
def _prepend_src_to_path():
    # Diretório raiz do projeto (onde este arquivo está localizado)
    project_root = os.path.dirname(__file__)
    src_path = os.path.abspath(os.path.join(project_root, 'src'))
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

_prepend_src_to_path()
