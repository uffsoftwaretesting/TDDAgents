backup/analyze.sh# Projeto de Integração Numérica – Regra do Trapézio Composta

Este projeto implementa a função `solve(f, a, b, n)` para aproximar integrais definidas usando a regra do trapézio composta.

## Como Executar os Testes

1. Instale as dependências de desenvolvimento (em um ambiente virtual):

   ```bash
   pip install -r requirements.txt
   ```

2. Execute os testes com pytest (as flags estão configuradas em `pytest.ini`):

   ```bash
   pytest
   ```

   Por padrão será usado:
   - `--maxfail=1`: interrompe após a primeira falha.
   - `--disable-warnings`: supressão de warnings no output.
   - `--strict-markers`: exige registro estrito de marcadores.

3. Para executar manualmente com flags explícitas:

   ```bash
   pytest --maxfail=1 --disable-warnings --strict-markers
   ```

## Relatório de Cobertura (Opcional)

Para obter relatório de cobertura do módulo `solve`, instale o plugin `pytest-cov`:

```bash
pip install pytest-cov
```

E execute:

```bash
pytest --cov=src/solve.py --cov-report=term-missing
```

O módulo `src/solve.py` possui cobertura de testes completa (100%).
