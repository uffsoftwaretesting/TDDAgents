# Roman to Integer Converter

[![CI](https://github.com/your-username/your-repo/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/your-repo/actions/workflows/ci.yml)
[![Coverage Status](https://img.shields.io/badge/coverage-80%25-brightgreen)](#)

Este projeto converte numerais romanos em inteiros, seguindo as regras descritas na documentação interna. O pipeline do GitHub Actions executa automaticamente os testes em cada commit e assegura cobertura mínima de 80%.  

## Como usar

1. Instale as dependências:
   ```bash
   pip install .
   ```
2. Converta um numeral romano para inteiro:
   ```python
   from roman_converter.converter import roman_to_int
   print(roman_to_int("MCMXCIV"))  # imprime 1994
   ```