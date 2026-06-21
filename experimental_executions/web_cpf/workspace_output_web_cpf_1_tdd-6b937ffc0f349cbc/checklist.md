# Checklist de Avaliação — API de Validação de CPF

## Arquitetura e Organização de Código

- [x] **Tecnologia Base:** A aplicação foi projetada e implementada utilizando Python e o framework FastAPI.
- [x] **Clean Architecture:** O projeto segue rigorosamente os princípios de Clean Architecture.
- [x] **Separação de Camadas:** Existe uma separação clara de camadas, abrangendo obrigatoriamente os diretórios `src` e `tests`.
- [x] **Qualidade Estrutural:** O código demonstra baixo acoplamento e alta coesão.
- [x] **Injeção de Dependência:** O sistema faz uso explícito de injeção de dependência para instanciar componentes.
- [x] **Isolamento de Lógica:** As rotas (controladores) não contêm nenhuma lógica de negócio.
- [x] **Diretório `domain`:** Existe um módulo `domain` contendo apenas as regras de negócio e entidades puras.
- [x] **Diretório `application`:** Existe um módulo `application` dedicado exclusivamente aos casos de uso.
- [x] **Diretório `infrastructure`:** Existe um módulo `infrastructure` para integrações externas, utilizando bibliotecas específicas como `validate-docbr` ou `python-cpf`.
- [x] **Diretório `interfaces`/`presentation`:** Existe um módulo `interfaces` ou `presentation` que contém os controladores do FastAPI.

---

## Contrato da API e Especificações de Endpoint

- [x] **Endpoint de Validação:** A aplicação expõe um endpoint acessível via método `POST` na rota `/validate-cpf`.
- [x] **Payload de Entrada:** O endpoint aceita um payload em formato JSON contendo o campo `cpf` estritamente tipado como `string`.
- [x] **Status de Sucesso:** Requisições válidas retornam o status HTTP `200`.
- [x] **Corpo de Resposta (Sucesso):** O retorno para requisições válidas é um JSON no formato `{ "valid": true }` (ou `false`, a depender da verificação).
- [x] **Validação de Payload:** A aplicação utiliza o Pydantic para validação automática do payload de entrada.
- [x] **Status de Erro:** Requisições com payloads inválidos retornam o status HTTP `422`.
- [x] **Documentação Automática:** A API possui suporte à documentação automática gerada via OpenAPI/Swagger.

---

## Lógica de Negócio (Validação de CPF)

- [x] **Biblioteca Confiável:** A verificação de validade do CPF é delegada a uma biblioteca de terceiros confiável.
- [x] **Suporte a Máscaras:** O sistema processa e aceita CPFs com máscara (ex: `123.456.789-09`).
- [x] **Suporte a Formato Bruto:** O sistema processa e aceita CPFs sem máscara (ex: `12345678909`).
- [x] **Dígitos Verificadores:** O algoritmo valida corretamente se os dígitos verificadores do CPF informado estão matematicamente corretos.
- [x] **Rejeição de Entradas Inválidas:** O sistema rejeita explicitamente strings vazias.
- [x] **Rejeição de Campos Ausentes/Nulos:** O sistema rejeita requisições onde o campo `cpf` está ausente ou possui valor nulo.
- [x] **Rejeição de Caracteres Inesperados:** O sistema rejeita CPFs que contenham caracteres não numéricos inesperados (letras, símbolos não pertencentes à máscara).

---

## Testes (TDD)

- [x] **Abordagem TDD:** O desenvolvimento evidencia o uso de Test-Driven Development (observável pelo Pass Rate e integridade do TDD nos logs da arquitetura multi-agente).
- [ ] **Escopo Unitário:** Foram implementados exclusivamente testes unitários (sem testes de integração ou E2E). | Existe `tests/integration/test_api_validate_cpf.py` com testes de integração assíncronos via `httpx.AsyncClient`.
- [x] **Framework de Teste:** O framework utilizado para os testes é o `pytest`.
- [ ] **Espelhamento de Diretórios:** A estrutura de pastas e arquivos dentro do diretório `tests` espelha perfeitamente a estrutura arquitetural do diretório `src`. | Existe `tests/integration/` que não tem correspondente em `src/`. Além disso, `tests/integration/` não possui `__init__.py`.
- [x] **Cobertura de Cenários:** Os testes cobrem todos os cenários relevantes especificados (CPFs válidos, inválidos, com/sem máscara, payloads vazios, nulos, etc).

---

## DevOps, Infraestrutura e Boas Práticas

- [ ] **Docker Development:** Existe configuração completa de Docker voltada para ambiente de desenvolvimento. | Nenhum `Dockerfile` ou `Dockerfile.dev` encontrado no projeto.
- [ ] **Recursos de Dev:** A configuração de desenvolvimento inclui hot reload e volume mount. | Sem Docker, não há configuração de hot reload/volume mount via container.
- [ ] **Docker Production:** Existe configuração completa de Docker voltada para produção via `Dockerfile`. | Nenhum `Dockerfile` encontrado.
- [ ] **Recursos de Prod:** A imagem de produção é otimizada e construída através de um multi-stage build. | Nenhum `Dockerfile` encontrado.
- [ ] **Orquestração _(Opcional)_:** Existe, opcionalmente, um arquivo `docker-compose`. | Nenhum `docker-compose.yml` ou `docker-compose.yaml` encontrado.
- [ ] **Pipeline CI:** Existe um pipeline de Continuous Integration configurado via GitHub Actions. | Nenhum diretório `.github/workflows/` ou arquivo de pipeline encontrado.
- [ ] **Etapas do CI:** O pipeline realiza a instalação de dependências, execução da suíte de testes e linting. | Sem pipeline CI.
- [ ] **Ferramenta de Linting:** O linting no pipeline CI é executado especificamente com o `flake8`. | Existe `.flake8` com configuração, mas não há pipeline CI que o execute.
- [ ] **Build de Imagem CI _(Opcional)_:** O pipeline opcionalmente realiza o build da imagem Docker. | Sem pipeline CI.
- [x] **Boas Práticas Gerais:** O código fonte faz uso de tipagem estática do Python e segue princípios de código limpo.
- [x] **Gestão de Variáveis:** O projeto utiliza um arquivo `.env` para gestão de ambiente.
