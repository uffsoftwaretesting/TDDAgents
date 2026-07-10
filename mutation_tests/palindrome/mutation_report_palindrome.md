# Relatório de Testes de Mutação - TDDAgents (Palindrome)

Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais dos verificadores de palíndromos.

## 📊 Resumo das Execuções

Os testes de mutação foram executados em três workspaces de execuções experimentais distintas:

| Métrica | Workspace 1 (`..._1_tdd-6d5197f...`) | Workspace 2 (`..._2_tdd-8c06d0a...`) | Workspace 3 (`..._3_tdd-404b83d...`) |
| :--- | :---: | :---: | :---: |
| **Total de Mutantes** | 22 | 38 | 26 |
| **Killed (Mortos)** | 20 (90.91%) | 35 (92.11%) | 21 (80.77%) |
| **Survived (Sobreviventes)** | 2 (9.09%) | 3 (7.89%) | 5 (19.23%) |
| **Timeouts** | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| **Score de Mutação Real*** | **95.45%** | **97.37%** | **100.00%** |

\*O Score de Mutação Real desconta os mutantes sobreviventes que são funcionalmente equivalentes ou que afetam apenas mensagens de exceção não verificadas pelos testes.

---

## 💡 Análise Geral e Lacunas Encontradas

### 1. Mutantes Semânticos Equivalentes (Todos os Workspaces)
Vários mutantes sobreviventes são funcionalmente equivalentes ao comportamento original do programa:
- **Laço Inclusivo** (`while i <= j` ou `while left <= right`): Nos **Workspaces 1 e 2**, a alteração do operador de `<` para `<=` no laço principal do algoritmo de dois ponteiros sobreviveu. Quando `left == right`, o caractere comparado é o caractere central de uma string de comprimento ímpar (que é sempre igual a si mesmo). Embora execute uma verificação redundante adicional, o resultado lógico final é idêntico para qualquer entrada.
- **Filtro de Diacríticos Redundante (Workspace 3)**: A mutação que corrompe a verificação da categoria `Mn` (`unicodedata.category(char) == 'Mn'`) para outros valores sobreviveu. Isso ocorre porque o laço logo em seguida aplica o filtro `char.isalnum()`. Como caracteres combinantes de acentuação (diacríticos) não são alfanuméricos, eles acabam sendo descartados pelo segundo filtro de qualquer forma, tornando a checagem explícita de `Mn` redundante.
- **Caixa Alta vs Caixa Baixa (Workspace 3)**: A alteração de `.lower()` para `.upper()` na normalização de caracteres sobreviveu porque toda a string é convertida uniformemente para maiúsculas e comparada com o seu reverso. A consistência da caixa garante o mesmo resultado para todas as strings.
- **Junção com Separador Simétrico (Workspace 3)**: O mutante que junta a lista de caracteres utilizando `'XXXX'` em vez de `''` sobreviveu. Como o caractere separador é perfeitamente simétrico (`'XXXX'`), a string resultante `c1 S c2 S ... S cn` permanece um palíndromo se e somente se a sequência de caracteres original era um palíndromo.

### 2. Lacunas de Teste nos Casos de Borda (Workspaces 1 e 2)
Nos **Workspaces 1 e 2**, a alteração que corrompe o decremento do ponteiro direito (`j = 1` ou `right = 1` no lugar de `j -= 1` ou `right -= 1`) sobreviveu. Isso indica um ponto cego nas suítes de teste: todas as strings não-palíndromas testadas diferem logo em seu primeiro e último caractere (ex: `'hello'`, `'abc123'`), o que faz com que a função retorne `False` logo na primeira iteração do laço. Se os testes tivessem incluído não-palíndromos que começam e terminam com o mesmo caractere (como `'abca'` ou `'radir'`), este mutante teria sido morto.

---

## 🔍 Detalhes - Workspace 1

- **Total de Mutantes:** 22
- **Killed:** 20
- **Survived:** 2
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>palindrome_checker.x_is_palindrome__mutmut_10</code> (survived)</summary>

```diff
# palindrome_checker.x_is_palindrome__mutmut_10: survived
--- src/palindrome_checker.py
+++ src/palindrome_checker.py
@@ -9,7 +9,7 @@
 
     # Two-pointer palindrome check
     i, j = 0, len(normalized) - 1
-    while i < j:
+    while i <= j:
         if normalized[i] != normalized[j]:
             return False
         i += 1

```

</details>

<details>
<summary><code>palindrome_checker.x_is_palindrome__mutmut_16</code> (survived)</summary>

```diff
# palindrome_checker.x_is_palindrome__mutmut_16: survived
--- src/palindrome_checker.py
+++ src/palindrome_checker.py
@@ -13,5 +13,5 @@
         if normalized[i] != normalized[j]:
             return False
         i += 1
-        j -= 1
+        j = 1
     return True

```

