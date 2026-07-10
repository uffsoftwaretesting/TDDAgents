# Relatório de Testes de Mutação - TDDAgents (Roman to Int)

Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais dos conversores de algarismos romanos para inteiros.

## 📊 Resumo das Execuções

Os testes de mutação foram executados em três workspaces de execuções experimentais distintas:

| Métrica | Workspace 1 (`..._1_tdd-120aedf...`) | Workspace 2 (`..._2_tdd-49425bb...`) | Workspace 3 (`..._3_tdd-b914c08...`) |
| :--- | :---: | :---: | :---: |
| **Total de Mutantes** | 122 | 158 | 80 |
| **Killed (Mortos)** | 106 (86.89%) | 130 (82.28%) | 61 (76.25%) |
| **Survived (Sobreviventes)** | 14 (11.48%) | 28 (17.72%) | 19 (23.75%) |
| **Timeouts** | 2 (1.64%) | 0 (0.00%) | 0 (0.00%) |
| **Score de Mutação Real*** | **100%** | **99.37%** | **100%** |

\*O Score de Mutação Real desconta os mutantes sobreviventes que são funcionalmente equivalentes ou que afetam apenas mensagens de exceção não verificadas pelos testes.

---

## 💡 Análise Geral e Lacunas Encontradas

### 1. Mensagens de Exceção não Verificadas
A grande maioria dos mutantes sobreviventes altera strings dentro de `raise ValueError(...)` ou `raise TypeError(...)`. Como as suítes de testes geradas apenas validam se a exceção correta foi disparada (ex: `with pytest.raises(ValueError)`), alterações no texto da exceção não quebram os testes.

### 2. Mutantes Equivalentes
Mutações de redundância lógica (como alterar `prev_char = None` para `prev_char = ""` ou inicializar contadores internos com valores que são imediatamente sobrescritos na primeira iteração) comportam-se de forma idêntica ao código original e são mutantes equivalentes.

### 3. Ponto Cego em Workspace 2 (Valor de 'C')
Em **Workspace 2**, a mutação `'C': 100` para `'C': 101` sobreviveu porque os únicos números de teste contendo `'C'` foram `MCMXCIV` (1994) e `MMMCMXCIX` (3999), onde `'C'` aparece duas vezes de forma a se autoanular (-101 e +101). Um teste simples como `'C'` esperando 100 mataria essa mutação.


---

## 🔍 Detalhes - Workspace 1

- **Total de Mutantes:** 122
- **Killed:** 106
- **Survived:** 14
- **Timeout:** 2

### Mutantes Sobreviventes

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_2</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_2: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -7,7 +7,7 @@
     """
     # Empty string check
     if not s:
-        raise ValueError("Input string is empty")
+        raise ValueError(None)
 
     # Normalize to uppercase
     s = s.upper()

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_3</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_3: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -7,7 +7,7 @@
     """
     # Empty string check
     if not s:
-        raise ValueError("Input string is empty")
+        raise ValueError("XXInput string is emptyXX")
 
     # Normalize to uppercase
     s = s.upper()

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_4</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_4: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -7,7 +7,7 @@
     """
     # Empty string check
     if not s:
-        raise ValueError("Input string is empty")
+        raise ValueError("input string is empty")
 
     # Normalize to uppercase
     s = s.upper()

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_5</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_5: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -7,7 +7,7 @@
     """
     # Empty string check
     if not s:
-        raise ValueError("Input string is empty")
+        raise ValueError("INPUT STRING IS EMPTY")
 
     # Normalize to uppercase
     s = s.upper()

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_31</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_31: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -26,7 +26,7 @@
     # Validate characters
     for ch in s:
         if ch not in values:
-            raise ValueError(f"Invalid Roman numeral character: {ch}")
+            raise ValueError(None)
 
     # Validate repetitions
     prev = None

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_32</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_32: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -29,7 +29,7 @@
             raise ValueError(f"Invalid Roman numeral character: {ch}")
 
     # Validate repetitions
-    prev = None
+    prev = ""
     count = 0
     for ch in s:
         if ch == prev:

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_33</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_33: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -30,7 +30,7 @@
 
     # Validate repetitions
     prev = None
