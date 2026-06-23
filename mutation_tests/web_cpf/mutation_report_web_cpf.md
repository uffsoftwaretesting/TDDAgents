# Relatório de Testes de Mutação - TDDAgents (Web CPF Validator)

Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais do validador de CPF via API web (FastAPI + arquitetura limpa).

Os mutantes foram gerados apenas sobre a **lógica de negócio** de cada execução (entidade de domínio do CPF, caso de uso de validação e adapter), excluindo o *glue* de framework (FastAPI `main`/rotas, *schemas* e configuração). Testes não comportamentais (estrutura, lint, análise estática, existência de arquivos) foram desativados durante a execução por dependerem do sistema de arquivos/ferramentas externas e não contribuírem para matar mutantes.

## 📊 Resumo das Execuções

| Métrica | Workspace 1 (`..._1_tdd-6b937ff...`) | Workspace 2 (`..._2_tdd-10b251d...`) | Workspace 3 (`..._3_tdd-d844175...`) |
| :--- | :---: | :---: | :---: |
| **Total de Mutantes** | 114 | 89 | 36 |
| **Killed (Mortos)** | 95 (83.33%) | 64 (71.91%) | 21 (58.33%) |
| **Survived (Sobreviventes)** | 19 (16.67%) | 25 (28.09%) | 15 (41.67%) |
| **Timeouts** | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| **Score de Mutação** | **83.33%** | **71.91%** | **58.33%** |

O Score de Mutação é calculado como `Killed / Total`. Mutantes sobreviventes funcionalmente equivalentes (ex.: alterações em código de *glue* sem efeito observável) devem ser inspecionados manualmente nas seções de detalhes abaixo.

---

## 🔍 Detalhes - Workspace 1

- **Total de Mutantes:** 114
- **Killed:** 95
- **Survived:** 19
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_2</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_2: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -1,7 +1,7 @@
 def __init__(self, value: str):
     # Input must be a string
     if not isinstance(value, str):
-        raise InvalidCPFError("CPF must be a string")
+        raise InvalidCPFError(None)
 
     # Remove common mask characters
     sanitized = value.replace('.', '').replace('-', '')

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_3</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_3: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -1,7 +1,7 @@
 def __init__(self, value: str):
     # Input must be a string
     if not isinstance(value, str):
-        raise InvalidCPFError("CPF must be a string")
+        raise InvalidCPFError("XXCPF must be a stringXX")
 
     # Remove common mask characters
     sanitized = value.replace('.', '').replace('-', '')

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_4</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_4: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -1,7 +1,7 @@
 def __init__(self, value: str):
     # Input must be a string
     if not isinstance(value, str):
-        raise InvalidCPFError("CPF must be a string")
+        raise InvalidCPFError("cpf must be a string")
 
     # Remove common mask characters
     sanitized = value.replace('.', '').replace('-', '')

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_5</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_5: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -1,7 +1,7 @@
 def __init__(self, value: str):
     # Input must be a string
     if not isinstance(value, str):
-        raise InvalidCPFError("CPF must be a string")
+        raise InvalidCPFError("CPF MUST BE A STRING")
 
     # Remove common mask characters
     sanitized = value.replace('.', '').replace('-', '')

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_21</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_21: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -8,7 +8,7 @@
 
     # After sanitization, must be only digits
     if not sanitized.isdigit():
-        raise InvalidCPFError("CPF must contain only digits after sanitization")
+        raise InvalidCPFError("XXCPF must contain only digits after sanitizationXX")
 
     # Must contain exactly 11 digits
     if len(sanitized) != 11:

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_27</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_27: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -12,7 +12,7 @@
 
     # Must contain exactly 11 digits
     if len(sanitized) != 11:
-        raise InvalidCPFError("CPF must have 11 digits")
+        raise InvalidCPFError("XXCPF must have 11 digitsXX")
 
     # Cannot be a sequence of the same digit
     if all(d == sanitized[0] for d in sanitized):

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_32</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_32: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -15,7 +15,7 @@
         raise InvalidCPFError("CPF must have 11 digits")
 
     # Cannot be a sequence of the same digit
