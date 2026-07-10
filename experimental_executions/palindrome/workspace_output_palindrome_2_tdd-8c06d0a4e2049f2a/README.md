# Verificador de Palíndromos

Este projeto fornece uma função simples, `is_palindrome`, que verifica se uma string é um palíndromo após normalização (remoção de caracteres não alfanuméricos e conversão para minúsculas).

## Pré-requisitos

- Python 3.7 ou superior
- git (para clonar o repositório)

## Setup do ambiente

1. Clone este repositório:
   ```bash
   git clone <URL_DO_REPOSITORIO>
   cd <NOME_DO_REPOSITORIO>
   ```
2. (Opcional) Crie e ative um ambiente virtual:
   - Linux / macOS:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```
   - Windows (PowerShell):
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```

3. Instale as dependências necessárias:
   ```bash
   pip install pytest
   ```

## Executando os testes

Para executar a suíte de testes com cobertura de código:
```bash
pytest --cov
```

Todos os testes estão localizados no diretório `tests` e a cobertura mínima exigida é 100% para o código em `src`.

## Estrutura do projeto

```
├── README.md
├── pytest.ini       # Configuração do pytest e cobertura
├── .coveragerc      # Regras de cobertura
├── src/
│   └── palindrome.py
└── tests/
    ├── test_palindrome.py
    ├── test_normalize.py
    ├── test_core_palindrome.py
    └── test_palindrome_additional.py
```

## Observações

- Não há I/O nem docstrings adicionais no código-fonte da biblioteca.
- Alterações na lógica de negócio devem manter 100% de cobertura de teste para continuar atendendo aos requisitos de qualidade.