</details>


---

## 🔍 Detalhes - Workspace 2

- **Total de Mutantes:** 38
- **Killed:** 35
- **Survived:** 3
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>palindrome.x_is_palindrome__mutmut_12</code> (survived)</summary>

```diff
# palindrome.x_is_palindrome__mutmut_12: survived
--- src/palindrome.py
+++ src/palindrome.py
@@ -8,7 +8,7 @@
 
     # Verificação de palíndromo usando dois ponteiros
     left, right = 0, len(normalized) - 1
-    while left < right:
+    while left <= right:
         if normalized[left] != normalized[right]:
             return False
         left += 1

```

</details>

<details>
<summary><code>palindrome.x_is_palindrome__mutmut_18</code> (survived)</summary>

```diff
# palindrome.x_is_palindrome__mutmut_18: survived
--- src/palindrome.py
+++ src/palindrome.py
@@ -12,5 +12,5 @@
         if normalized[left] != normalized[right]:
             return False
         left += 1
-        right -= 1
+        right = 1
     return True

```

</details>

<details>
<summary><code>palindrome.x__is_palindrome_core__mutmut_5</code> (survived)</summary>

```diff
# palindrome.x__is_palindrome_core__mutmut_5: survived
--- src/palindrome.py
+++ src/palindrome.py
@@ -1,7 +1,7 @@
 def _is_palindrome_core(s: str) -> bool:
     # Verificação de palíndromo usando dois ponteiros em string já normalizada ou não
     left, right = 0, len(s) - 1
-    while left < right:
+    while left <= right:
         if s[left] != s[right]:
             return False
         left += 1

```

</details>


---

## 🔍 Detalhes - Workspace 3

- **Total de Mutantes:** 26
- **Killed:** 21
- **Survived:** 5
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>palindrome.x_is_palindrome__mutmut_15</code> (survived)</summary>

```diff
# palindrome.x_is_palindrome__mutmut_15: survived
--- src/palindrome.py
+++ src/palindrome.py
@@ -4,7 +4,7 @@
     # Decompose Unicode and strip diacritics
     normalized_chars = []
     for char in unicodedata.normalize('NFD', s):
-        if unicodedata.category(char) == 'Mn':
+        if unicodedata.category(char) == 'XXMnXX':
             continue
         if char.isalnum():
             normalized_chars.append(char.lower())

```

</details>

<details>
<summary><code>palindrome.x_is_palindrome__mutmut_16</code> (survived)</summary>

```diff
# palindrome.x_is_palindrome__mutmut_16: survived
--- src/palindrome.py
+++ src/palindrome.py
@@ -4,7 +4,7 @@
     # Decompose Unicode and strip diacritics
     normalized_chars = []
     for char in unicodedata.normalize('NFD', s):
-        if unicodedata.category(char) == 'Mn':
+        if unicodedata.category(char) == 'mn':
             continue
         if char.isalnum():
             normalized_chars.append(char.lower())

```

</details>

<details>
<summary><code>palindrome.x_is_palindrome__mutmut_17</code> (survived)</summary>

```diff
# palindrome.x_is_palindrome__mutmut_17: survived
--- src/palindrome.py
+++ src/palindrome.py
@@ -4,7 +4,7 @@
     # Decompose Unicode and strip diacritics
     normalized_chars = []
     for char in unicodedata.normalize('NFD', s):
-        if unicodedata.category(char) == 'Mn':
+        if unicodedata.category(char) == 'MN':
             continue
         if char.isalnum():
             normalized_chars.append(char.lower())

```

</details>

<details>
<summary><code>palindrome.x_is_palindrome__mutmut_20</code> (survived)</summary>

```diff
# palindrome.x_is_palindrome__mutmut_20: survived
--- src/palindrome.py
+++ src/palindrome.py
@@ -7,7 +7,7 @@
         if unicodedata.category(char) == 'Mn':
             continue
         if char.isalnum():
-            normalized_chars.append(char.lower())
+            normalized_chars.append(char.upper())
     normalized = ''.join(normalized_chars)
     # Compare with reverse
     return normalized == normalized[::-1]

```

</details>

<details>
<summary><code>palindrome.x_is_palindrome__mutmut_23</code> (survived)</summary>

```diff
# palindrome.x_is_palindrome__mutmut_23: survived
--- src/palindrome.py
+++ src/palindrome.py
@@ -8,6 +8,6 @@
             continue
         if char.isalnum():
             normalized_chars.append(char.lower())
-    normalized = ''.join(normalized_chars)
+    normalized = 'XXXX'.join(normalized_chars)
     # Compare with reverse
     return normalized == normalized[::-1]

```

</details>


---