-    count = 0
+    count = None
     for ch in s:
         if ch == prev:
             count += 1

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_34</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_34: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -30,7 +30,7 @@
 
     # Validate repetitions
     prev = None
-    count = 0
+    count = 1
     for ch in s:
         if ch == prev:
             count += 1

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_50</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_50: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -38,7 +38,7 @@
             prev = ch
             count = 1
         # Symbols that can repeat up to 3 times
-        if ch in ('I', 'X', 'C', 'M') and count > 3:
+        if ch in ('I', 'X', 'C', 'XXMXX') and count > 3:
             raise ValueError(f"Too many repeats of symbol: {ch}")
         # Symbols that cannot repeat
         if ch in ('V', 'L', 'D') and count > 1:

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_51</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_51: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -38,7 +38,7 @@
             prev = ch
             count = 1
         # Symbols that can repeat up to 3 times
-        if ch in ('I', 'X', 'C', 'M') and count > 3:
+        if ch in ('I', 'X', 'C', 'm') and count > 3:
             raise ValueError(f"Too many repeats of symbol: {ch}")
         # Symbols that cannot repeat
         if ch in ('V', 'L', 'D') and count > 1:

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_54</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_54: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -39,7 +39,7 @@
             count = 1
         # Symbols that can repeat up to 3 times
         if ch in ('I', 'X', 'C', 'M') and count > 3:
-            raise ValueError(f"Too many repeats of symbol: {ch}")
+            raise ValueError(None)
         # Symbols that cannot repeat
         if ch in ('V', 'L', 'D') and count > 1:
             raise ValueError(f"Invalid repetition of symbol: {ch}")

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_65</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_65: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -42,7 +42,7 @@
             raise ValueError(f"Too many repeats of symbol: {ch}")
         # Symbols that cannot repeat
         if ch in ('V', 'L', 'D') and count > 1:
-            raise ValueError(f"Invalid repetition of symbol: {ch}")
+            raise ValueError(None)
 
     # Allowed subtractive combinations
     allowed_subtractive = {

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_105</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_105: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -61,7 +61,7 @@
             nxt = s[i + 1]
             # Validate allowed subtractive
             if curr not in allowed_subtractive or nxt not in allowed_subtractive[curr]:
-                raise ValueError(f"Invalid subtractive combination: {curr}{nxt}")
+                raise ValueError(None)
             total += values[nxt] - values[curr]
             i += 2
         else:

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_122</code> (survived)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_122: survived
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -70,6 +70,6 @@
 
     # Validate result range
     if total < 1 or total > 3999:
-        raise ValueError(f"Result out of range: {total}")
+        raise ValueError(None)
 
     return total

```

</details>

### Mutantes com Timeout

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_109</code> (timeout)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_109: timeout
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -63,7 +63,7 @@
             if curr not in allowed_subtractive or nxt not in allowed_subtractive[curr]:
                 raise ValueError(f"Invalid subtractive combination: {curr}{nxt}")
             total += values[nxt] - values[curr]
-            i += 2
+            i = 2
         else:
             total += values[s[i]]
             i += 1

```

</details>

<details>
<summary><code>roman_converter.converter.x_roman_to_int__mutmut_114</code> (timeout)</summary>

```diff
# roman_converter.converter.x_roman_to_int__mutmut_114: timeout
--- src/roman_converter/converter.py
+++ src/roman_converter/converter.py
@@ -66,7 +66,7 @@
             i += 2
         else:
             total += values[s[i]]
-            i += 1
+            i = 1
 
     # Validate result range
     if total < 1 or total > 3999:

```

</details>


---

## 🔍 Detalhes - Workspace 2

- **Total de Mutantes:** 158
- **Killed:** 130
- **Survived:** 28
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>roman_converter.converter.x_validate_characters__mutmut_3</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_characters__mutmut_3: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -3,7 +3,7 @@
     Validates that each character in the string is a valid Roman numeral symbol.
     Raises ValueError on the first invalid character.
     """
-    allowed = set("IVXLCDM")
+    allowed = set("XXIVXLCDMXX")
     for c in s:
         if c not in allowed:
             raise ValueError(f"Caractere inválido: {c}")

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_2</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_2: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -5,7 +5,7 @@
     """
     # Mapping of Roman numerals to values
     values = {
-        'I': 1,
+        'XXIXX': 1,
         'V': 5,
         'X': 10,
         'L': 50,

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_3</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_3: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -5,7 +5,7 @@
     """
     # Mapping of Roman numerals to values
     values = {
-        'I': 1,
+        'i': 1,
         'V': 5,
         'X': 10,
         'L': 50,

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_4</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_4: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -5,7 +5,7 @@
     """
     # Mapping of Roman numerals to values
     values = {
-        'I': 1,
+        'I': 2,
         'V': 5,
         'X': 10,
         'L': 50,

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_5</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_5: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -6,7 +6,7 @@
     # Mapping of Roman numerals to values
     values = {
         'I': 1,
-        'V': 5,
+        'XXVXX': 5,
         'X': 10,
         'L': 50,
         'C': 100,

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_6</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_6: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -6,7 +6,7 @@
     # Mapping of Roman numerals to values
     values = {
         'I': 1,
-        'V': 5,
+        'v': 5,
         'X': 10,
         'L': 50,
         'C': 100,

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_7</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_7: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -6,7 +6,7 @@
     # Mapping of Roman numerals to values
     values = {
         'I': 1,
-        'V': 5,
+        'V': 6,
         'X': 10,
         'L': 50,
         'C': 100,

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_10</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_10: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -7,7 +7,7 @@
     values = {
         'I': 1,
         'V': 5,
-        'X': 10,
+        'X': 11,
         'L': 50,
         'C': 100,
         'D': 500,

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_13</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_13: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -8,7 +8,7 @@
         'I': 1,
         'V': 5,
         'X': 10,
-        'L': 50,
+        'L': 51,
         'C': 100,
         'D': 500,
         'M': 1000

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_16</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_16: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -9,7 +9,7 @@
         'V': 5,
         'X': 10,
         'L': 50,
-        'C': 100,
+        'C': 101,
         'D': 500,
         'M': 1000
     }

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_19</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_19: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -10,7 +10,7 @@
         'X': 10,
         'L': 50,
         'C': 100,
-        'D': 500,
+        'D': 501,
         'M': 1000
     }
     valid_pairs = {'IV', 'IX', 'XL', 'XC', 'CD', 'CM'}

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_22</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_22: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -11,7 +11,7 @@
         'L': 50,
         'C': 100,
         'D': 500,
-        'M': 1000
+        'M': 1001
     }
     valid_pairs = {'IV', 'IX', 'XL', 'XC', 'CD', 'CM'}
     length = len(s)

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_45</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_45: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -19,7 +19,7 @@
         first = s[i]
         second = s[i + 1]
         # If a smaller numeral precedes a larger, it's a subtraction scenario
-        if values.get(first, 0) < values.get(second, 0):
+        if values.get(first, None) < values.get(second, 0):
             pair = first + second
             if pair not in valid_pairs:
                 raise ValueError(f"Subtração inválida: {pair}")

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_47</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_47: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -19,7 +19,7 @@
         first = s[i]
         second = s[i + 1]
         # If a smaller numeral precedes a larger, it's a subtraction scenario
-        if values.get(first, 0) < values.get(second, 0):
+        if values.get(first, ) < values.get(second, 0):
             pair = first + second
             if pair not in valid_pairs:
                 raise ValueError(f"Subtração inválida: {pair}")

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_48</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_48: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -19,7 +19,7 @@
         first = s[i]
         second = s[i + 1]
         # If a smaller numeral precedes a larger, it's a subtraction scenario
-        if values.get(first, 0) < values.get(second, 0):
+        if values.get(first, 1) < values.get(second, 0):
             pair = first + second
             if pair not in valid_pairs:
                 raise ValueError(f"Subtração inválida: {pair}")

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_51</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_51: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -19,7 +19,7 @@
         first = s[i]
         second = s[i + 1]
         # If a smaller numeral precedes a larger, it's a subtraction scenario
-        if values.get(first, 0) < values.get(second, 0):
+        if values.get(first, 0) < values.get(second, None):
             pair = first + second
             if pair not in valid_pairs:
                 raise ValueError(f"Subtração inválida: {pair}")

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_53</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_53: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -19,7 +19,7 @@
         first = s[i]
         second = s[i + 1]
         # If a smaller numeral precedes a larger, it's a subtraction scenario
-        if values.get(first, 0) < values.get(second, 0):
+        if values.get(first, 0) < values.get(second, ):
             pair = first + second
             if pair not in valid_pairs:
                 raise ValueError(f"Subtração inválida: {pair}")

```

</details>

<details>
<summary><code>roman_converter.converter.x_validate_subtraction_pairs__mutmut_54</code> (survived)</summary>

```diff
# roman_converter.converter.x_validate_subtraction_pairs__mutmut_54: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -19,7 +19,7 @@
         first = s[i]
         second = s[i + 1]
         # If a smaller numeral precedes a larger, it's a subtraction scenario
-        if values.get(first, 0) < values.get(second, 0):
+        if values.get(first, 0) < values.get(second, 1):
             pair = first + second
             if pair not in valid_pairs:
                 raise ValueError(f"Subtração inválida: {pair}")

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_16</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_16: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -8,7 +8,7 @@
         'V': 5,
         'X': 10,
         'L': 50,
-        'C': 100,
+        'C': 101,
         'D': 500,
         'M': 1000
     }

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_17</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_17: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -9,7 +9,7 @@
         'X': 10,
         'L': 50,
         'C': 100,
-        'D': 500,
+        'XXDXX': 500,
         'M': 1000
     }
     total = 0

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_18</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_18: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -9,7 +9,7 @@
         'X': 10,
         'L': 50,
         'C': 100,
-        'D': 500,
+        'd': 500,
         'M': 1000
     }
     total = 0

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_19</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_19: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -9,7 +9,7 @@
         'X': 10,
         'L': 50,
         'C': 100,
-        'D': 500,
+        'D': 501,
         'M': 1000
     }
     total = 0

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_29</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_29: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -15,7 +15,7 @@
     total = 0
     length = len(s)
     for i in range(length):