-    if all(d == sanitized[0] for d in sanitized):
+    if all(d == sanitized[1] for d in sanitized):
         raise InvalidCPFError("CPF cannot have all digits equal")
 
     # Convert to list of integers

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_34</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_34: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -16,7 +16,7 @@
 
     # Cannot be a sequence of the same digit
     if all(d == sanitized[0] for d in sanitized):
-        raise InvalidCPFError("CPF cannot have all digits equal")
+        raise InvalidCPFError("XXCPF cannot have all digits equalXX")
 
     # Convert to list of integers
     digits = [int(d) for d in sanitized]

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_46</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_46: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -22,7 +22,7 @@
     digits = [int(d) for d in sanitized]
 
     # Calculate first verifying digit
-    first_sum = sum(d * w for d, w in zip(digits[:9], range(10, 1, -1)))
+    first_sum = sum(d * w for d, w in zip(digits[:10], range(10, 1, -1)))
     first_mod = first_sum % 11
     first_check = 0 if first_mod < 2 else 11 - first_mod
     if digits[9] != first_check:

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_61</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_61: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -24,7 +24,7 @@
     # Calculate first verifying digit
     first_sum = sum(d * w for d, w in zip(digits[:9], range(10, 1, -1)))
     first_mod = first_sum % 11
-    first_check = 0 if first_mod < 2 else 11 - first_mod
+    first_check = 1 if first_mod < 2 else 11 - first_mod
     if digits[9] != first_check:
         raise InvalidCPFError("Invalid CPF check digits")
 

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_62</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_62: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -24,7 +24,7 @@
     # Calculate first verifying digit
     first_sum = sum(d * w for d, w in zip(digits[:9], range(10, 1, -1)))
     first_mod = first_sum % 11
-    first_check = 0 if first_mod < 2 else 11 - first_mod
+    first_check = 0 if first_mod <= 2 else 11 - first_mod
     if digits[9] != first_check:
         raise InvalidCPFError("Invalid CPF check digits")
 

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_63</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_63: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -24,7 +24,7 @@
     # Calculate first verifying digit
     first_sum = sum(d * w for d, w in zip(digits[:9], range(10, 1, -1)))
     first_mod = first_sum % 11
-    first_check = 0 if first_mod < 2 else 11 - first_mod
+    first_check = 0 if first_mod < 3 else 11 - first_mod
     if digits[9] != first_check:
         raise InvalidCPFError("Invalid CPF check digits")
 

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_68</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_68: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -26,7 +26,7 @@
     first_mod = first_sum % 11
     first_check = 0 if first_mod < 2 else 11 - first_mod
     if digits[9] != first_check:
-        raise InvalidCPFError("Invalid CPF check digits")
+        raise InvalidCPFError(None)
 
     # Calculate second verifying digit
     second_sum = sum(d * w for d, w in zip(digits[:10], range(11, 1, -1)))

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_69</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_69: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -26,7 +26,7 @@
     first_mod = first_sum % 11
     first_check = 0 if first_mod < 2 else 11 - first_mod
     if digits[9] != first_check:
-        raise InvalidCPFError("Invalid CPF check digits")
+        raise InvalidCPFError("XXInvalid CPF check digitsXX")
 
     # Calculate second verifying digit
     second_sum = sum(d * w for d, w in zip(digits[:10], range(11, 1, -1)))

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_70</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_70: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -26,7 +26,7 @@
     first_mod = first_sum % 11
     first_check = 0 if first_mod < 2 else 11 - first_mod
     if digits[9] != first_check:
-        raise InvalidCPFError("Invalid CPF check digits")
+        raise InvalidCPFError("invalid cpf check digits")
 
     # Calculate second verifying digit
     second_sum = sum(d * w for d, w in zip(digits[:10], range(11, 1, -1)))

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_71</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_71: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -26,7 +26,7 @@
     first_mod = first_sum % 11
     first_check = 0 if first_mod < 2 else 11 - first_mod
     if digits[9] != first_check:
-        raise InvalidCPFError("Invalid CPF check digits")
+        raise InvalidCPFError("INVALID CPF CHECK DIGITS")
 
     # Calculate second verifying digit
     second_sum = sum(d * w for d, w in zip(digits[:10], range(11, 1, -1)))

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_79</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_79: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -29,7 +29,7 @@
         raise InvalidCPFError("Invalid CPF check digits")
 
     # Calculate second verifying digit
