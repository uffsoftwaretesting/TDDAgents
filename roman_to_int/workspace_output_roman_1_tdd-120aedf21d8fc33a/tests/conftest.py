import sys, os

# Adiciona o diretório src ao PYTHONPATH para que o pytest consiga importar o pacote
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))