-        current_val = values.get(s[i], 0)
+        current_val = values.get(s[i], None)
         if i + 1 < length:
             next_val = values.get(s[i + 1], 0)
             if current_val < next_val:

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_31</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_31: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -15,7 +15,7 @@
     total = 0
     length = len(s)
     for i in range(length):
-        current_val = values.get(s[i], 0)
+        current_val = values.get(s[i], )
         if i + 1 < length:
             next_val = values.get(s[i + 1], 0)
             if current_val < next_val:

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_32</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_32: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -15,7 +15,7 @@
     total = 0
     length = len(s)
     for i in range(length):
-        current_val = values.get(s[i], 0)
+        current_val = values.get(s[i], 1)
         if i + 1 < length:
             next_val = values.get(s[i + 1], 0)
             if current_val < next_val:

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_38</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_38: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -17,7 +17,7 @@
     for i in range(length):
         current_val = values.get(s[i], 0)
         if i + 1 < length:
-            next_val = values.get(s[i + 1], 0)
+            next_val = values.get(s[i + 1], None)
             if current_val < next_val:
                 total -= current_val
             else:

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_40</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_40: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -17,7 +17,7 @@
     for i in range(length):
         current_val = values.get(s[i], 0)
         if i + 1 < length:
-            next_val = values.get(s[i + 1], 0)
+            next_val = values.get(s[i + 1], )
             if current_val < next_val:
                 total -= current_val
             else:

```

</details>

<details>
<summary><code>roman_converter.converter.x_compute_value__mutmut_43</code> (survived)</summary>

```diff
# roman_converter.converter.x_compute_value__mutmut_43: survived
--- roman_converter/converter.py
+++ roman_converter/converter.py
@@ -17,7 +17,7 @@
     for i in range(length):
         current_val = values.get(s[i], 0)
         if i + 1 < length:
-            next_val = values.get(s[i + 1], 0)
+            next_val = values.get(s[i + 1], 1)
             if current_val < next_val:
                 total -= current_val
             else:

```

</details>


---

## 🔍 Detalhes - Workspace 3

- **Total de Mutantes:** 80
- **Killed:** 61
- **Survived:** 19
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>roman.x_roman_to_int__mutmut_2</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_2: survived
--- src/roman.py
+++ src/roman.py
@@ -1,7 +1,7 @@
 def roman_to_int(roman: str) -> int:
     # Validação de tipo: apenas str é permitido
     if not isinstance(roman, str):
-        raise TypeError("Input must be a string")
+        raise TypeError(None)
 
     # Normalização para uppercase
     roman = roman.upper()

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_3</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_3: survived
--- src/roman.py
+++ src/roman.py
@@ -1,7 +1,7 @@
 def roman_to_int(roman: str) -> int:
     # Validação de tipo: apenas str é permitido
     if not isinstance(roman, str):
-        raise TypeError("Input must be a string")
+        raise TypeError("XXInput must be a stringXX")
 
     # Normalização para uppercase
     roman = roman.upper()

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_4</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_4: survived
--- src/roman.py
+++ src/roman.py
@@ -1,7 +1,7 @@
 def roman_to_int(roman: str) -> int:
     # Validação de tipo: apenas str é permitido
     if not isinstance(roman, str):
-        raise TypeError("Input must be a string")
+        raise TypeError("input must be a string")
 
     # Normalização para uppercase
     roman = roman.upper()

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_5</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_5: survived
--- src/roman.py
+++ src/roman.py
@@ -1,7 +1,7 @@
 def roman_to_int(roman: str) -> int:
     # Validação de tipo: apenas str é permitido
     if not isinstance(roman, str):
-        raise TypeError("Input must be a string")
+        raise TypeError("INPUT MUST BE A STRING")
 
     # Normalização para uppercase
     roman = roman.upper()

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_10</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_10: survived
--- src/roman.py
+++ src/roman.py
@@ -8,7 +8,7 @@
 
     # String vazia não é válida
     if len(roman) == 0:
-        raise ValueError("Input cannot be empty")
+        raise ValueError(None)
 
     total = 0
     length = len(roman)

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_11</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_11: survived
--- src/roman.py
+++ src/roman.py
@@ -8,7 +8,7 @@
 
     # String vazia não é válida
     if len(roman) == 0:
-        raise ValueError("Input cannot be empty")
+        raise ValueError("XXInput cannot be emptyXX")
 
     total = 0
     length = len(roman)

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_12</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_12: survived
--- src/roman.py
+++ src/roman.py
@@ -8,7 +8,7 @@
 
     # String vazia não é válida
     if len(roman) == 0:
-        raise ValueError("Input cannot be empty")
+        raise ValueError("input cannot be empty")
 
     total = 0
     length = len(roman)

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_13</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_13: survived
--- src/roman.py
+++ src/roman.py
@@ -8,7 +8,7 @@
 
     # String vazia não é válida
     if len(roman) == 0:
-        raise ValueError("Input cannot be empty")
+        raise ValueError("INPUT CANNOT BE EMPTY")
 
     total = 0
     length = len(roman)

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_17</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_17: survived
--- src/roman.py
+++ src/roman.py
@@ -13,7 +13,7 @@
     total = 0
     length = len(roman)
 
-    prev_char = None
+    prev_char = ""
     repeat_count = 0
 
     for i, ch in enumerate(roman):

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_18</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_18: survived
--- src/roman.py
+++ src/roman.py
@@ -14,7 +14,7 @@
     length = len(roman)
 
     prev_char = None
-    repeat_count = 0
+    repeat_count = None
 
     for i, ch in enumerate(roman):
         # Validação de caractere

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_19</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_19: survived
--- src/roman.py
+++ src/roman.py
@@ -14,7 +14,7 @@
     length = len(roman)
 
     prev_char = None
-    repeat_count = 0
+    repeat_count = 1
 
     for i, ch in enumerate(roman):
         # Validação de caractere

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_22</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_22: survived
--- src/roman.py
+++ src/roman.py
@@ -19,7 +19,7 @@
     for i, ch in enumerate(roman):
         # Validação de caractere
         if ch not in ROMAN_VALUES:
-            raise ValueError(f"Invalid Roman numeral character: {ch}")
+            raise ValueError(None)
 
         # Verificação de repetição
         if ch == prev_char:

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_40</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_40: survived
--- src/roman.py
+++ src/roman.py
@@ -31,7 +31,7 @@
         # Regras de repetição:
         # V, L, D não podem repetir consecutivamente
         if ch in ('V', 'L', 'D') and repeat_count > 1:
-            raise ValueError(f"Invalid repetition of roman numeral: {ch}")
+            raise ValueError(None)
         # I, X, C, M no máximo 3 vezes consecutivas
         if ch in ('I', 'X', 'C', 'M') and repeat_count > 3:
             raise ValueError(f"Invalid repetition of roman numeral: {ch}")

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_49</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_49: survived
--- src/roman.py
+++ src/roman.py
@@ -33,7 +33,7 @@
         if ch in ('V', 'L', 'D') and repeat_count > 1:
             raise ValueError(f"Invalid repetition of roman numeral: {ch}")
         # I, X, C, M no máximo 3 vezes consecutivas
-        if ch in ('I', 'X', 'C', 'M') and repeat_count > 3:
+        if ch in ('I', 'X', 'C', 'XXMXX') and repeat_count > 3:
             raise ValueError(f"Invalid repetition of roman numeral: {ch}")
 
         value = ROMAN_VALUES[ch]

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_50</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_50: survived
--- src/roman.py
+++ src/roman.py
@@ -33,7 +33,7 @@
         if ch in ('V', 'L', 'D') and repeat_count > 1:
             raise ValueError(f"Invalid repetition of roman numeral: {ch}")
         # I, X, C, M no máximo 3 vezes consecutivas
-        if ch in ('I', 'X', 'C', 'M') and repeat_count > 3:
+        if ch in ('I', 'X', 'C', 'm') and repeat_count > 3:
             raise ValueError(f"Invalid repetition of roman numeral: {ch}")
 
         value = ROMAN_VALUES[ch]

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_53</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_53: survived
--- src/roman.py
+++ src/roman.py
@@ -34,7 +34,7 @@
             raise ValueError(f"Invalid repetition of roman numeral: {ch}")
         # I, X, C, M no máximo 3 vezes consecutivas
         if ch in ('I', 'X', 'C', 'M') and repeat_count > 3:
-            raise ValueError(f"Invalid repetition of roman numeral: {ch}")
+            raise ValueError(None)
 
         value = ROMAN_VALUES[ch]
 

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_62</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_62: survived
--- src/roman.py
+++ src/roman.py
@@ -42,7 +42,7 @@
         if i + 1 < length:
             next_ch = roman[i + 1]
             if next_ch not in ROMAN_VALUES:
-                raise ValueError(f"Invalid Roman numeral character: {next_ch}")
+                raise ValueError(None)
             next_value = ROMAN_VALUES[next_ch]
             if value < next_value:
                 pair = ch + next_ch

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_71</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_71: survived
--- src/roman.py
+++ src/roman.py
@@ -48,7 +48,7 @@
                 pair = ch + next_ch
                 # Verifica par subtrativo válido e sem repetição antes da subtração
                 if pair not in VALID_SUBTRACTIVE_PAIRS or repeat_count > 1:
-                    raise ValueError(f"Invalid subtractive pair: {pair}")
+                    raise ValueError(None)
                 total -= value
                 continue
         # Soma normal

```

</details>

<details>
<summary><code>roman.x_roman_to_int__mutmut_80</code> (survived)</summary>

```diff
# roman.x_roman_to_int__mutmut_80: survived
--- src/roman.py
+++ src/roman.py
@@ -56,6 +56,6 @@
 
     # Validação de faixa de resultado
     if total < MIN_VALUE or total > MAX_VALUE:
-        raise ValueError(f"Result out of range: {total}")
+        raise ValueError(None)
 
     return total

```

</details>


---