-    second_sum = sum(d * w for d, w in zip(digits[:10], range(11, 1, -1)))
+    second_sum = sum(d * w for d, w in zip(digits[:11], range(11, 1, -1)))
     second_mod = second_sum % 11
     second_check = 0 if second_mod < 2 else 11 - second_mod
     if digits[10] != second_check:

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_94</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_94: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -31,7 +31,7 @@
     # Calculate second verifying digit
     second_sum = sum(d * w for d, w in zip(digits[:10], range(11, 1, -1)))
     second_mod = second_sum % 11
-    second_check = 0 if second_mod < 2 else 11 - second_mod
+    second_check = 1 if second_mod < 2 else 11 - second_mod
     if digits[10] != second_check:
         raise InvalidCPFError("Invalid CPF check digits")
 

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_102</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_102: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -33,7 +33,7 @@
     second_mod = second_sum % 11
     second_check = 0 if second_mod < 2 else 11 - second_mod
     if digits[10] != second_check:
-        raise InvalidCPFError("Invalid CPF check digits")
+        raise InvalidCPFError("XXInvalid CPF check digitsXX")
 
     # All checks passed; set the sanitized value
     self.value = sanitized

```

</details>


---

## 🔍 Detalhes - Workspace 2

- **Total de Mutantes:** 89
- **Killed:** 64
- **Survived:** 25
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_2</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_2: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -1,7 +1,7 @@
 def __init__(self, raw: str):
     # Input must be a string
     if not isinstance(raw, str):
-        raise InvalidCpfFormat("CPF must be provided as a string")
+        raise InvalidCpfFormat(None)
     # Only digits, dots and dashes are allowed
     allowed_chars = set("0123456789.-")
     for ch in raw:

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_3</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_3: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -1,7 +1,7 @@
 def __init__(self, raw: str):
     # Input must be a string
     if not isinstance(raw, str):
-        raise InvalidCpfFormat("CPF must be provided as a string")
+        raise InvalidCpfFormat("XXCPF must be provided as a stringXX")
     # Only digits, dots and dashes are allowed
     allowed_chars = set("0123456789.-")
     for ch in raw:

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_4</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_4: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -1,7 +1,7 @@
 def __init__(self, raw: str):
     # Input must be a string
     if not isinstance(raw, str):
-        raise InvalidCpfFormat("CPF must be provided as a string")
+        raise InvalidCpfFormat("cpf must be provided as a string")
     # Only digits, dots and dashes are allowed
     allowed_chars = set("0123456789.-")
     for ch in raw:

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_5</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_5: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -1,7 +1,7 @@
 def __init__(self, raw: str):
     # Input must be a string
     if not isinstance(raw, str):
-        raise InvalidCpfFormat("CPF must be provided as a string")
+        raise InvalidCpfFormat("CPF MUST BE PROVIDED AS A STRING")
     # Only digits, dots and dashes are allowed
     allowed_chars = set("0123456789.-")
     for ch in raw:

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_8</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_8: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -3,7 +3,7 @@
     if not isinstance(raw, str):
         raise InvalidCpfFormat("CPF must be provided as a string")
     # Only digits, dots and dashes are allowed
-    allowed_chars = set("0123456789.-")
+    allowed_chars = set("XX0123456789.-XX")
     for ch in raw:
         if ch not in allowed_chars:
             raise InvalidCpfFormat(f"Invalid character in CPF: '{ch}'")

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_10</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_10: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -6,7 +6,7 @@
     allowed_chars = set("0123456789.-")
     for ch in raw:
         if ch not in allowed_chars:
-            raise InvalidCpfFormat(f"Invalid character in CPF: '{ch}'")
+            raise InvalidCpfFormat(None)
     # Normalize: remove dots and dashes
     digits = ''.join(filter(str.isdigit, raw))
     # Must have exactly 11 digits

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_20</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_20: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -11,7 +11,7 @@
     digits = ''.join(filter(str.isdigit, raw))
     # Must have exactly 11 digits
     if len(digits) != 11:
-        raise InvalidCpfFormat("CPF must contain exactly 11 digits after removing mask")
+        raise InvalidCpfFormat(None)
     self.value = digits
     # All digits equal is considered invalid
     if len(set(digits)) == 1:

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_21</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_21: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -11,7 +11,7 @@
     digits = ''.join(filter(str.isdigit, raw))
     # Must have exactly 11 digits
     if len(digits) != 11:
-        raise InvalidCpfFormat("CPF must contain exactly 11 digits after removing mask")
+        raise InvalidCpfFormat("XXCPF must contain exactly 11 digits after removing maskXX")
     self.value = digits
     # All digits equal is considered invalid
     if len(set(digits)) == 1:

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_22</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_22: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -11,7 +11,7 @@
     digits = ''.join(filter(str.isdigit, raw))
     # Must have exactly 11 digits
     if len(digits) != 11:
-        raise InvalidCpfFormat("CPF must contain exactly 11 digits after removing mask")
+        raise InvalidCpfFormat("cpf must contain exactly 11 digits after removing mask")
     self.value = digits
     # All digits equal is considered invalid
     if len(set(digits)) == 1:

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_23</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_23: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -11,7 +11,7 @@
     digits = ''.join(filter(str.isdigit, raw))
     # Must have exactly 11 digits
     if len(digits) != 11:
-        raise InvalidCpfFormat("CPF must contain exactly 11 digits after removing mask")
+        raise InvalidCpfFormat("CPF MUST CONTAIN EXACTLY 11 DIGITS AFTER REMOVING MASK")
     self.value = digits
     # All digits equal is considered invalid
     if len(set(digits)) == 1:

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_27</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_27: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -15,7 +15,7 @@
     self.value = digits
     # All digits equal is considered invalid
     if len(set(digits)) == 1:
-        raise InvalidCpfCheckDigits("CPF with all identical digits is invalid")
+        raise InvalidCpfCheckDigits(None)
     # Convert to list of ints
     nums = [int(d) for d in digits]
     # Validate first check digit

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_28</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_28: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -15,7 +15,7 @@
     self.value = digits
     # All digits equal is considered invalid
     if len(set(digits)) == 1:
-        raise InvalidCpfCheckDigits("CPF with all identical digits is invalid")
+        raise InvalidCpfCheckDigits("XXCPF with all identical digits is invalidXX")
     # Convert to list of ints
     nums = [int(d) for d in digits]
     # Validate first check digit

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_29</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_29: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -15,7 +15,7 @@
     self.value = digits
     # All digits equal is considered invalid
     if len(set(digits)) == 1:
-        raise InvalidCpfCheckDigits("CPF with all identical digits is invalid")
+        raise InvalidCpfCheckDigits("cpf with all identical digits is invalid")
     # Convert to list of ints
     nums = [int(d) for d in digits]
     # Validate first check digit

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_30</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_30: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -15,7 +15,7 @@
     self.value = digits
     # All digits equal is considered invalid
     if len(set(digits)) == 1:
-        raise InvalidCpfCheckDigits("CPF with all identical digits is invalid")
+        raise InvalidCpfCheckDigits("CPF WITH ALL IDENTICAL DIGITS IS INVALID")
     # Convert to list of ints
     nums = [int(d) for d in digits]
     # Validate first check digit

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_51</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_51: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -24,7 +24,7 @@
     if first_rest == 10:
         first_rest = 0
     if first_rest != nums[9]:
-        raise InvalidCpfCheckDigits("First check digit does not match")
+        raise InvalidCpfCheckDigits(None)
     # Validate second check digit
     second_sum = sum(nums[i] * (11 - i) for i in range(10))
     second_rest = (second_sum * 10) % 11

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_52</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_52: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -24,7 +24,7 @@
     if first_rest == 10:
         first_rest = 0
     if first_rest != nums[9]:
-        raise InvalidCpfCheckDigits("First check digit does not match")
+        raise InvalidCpfCheckDigits("XXFirst check digit does not matchXX")
     # Validate second check digit
     second_sum = sum(nums[i] * (11 - i) for i in range(10))
     second_rest = (second_sum * 10) % 11

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_53</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_53: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -24,7 +24,7 @@
     if first_rest == 10:
         first_rest = 0
     if first_rest != nums[9]:
-        raise InvalidCpfCheckDigits("First check digit does not match")
+        raise InvalidCpfCheckDigits("first check digit does not match")
     # Validate second check digit
     second_sum = sum(nums[i] * (11 - i) for i in range(10))
     second_rest = (second_sum * 10) % 11

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_54</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_54: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -24,7 +24,7 @@
     if first_rest == 10:
         first_rest = 0
     if first_rest != nums[9]:
-        raise InvalidCpfCheckDigits("First check digit does not match")
+        raise InvalidCpfCheckDigits("FIRST CHECK DIGIT DOES NOT MATCH")
     # Validate second check digit
     second_sum = sum(nums[i] * (11 - i) for i in range(10))
     second_rest = (second_sum * 10) % 11

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_68</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_68: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -28,7 +28,7 @@
     # Validate second check digit
     second_sum = sum(nums[i] * (11 - i) for i in range(10))
     second_rest = (second_sum * 10) % 11
-    if second_rest == 10:
+    if second_rest == 11:
         second_rest = 0
     if second_rest != nums[10]:
         raise InvalidCpfCheckDigits("Second check digit does not match")

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_69</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_69: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -29,6 +29,6 @@
     second_sum = sum(nums[i] * (11 - i) for i in range(10))
     second_rest = (second_sum * 10) % 11
     if second_rest == 10:
-        second_rest = 0
+        second_rest = None
     if second_rest != nums[10]:
         raise InvalidCpfCheckDigits("Second check digit does not match")

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_70</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_70: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -29,6 +29,6 @@
     second_sum = sum(nums[i] * (11 - i) for i in range(10))
     second_rest = (second_sum * 10) % 11
     if second_rest == 10:
-        second_rest = 0
+        second_rest = 1
     if second_rest != nums[10]:
         raise InvalidCpfCheckDigits("Second check digit does not match")

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_73</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_73: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -31,4 +31,4 @@
     if second_rest == 10:
         second_rest = 0
     if second_rest != nums[10]:
-        raise InvalidCpfCheckDigits("Second check digit does not match")
+        raise InvalidCpfCheckDigits(None)

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_74</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_74: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -31,4 +31,4 @@
     if second_rest == 10:
         second_rest = 0
     if second_rest != nums[10]:
-        raise InvalidCpfCheckDigits("Second check digit does not match")
+        raise InvalidCpfCheckDigits("XXSecond check digit does not matchXX")

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_75</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_75: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -31,4 +31,4 @@
     if second_rest == 10:
         second_rest = 0
     if second_rest != nums[10]:
-        raise InvalidCpfCheckDigits("Second check digit does not match")
+        raise InvalidCpfCheckDigits("second check digit does not match")

```

