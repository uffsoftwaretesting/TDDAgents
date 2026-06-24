# Relatório de Testes de Mutação - TDDAgents (Web Markdown to HTML Converter)

Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais do conversor de Markdown para HTML via API web (FastAPI).

Os mutantes foram gerados apenas sobre a **lógica de conversão** de cada execução (serviços de conversão), excluindo o *glue* de framework (FastAPI `main`/rotas, *schemas* e configuração). Testes não comportamentais (configuração, inicialização, importações, OpenAPI/documentação e linting/estrutura) foram desativados durante a execução por não contribuírem para matar mutantes.

## 📊 Resumo das Execuções

| Métrica | Workspace 1 (`..._1_tdd-543084c...`) | Workspace 2 (`..._2_tdd-c7659e1...`) | Workspace 3 (`..._3_tdd-92cbf21...`) |
| :--- | :---: | :---: | :---: |
| **Total de Mutantes** | 5 | 3 | 8 |
| **Killed (Mortos)** | 1 (20.00%) | 3 (100.00%) | 4 (50.00%) |
| **Survived (Sobreviventes)** | 4 (80.00%) | 0 (0.00%) | 4 (50.00%) |
| **Timeouts** | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| **Score de Mutação** | **20.00%** | **100.00%** | **50.00%** |

O Score de Mutação é calculado como `Killed / Total`. Mutantes sobreviventes funcionalmente equivalentes devem ser inspecionados manualmente nas seções de detalhes abaixo.

Os mutantes sobreviventes nos Workspaces 1 e 3 correspondem principalmente a mutações em strings de erro (TypeError ou MarkdownConversionError) lançadas pelas exceções. Como as asserções de teste correspondentes validam apenas o tipo da exceção e não a mensagem exata, esses mutantes sobrevivem. O Workspace 2 obteve 100% de cobertura de mutação (3/3 mortos).

---

## 🔍 Detalhes - Workspace 1

- **Total de Mutantes:** 5
- **Killed:** 1
- **Survived:** 4
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>services.markdown_converter.x_convert__mutmut_2</code> (survived)</summary>

```diff
# services.markdown_converter.x_convert__mutmut_2: survived
--- services/markdown_converter.py
+++ services/markdown_converter.py
@@ -8,4 +8,4 @@
     try:
         return markdown.markdown(markdown_text)
     except Exception as e:
-        raise MarkdownConversionError("Internal conversion error") from e
+        raise MarkdownConversionError(None) from e

```

</details>

<details>
<summary><code>services.markdown_converter.x_convert__mutmut_3</code> (survived)</summary>

```diff
# services.markdown_converter.x_convert__mutmut_3: survived
--- services/markdown_converter.py
+++ services/markdown_converter.py
@@ -8,4 +8,4 @@
     try:
         return markdown.markdown(markdown_text)
     except Exception as e:
-        raise MarkdownConversionError("Internal conversion error") from e
+        raise MarkdownConversionError("XXInternal conversion errorXX") from e

```

</details>

<details>
<summary><code>services.markdown_converter.x_convert__mutmut_4</code> (survived)</summary>

```diff
# services.markdown_converter.x_convert__mutmut_4: survived
--- services/markdown_converter.py
+++ services/markdown_converter.py
@@ -8,4 +8,4 @@
     try:
         return markdown.markdown(markdown_text)
     except Exception as e:
-        raise MarkdownConversionError("Internal conversion error") from e
+        raise MarkdownConversionError("internal conversion error") from e

```

</details>

<details>
<summary><code>services.markdown_converter.x_convert__mutmut_5</code> (survived)</summary>

```diff
# services.markdown_converter.x_convert__mutmut_5: survived
--- services/markdown_converter.py
+++ services/markdown_converter.py
@@ -8,4 +8,4 @@
     try:
         return markdown.markdown(markdown_text)
     except Exception as e:
-        raise MarkdownConversionError("Internal conversion error") from e
+        raise MarkdownConversionError("INTERNAL CONVERSION ERROR") from e

```

</details>


---

## 🔍 Detalhes - Workspace 2

- **Total de Mutantes:** 3
- **Killed:** 3
- **Survived:** 0
- **Timeout:** 0

Não houve mutantes sobreviventes neste workspace.


---

## 🔍 Detalhes - Workspace 3

- **Total de Mutantes:** 8
- **Killed:** 4
- **Survived:** 4
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>app.services.markdown_converter.x_convert_markdown_to_html__mutmut_2</code> (survived)</summary>

```diff
# app.services.markdown_converter.x_convert_markdown_to_html__mutmut_2: survived
--- app/services/markdown_converter.py
+++ app/services/markdown_converter.py
@@ -8,7 +8,7 @@
     :raises MarkdownConversionError: if conversion fails
     """
     if not isinstance(text, str):
-        raise TypeError("Input must be a string")
+        raise TypeError(None)
 
     try:
         html = markdown2.markdown(text)

```

</details>

<details>
<summary><code>app.services.markdown_converter.x_convert_markdown_to_html__mutmut_3</code> (survived)</summary>

```diff
# app.services.markdown_converter.x_convert_markdown_to_html__mutmut_3: survived
--- app/services/markdown_converter.py
+++ app/services/markdown_converter.py
@@ -8,7 +8,7 @@
     :raises MarkdownConversionError: if conversion fails
     """
     if not isinstance(text, str):
-        raise TypeError("Input must be a string")
+        raise TypeError("XXInput must be a stringXX")
 
     try:
         html = markdown2.markdown(text)

```

</details>

<details>
<summary><code>app.services.markdown_converter.x_convert_markdown_to_html__mutmut_4</code> (survived)</summary>

```diff
# app.services.markdown_converter.x_convert_markdown_to_html__mutmut_4: survived
--- app/services/markdown_converter.py
+++ app/services/markdown_converter.py
@@ -8,7 +8,7 @@
     :raises MarkdownConversionError: if conversion fails
     """
     if not isinstance(text, str):
-        raise TypeError("Input must be a string")
+        raise TypeError("input must be a string")
 
     try:
         html = markdown2.markdown(text)

```

</details>

<details>
<summary><code>app.services.markdown_converter.x_convert_markdown_to_html__mutmut_5</code> (survived)</summary>

```diff
# app.services.markdown_converter.x_convert_markdown_to_html__mutmut_5: survived
--- app/services/markdown_converter.py
+++ app/services/markdown_converter.py
@@ -8,7 +8,7 @@
     :raises MarkdownConversionError: if conversion fails
     """
     if not isinstance(text, str):
-        raise TypeError("Input must be a string")
+        raise TypeError("INPUT MUST BE A STRING")
 
     try:
         html = markdown2.markdown(text)

```

</details>


---

