# Relatório de Testes de Mutação - TDDAgents (CEP Validator)

Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais dos validadores/formatadores de CEP.

## 📊 Resumo das Execuções

Os testes de mutação foram executados em três workspaces de execuções experimentais distintas:

| Métrica | Workspace 1 (`..._1_tdd-2aeecb4...`) | Workspace 2 (`..._2_tdd-0669222...`) | Workspace 3 (`..._3_tdd-cdbc4ce...`) |
| :--- | :---: | :---: | :---: |
| **Total de Mutantes** | 23 | 26 | 23 |
| **Killed (Mortos)** | 23 (100.00%) | 26 (100.00%) | 23 (100.00%) |
| **Survived (Sobreviventes)** | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| **Timeouts** | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| **Score de Mutação Real*** | **100%** | **100%** | **100%** |

\*O Score de Mutação Real desconta os mutantes sobreviventes que são funcionalmente equivalentes ou que afetam apenas mensagens de exceção não verificadas pelos testes.

---

## 💡 Análise Geral e Lacunas Encontradas

### 1. Robustez Excepcional dos Testes Unitários
Diferente do observado em outros problemas (como `temperature_conversion` e `roman_to_int`), todas as suítes de testes geradas pelo TDDAgents para o validador de CEP atingiram **100% de Score de Mutação (bruto e real)** em todos os três workspaces experimentais na primeira execução bem-sucedida.

Isso se deve aos seguintes fatores fundamentais:
- **Validação de Mensagens de Erro**: Os testes gerados pelos agentes verificaram explicitamente o conteúdo das strings de exceções (`ValueError` e `TypeError`) usando asserts como `assert str(exc.value) == "..."`. Com isso, qualquer tentativa do `mutmut` em corromper os textos de erro foi imediatamente detectada e matada.
- **Tratamento de Zeros à Esquerda e Inteiros**: Casos envolvendo o preenchimento de zeros à esquerda (`zfill` em inteiros/strings de tamanho menor que 8 ou formatação de zeros totais como `"00000000"`) foram amplamente cobertos por casos de teste parametrizados em todos os workspaces.
- **Detecção de Tipos Inválidos**: Mutações direcionadas às validações de tipos (ex. checagem com `isinstance(cep, (str, int))`) foram prontamente capturadas, pois os testes enviavam sistematicamente tipos inválidos (`None`, floats, listas, objetos genéricos).

Como resultado de tal cobertura rigorosa, não houve qualquer mutante sobrevivente ou em timeout em nenhuma das três workspaces sob análise.

---

## 🔍 Detalhes - Workspace 1

- **Total de Mutantes:** 23
- **Killed:** 23
- **Survived:** 0
- **Timeout:** 0

Não houve mutantes sobreviventes neste workspace.


---

## 🔍 Detalhes - Workspace 2

- **Total de Mutantes:** 26
- **Killed:** 26
- **Survived:** 0
- **Timeout:** 0

Não houve mutantes sobreviventes neste workspace.


---

## 🔍 Detalhes - Workspace 3

- **Total de Mutantes:** 23
- **Killed:** 23
- **Survived:** 0
- **Timeout:** 0

Não houve mutantes sobreviventes neste workspace.


---