</details>

<details>
<summary><code>domain.cpf.xǁCpfǁ__init____mutmut_76</code> (survived)</summary>

```diff
# domain.cpf.xǁCpfǁ__init____mutmut_76: survived
--- domain/cpf.py
+++ domain/cpf.py
@@ -31,4 +31,4 @@
     if second_rest == 10:
         second_rest = 0
     if second_rest != nums[10]:
-        raise InvalidCpfCheckDigits("Second check digit does not match")
+        raise InvalidCpfCheckDigits("SECOND CHECK DIGIT DOES NOT MATCH")

```

</details>


---

## 🔍 Detalhes - Workspace 3

- **Total de Mutantes:** 36
- **Killed:** 21
- **Survived:** 15
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_2</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_2: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -1,6 +1,6 @@
 def __init__(self, cpf: str) -> None:
     if not isinstance(cpf, str):
-        raise InvalidCPFFormatError("CPF must be a string")
+        raise InvalidCPFFormatError(None)
 
     # Allowed characters: digits, dot, hyphen
     allowed = set("0123456789.-")

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_3</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_3: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -1,6 +1,6 @@
 def __init__(self, cpf: str) -> None:
     if not isinstance(cpf, str):
-        raise InvalidCPFFormatError("CPF must be a string")
+        raise InvalidCPFFormatError("XXCPF must be a stringXX")
 
     # Allowed characters: digits, dot, hyphen
     allowed = set("0123456789.-")

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_4</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_4: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -1,6 +1,6 @@
 def __init__(self, cpf: str) -> None:
     if not isinstance(cpf, str):
