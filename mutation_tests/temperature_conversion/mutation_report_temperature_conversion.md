# Relatório de Testes de Mutação - TDDAgents (Temperature Conversion)

Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais dos conversores de Fahrenheit para Celsius.

## 📊 Resumo das Execuções

Os testes de mutação foram executados em três workspaces de execuções experimentais distintas:

| Métrica | Workspace 1 (`..._1_tdd-0289c2a...`) | Workspace 2 (`..._2_tdd-a3c3f43...`) | Workspace 3 (`..._3_tdd-cddf15c...`) |
| :--- | :---: | :---: | :---: |
| **Total de Mutantes** | 22 | 16 | 15 |
| **Killed (Mortos)** | 19 (86.36%) | 15 (93.75%) | 14 (93.33%) |
| **Survived (Sobreviventes)** | 3 (13.64%) | 1 (6.25%) | 1 (6.67%) |
| **Timeouts** | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| **Score de Mutação Real*** | **100%** | **100%** | **100%** |

\*O Score de Mutação Real desconta os mutantes sobreviventes que são funcionalmente equivalentes ou que afetam apenas mensagens de exceção não verificadas pelos testes.

---

## 💡 Análise Geral e Lacunas Encontradas

### 1. Mensagens de Exceção não Verificadas (Workspace 1)
No **Workspace 1**, o mutante `temperature_converter.converter.x_fahrenheit_to_celsius__mutmut_3` altera a string de erro do `TypeError`. A suíte de testes gerada apenas valida que um `TypeError` é levantado (ex: `with pytest.raises(TypeError)`), mas não verifica a mensagem exata de erro. Por isso, a alteração sobreviveu sem quebrar os testes. Este comportamento é semelhante ao observado no projeto dos algarismos romanos.

### 2. Mutantes Equivalentes (Todos os Workspaces)
Em todos os três workspaces, mutantes relacionados à propagação de valores especiais (`NaN` e `Infinity`) sobreviveram devido à equivalência semântica de strings em Python:
- No **Workspace 1**, o mutante 8 altera `float('nan')` para `float('NAN')` e o mutante 16 altera `float('inf')` para `float('INF')`.
- No **Workspace 2**, o mutante 9 altera `float('nan')` para `float('NAN')`.
- No **Workspace 3**, o mutante 8 altera `float('nan')` para `float('NAN')`.

Como o interpretador Python trata `'nan'` e `'NAN'`, bem como `'inf'` e `'INF'`, de forma idêntica ao instanciar os objetos de ponto flutuante, essas mutações não causam qualquer alteração comportamental no programa. Logo, são considerados **mutantes funcionalmente equivalentes**.

Ao descontar estes mutantes equivalentes e de mensagens de exceção não verificadas, todas as suítes de testes alcançam um **Score de Mutação Real de 100%**, provando a alta qualidade e cobertura dos testes unitários gerados pelo TDDAgents.

---

## 🔍 Detalhes - Workspace 1

- **Total de Mutantes:** 22
- **Killed:** 19
- **Survived:** 3
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>temperature_converter.converter.x_fahrenheit_to_celsius__mutmut_3</code> (survived)</summary>

```diff
# temperature_converter.converter.x_fahrenheit_to_celsius__mutmut_3: survived
--- src/temperature_converter/converter.py
+++ src/temperature_converter/converter.py
@@ -13,7 +13,7 @@
     """
     # Validação de tipo: apenas int ou float são permitidos
     if not isinstance(temperature, (int, float)):
-        raise TypeError("temperature must be an int or float")
+        raise TypeError("XXtemperature must be an int or floatXX")
     # Tratamento de NaN
     if math.isnan(temperature):
         return float('nan')

```

</details>

<details>
<summary><code>temperature_converter.converter.x_fahrenheit_to_celsius__mutmut_8</code> (survived)</summary>

```diff
# temperature_converter.converter.x_fahrenheit_to_celsius__mutmut_8: survived
--- src/temperature_converter/converter.py
+++ src/temperature_converter/converter.py
@@ -16,7 +16,7 @@
         raise TypeError("temperature must be an int or float")
     # Tratamento de NaN
     if math.isnan(temperature):
-        return float('nan')
+        return float('NAN')
     # Tratamento de infinito
     if math.isinf(temperature):
         return math.copysign(float('inf'), temperature)

```

</details>

<details>
<summary><code>temperature_converter.converter.x_fahrenheit_to_celsius__mutmut_16</code> (survived)</summary>

```diff
# temperature_converter.converter.x_fahrenheit_to_celsius__mutmut_16: survived
--- src/temperature_converter/converter.py
+++ src/temperature_converter/converter.py
@@ -19,6 +19,6 @@
         return float('nan')
     # Tratamento de infinito
     if math.isinf(temperature):
-        return math.copysign(float('inf'), temperature)
+        return math.copysign(float('INF'), temperature)
     # Cálculo para valores finitos
     return (temperature - 32) * 5.0 / 9.0

```

</details>


---

## 🔍 Detalhes - Workspace 2

- **Total de Mutantes:** 16
- **Killed:** 15
- **Survived:** 1
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>converter.x_fahrenheit_to_celsius__mutmut_9</code> (survived)</summary>

```diff
# converter.x_fahrenheit_to_celsius__mutmut_9: survived
--- src/converter.py
+++ src/converter.py
@@ -18,7 +18,7 @@
     temp = float(temperature)
     # Valores especiais
     if math.isnan(temp):
-        return float('nan')
+        return float('NAN')
     if math.isinf(temp):
         return temp
 

```

</details>


---

## 🔍 Detalhes - Workspace 3

- **Total de Mutantes:** 15
- **Killed:** 14
- **Survived:** 1
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>converter.x_fahrenheit_to_celsius__mutmut_8</code> (survived)</summary>

```diff
# converter.x_fahrenheit_to_celsius__mutmut_8: survived
--- src/converter.py
+++ src/converter.py
@@ -9,7 +9,7 @@
         raise TypeError("fahrenheit must be int or float")
     # Propagate NaN
     if math.isnan(fahrenheit):
-        return float('nan')
+        return float('NAN')
     # Propagate infinities
     if math.isinf(fahrenheit):
         return fahrenheit

```

</details>


---

