import sys
import os

# Inserir o diretório src no caminho de importação para que pytest encontre o módulo
project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)