-        raise InvalidCPFFormatError("CPF must be a string")
+        raise InvalidCPFFormatError("cpf must be a string")
 
     # Allowed characters: digits, dot, hyphen
     allowed = set("0123456789.-")

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_5</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_5: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -1,6 +1,6 @@
 def __init__(self, cpf: str) -> None:
     if not isinstance(cpf, str):
-        raise InvalidCPFFormatError("CPF must be a string")
+        raise InvalidCPFFormatError("CPF MUST BE A STRING")
 
     # Allowed characters: digits, dot, hyphen
     allowed = set("0123456789.-")

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_8</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_8: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -3,7 +3,7 @@
         raise InvalidCPFFormatError("CPF must be a string")
 
     # Allowed characters: digits, dot, hyphen
-    allowed = set("0123456789.-")
+    allowed = set("XX0123456789.-XX")
     for ch in cpf:
         if ch not in allowed:
             raise InvalidCPFFormatError(

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_10</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_10: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -7,7 +7,7 @@
     for ch in cpf:
         if ch not in allowed:
             raise InvalidCPFFormatError(
-                f"Invalid character '{ch}' in CPF"
+                None
             )
 
     # Remove mask characters

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_16</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_16: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -16,7 +16,7 @@
     # Must have exactly 11 digits
     if len(digits) != 11:
         raise InvalidCPFFormatError(
-            "CPF must contain exactly 11 digits"
+            None
         )
 
     # Cannot be a sequence of the same digit

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_17</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_17: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -16,7 +16,7 @@
     # Must have exactly 11 digits
     if len(digits) != 11:
         raise InvalidCPFFormatError(
-            "CPF must contain exactly 11 digits"
+            "XXCPF must contain exactly 11 digitsXX"
         )
 
     # Cannot be a sequence of the same digit

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_18</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_18: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -16,7 +16,7 @@
     # Must have exactly 11 digits
     if len(digits) != 11:
         raise InvalidCPFFormatError(
-            "CPF must contain exactly 11 digits"
+            "cpf must contain exactly 11 digits"
         )
 
     # Cannot be a sequence of the same digit

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_19</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_19: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -16,7 +16,7 @@
     # Must have exactly 11 digits
     if len(digits) != 11:
         raise InvalidCPFFormatError(
-            "CPF must contain exactly 11 digits"
+            "CPF MUST CONTAIN EXACTLY 11 DIGITS"
         )
 
     # Cannot be a sequence of the same digit

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_22</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_22: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -20,7 +20,7 @@
         )
 
     # Cannot be a sequence of the same digit
