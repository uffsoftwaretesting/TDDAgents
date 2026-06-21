# Verificador de Palíndromos em Python

![CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg)

Este repositório contém a função `is_palindrome(s: str) -> bool`, que verifica se uma string é um palíndromo após normalização (removendo caracteres não alfanuméricos e case-insensitive). A cada push ou pull request na branch `main`, o CI executa toda a suíte de testes automaticamente.

## Como executar localmente

# Crie um ambiente virtual
python3 -m venv venv
source venv/bin/activate

# Instale pytest
pip install pytest

# Execute os testes
pytest

---

*Desenvolvido seguindo Test-Driven Development (TDD) e integrado via GitHub Actions.*