-    if digits == digits[0] * 11:
+    if digits == digits[1] * 11:
         raise InvalidCPFSequenceError(
             "CPF cannot be a sequence of the same digit"
         )

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_24</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_24: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -22,7 +22,7 @@
     # Cannot be a sequence of the same digit
     if digits == digits[0] * 11:
         raise InvalidCPFSequenceError(
-            "CPF cannot be a sequence of the same digit"
+            None
         )
 
     self.value = digits

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_25</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_25: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -22,7 +22,7 @@
     # Cannot be a sequence of the same digit
     if digits == digits[0] * 11:
         raise InvalidCPFSequenceError(
-            "CPF cannot be a sequence of the same digit"
+            "XXCPF cannot be a sequence of the same digitXX"
         )
 
     self.value = digits

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_26</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_26: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -22,7 +22,7 @@
     # Cannot be a sequence of the same digit
     if digits == digits[0] * 11:
         raise InvalidCPFSequenceError(
-            "CPF cannot be a sequence of the same digit"
+            "cpf cannot be a sequence of the same digit"
         )
 
     self.value = digits

```

</details>

<details>
<summary><code>domain.cpf.xǁCPFǁ__init____mutmut_27</code> (survived)</summary>

```diff
# domain.cpf.xǁCPFǁ__init____mutmut_27: survived
--- src/domain/cpf.py
+++ src/domain/cpf.py
@@ -22,7 +22,7 @@
     # Cannot be a sequence of the same digit
     if digits == digits[0] * 11:
         raise InvalidCPFSequenceError(
-            "CPF cannot be a sequence of the same digit"
+            "CPF CANNOT BE A SEQUENCE OF THE SAME DIGIT"
         )
 
     self.value = digits

```

</details>


---

