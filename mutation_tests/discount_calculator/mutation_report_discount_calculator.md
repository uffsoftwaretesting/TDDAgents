# Relatório de Testes de Mutação - TDDAgents (Discount Calculator)

Este relatório apresenta os resultados consolidados dos testes de mutação para as três execuções experimentais das calculadoras de desconto.

## 📊 Resumo das Execuções

Os testes de mutação foram executados em três workspaces de execuções experimentais distintas:

| Métrica | Workspace 1 (`..._1_tdd-48bab12...`) | Workspace 2 (`..._2_tdd-94dd475...`) | Workspace 3 (`..._3_tdd-95a3c4c...`) |
| :--- | :---: | :---: | :---: |
| **Total de Mutantes** | 54 | 41 | 50 |
| **Killed (Mortos)** | 49 (90.74%) | 35 (85.37%) | 45 (90.00%) |
| **Survived (Sobreviventes)** | 5 (9.26%) | 6 (14.63%) | 5 (10.00%) |
| **Timeouts** | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| **Score de Mutação Real*** | **90.74%** | **95.12%** | **90.00%** |

\*O Score de Mutação Real desconta os mutantes sobreviventes que são funcionalmente equivalentes ou que afetam apenas mensagens de exceção não verificadas pelos testes.

---

## 💡 Análise Geral e Lacunas Encontradas

### 1. Mensagens de Exceção Não Verificadas (Workspace 2)
No **Workspace 2**, quatro mutantes sobreviventes (`_mutmut_3`, `_mutmut_7`, `_mutmut_12`, `_mutmut_20`) alteram as strings de erro de `TypeError` ou `ValueError`. A suíte de testes do Workspace 2 apenas valida se a exceção correta foi lançada usando `with pytest.raises(...)`, sem comparar a mensagem exata do erro. Portanto, estas alterações não quebraram os testes.
Como a validação de strings de exceções não-verificadas é considerada uma flexibilidade de implementação aceitável (não afetando a corretude lógica), estes 4 mutantes são descartados no cálculo do **Score de Mutação Real**.
Em contrapartida, no **Workspace 3**, os testes verificam a string exata das exceções (ex. `assert str(excinfo.value) == "..."`), fazendo com que todos esses mutantes fossem devidamente mortos.

### 2. Lacunas de Teste nos Casos de Borda (Todos os Workspaces)
Em todos os três workspaces, o mutante que altera o limite superior de validação do percentual de desconto de `100` para `101` (ex. `discount_percent > 101` ou `percentual > 101`) sobreviveu. Isso ocorre porque as suítes de testes validam apenas valores muito distantes do limite (como `150`) para garantir que uma exceção seja lançada, deixando um ponto cego para valores limítrofes entre `100` e `101` (como `100.5`).

### 3. Modo de Arredondamento do Decimal (Workspaces 1 e 3)
Nos **Workspaces 1 e 3**, mutantes que alteram o parâmetro `rounding=ROUND_HALF_UP` para `rounding=None` (ou removem o parâmetro) na chamada do método `.quantize()` do `Decimal` sobreviveram. Ao remover o modo explícito, o Python utiliza o modo de arredondamento padrão do contexto ativo (`ROUND_HALF_EVEN`). Como a suíte de testes não incluiu nenhum caso de teste cuja metade (`.005`) desempate de forma diferente entre os dois métodos (por exemplo, testar arredondamento de `0.025` que vai para `0.03` em `HALF_UP` mas para `0.02` em `HALF_EVEN`), o comportamento dos testes permaneceu inalterado e os mutantes sobreviveram.

### 4. Precisão do Arredondamento do Float (Workspace 2)
No **Workspace 2**, a mutação que altera o arredondamento do preço final de 2 para 3 casas decimais (`final_price = round(price - discount_amount, 3)`) sobreviveu. Como os preços esperados nos asserts dos testes possuem no máximo duas casas decimais, o float correspondente (ex: `90.0` ou `200.4`) continua passando na comparação direta de igualdade numérica do Python.

---

## 🔍 Detalhes - Workspace 1

- **Total de Mutantes:** 54
- **Killed:** 49
- **Survived:** 5
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>calculate_discount.x_calculate_discount__mutmut_18</code> (survived)</summary>

```diff
# calculate_discount.x_calculate_discount__mutmut_18: survived
--- src/calculate_discount.py
+++ src/calculate_discount.py
@@ -13,7 +13,7 @@
     if price < 0:
         raise ValueError("price must be non-negative")
     # Domínio: discount_percent entre 0 e 100
-    if discount_percent < 0 or discount_percent > 100:
+    if discount_percent < 0 or discount_percent > 101:
         raise ValueError("discount_percent must be between 0 and 100")
     # Conversão para Decimal para cálculo preciso
     price_dec = Decimal(str(price))

```

</details>

<details>
<summary><code>calculate_discount.x_calculate_discount__mutmut_37</code> (survived)</summary>

```diff
# calculate_discount.x_calculate_discount__mutmut_37: survived
--- src/calculate_discount.py
+++ src/calculate_discount.py
@@ -22,7 +22,7 @@
     raw_discount = price_dec * (percent_dec / Decimal('100'))
     raw_final_price = price_dec - raw_discount
     # Arredondamento usando ROUND_HALF_UP a duas casas decimais
-    discount_dec = raw_discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
+    discount_dec = raw_discount.quantize(Decimal('0.01'), rounding=None)
     final_price_dec = raw_final_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
     # Retorna valores como float
     return {

```

</details>

<details>
<summary><code>calculate_discount.x_calculate_discount__mutmut_39</code> (survived)</summary>

```diff
# calculate_discount.x_calculate_discount__mutmut_39: survived
--- src/calculate_discount.py
+++ src/calculate_discount.py
@@ -22,7 +22,7 @@
     raw_discount = price_dec * (percent_dec / Decimal('100'))
     raw_final_price = price_dec - raw_discount
     # Arredondamento usando ROUND_HALF_UP a duas casas decimais
-    discount_dec = raw_discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
+    discount_dec = raw_discount.quantize(Decimal('0.01'), )
     final_price_dec = raw_final_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
     # Retorna valores como float
     return {

```

</details>

<details>
<summary><code>calculate_discount.x_calculate_discount__mutmut_44</code> (survived)</summary>

```diff
# calculate_discount.x_calculate_discount__mutmut_44: survived
--- src/calculate_discount.py
+++ src/calculate_discount.py
@@ -23,7 +23,7 @@
     raw_final_price = price_dec - raw_discount
     # Arredondamento usando ROUND_HALF_UP a duas casas decimais
     discount_dec = raw_discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
-    final_price_dec = raw_final_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
+    final_price_dec = raw_final_price.quantize(Decimal('0.01'), rounding=None)
     # Retorna valores como float
     return {
         'discount': float(discount_dec),

```

</details>

<details>
<summary><code>calculate_discount.x_calculate_discount__mutmut_46</code> (survived)</summary>

```diff
# calculate_discount.x_calculate_discount__mutmut_46: survived
--- src/calculate_discount.py
+++ src/calculate_discount.py
@@ -23,7 +23,7 @@
     raw_final_price = price_dec - raw_discount
     # Arredondamento usando ROUND_HALF_UP a duas casas decimais
     discount_dec = raw_discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
-    final_price_dec = raw_final_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
+    final_price_dec = raw_final_price.quantize(Decimal('0.01'), )
     # Retorna valores como float
     return {
         'discount': float(discount_dec),

```

</details>


---

## 🔍 Detalhes - Workspace 2

- **Total de Mutantes:** 41
- **Killed:** 35
- **Survived:** 6
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>discount.x_calculate_discount__mutmut_3</code> (survived)</summary>

```diff
# discount.x_calculate_discount__mutmut_3: survived
--- src/discount.py
+++ src/discount.py
@@ -10,7 +10,7 @@
         dict: com chaves "discount_amount" e "final_price", ambos floats arredondados em duas casas decimais
     """
     if not isinstance(price, (int, float)):
-        raise TypeError("price must be an int or float")
+        raise TypeError("XXprice must be an int or floatXX")
     if not isinstance(discount_percentage, (int, float)):
         raise TypeError("discount_percentage must be an int or float")
     if price < 0:

```

</details>

<details>
<summary><code>discount.x_calculate_discount__mutmut_7</code> (survived)</summary>

```diff
# discount.x_calculate_discount__mutmut_7: survived
--- src/discount.py
+++ src/discount.py
@@ -12,7 +12,7 @@
     if not isinstance(price, (int, float)):
         raise TypeError("price must be an int or float")
     if not isinstance(discount_percentage, (int, float)):
-        raise TypeError("discount_percentage must be an int or float")
+        raise TypeError("XXdiscount_percentage must be an int or floatXX")
     if price < 0:
         raise ValueError("price must be non-negative")
     if discount_percentage < 0 or discount_percentage > 100:

```

</details>

<details>
<summary><code>discount.x_calculate_discount__mutmut_12</code> (survived)</summary>

```diff
# discount.x_calculate_discount__mutmut_12: survived
--- src/discount.py
+++ src/discount.py
@@ -14,7 +14,7 @@
     if not isinstance(discount_percentage, (int, float)):
         raise TypeError("discount_percentage must be an int or float")
     if price < 0:
-        raise ValueError("price must be non-negative")
+        raise ValueError("XXprice must be non-negativeXX")
     if discount_percentage < 0 or discount_percentage > 100:
         raise ValueError("discount_percentage must be between 0 and 100")
 

```

</details>

<details>
<summary><code>discount.x_calculate_discount__mutmut_18</code> (survived)</summary>

```diff
# discount.x_calculate_discount__mutmut_18: survived
--- src/discount.py
+++ src/discount.py
@@ -15,7 +15,7 @@
         raise TypeError("discount_percentage must be an int or float")
     if price < 0:
         raise ValueError("price must be non-negative")
-    if discount_percentage < 0 or discount_percentage > 100:
+    if discount_percentage < 0 or discount_percentage > 101:
         raise ValueError("discount_percentage must be between 0 and 100")
 
     discount_amount = round(price * (discount_percentage / 100), 2)

```

</details>

<details>
<summary><code>discount.x_calculate_discount__mutmut_20</code> (survived)</summary>

```diff
# discount.x_calculate_discount__mutmut_20: survived
--- src/discount.py
+++ src/discount.py
@@ -16,7 +16,7 @@
     if price < 0:
         raise ValueError("price must be non-negative")
     if discount_percentage < 0 or discount_percentage > 100:
-        raise ValueError("discount_percentage must be between 0 and 100")
+        raise ValueError("XXdiscount_percentage must be between 0 and 100XX")
 
     discount_amount = round(price * (discount_percentage / 100), 2)
     final_price = round(price - discount_amount, 2)

```

</details>

<details>
<summary><code>discount.x_calculate_discount__mutmut_37</code> (survived)</summary>

```diff
# discount.x_calculate_discount__mutmut_37: survived
--- src/discount.py
+++ src/discount.py
@@ -19,5 +19,5 @@
         raise ValueError("discount_percentage must be between 0 and 100")
 
     discount_amount = round(price * (discount_percentage / 100), 2)
-    final_price = round(price - discount_amount, 2)
+    final_price = round(price - discount_amount, 3)
     return {"discount_amount": discount_amount, "final_price": final_price}

```

</details>


---

## 🔍 Detalhes - Workspace 3

- **Total de Mutantes:** 50
- **Killed:** 45
- **Survived:** 5
- **Timeout:** 0

### Mutantes Sobreviventes

<details>
<summary><code>desconto.x_calcular_desconto__mutmut_16</code> (survived)</summary>

```diff
# desconto.x_calcular_desconto__mutmut_16: survived
--- src/desconto.py
+++ src/desconto.py
@@ -22,7 +22,7 @@
     if preco < 0:
         raise ValueError("preco não pode ser negativo")
     # Validação de domínio do percentual
-    if percentual < 0 or percentual > 100:
+    if percentual < 0 or percentual > 101:
         raise ValueError("percentual deve estar entre 0 e 100")
     # Cálculo e arredondamento com Decimal e HALF_UP
     preco_dec = Decimal(str(preco))

```

</details>

<details>
<summary><code>desconto.x_calcular_desconto__mutmut_28</code> (survived)</summary>

```diff
# desconto.x_calcular_desconto__mutmut_28: survived
--- src/desconto.py
+++ src/desconto.py
@@ -28,7 +28,7 @@
     preco_dec = Decimal(str(preco))
     perc_dec = Decimal(str(percentual))
     desconto_dec = (preco_dec * perc_dec / Decimal('100')).quantize(
-        Decimal('0.00'), rounding=ROUND_HALF_UP
+        Decimal('0.00'), rounding=None
     )
     preco_final_dec = (preco_dec - desconto_dec).quantize(
         Decimal('0.00'), rounding=ROUND_HALF_UP

```

</details>

<details>
<summary><code>desconto.x_calcular_desconto__mutmut_30</code> (survived)</summary>

```diff
# desconto.x_calcular_desconto__mutmut_30: survived
--- src/desconto.py
+++ src/desconto.py
@@ -28,8 +28,7 @@
     preco_dec = Decimal(str(preco))
     perc_dec = Decimal(str(percentual))
     desconto_dec = (preco_dec * perc_dec / Decimal('100')).quantize(
-        Decimal('0.00'), rounding=ROUND_HALF_UP
-    )
+        Decimal('0.00'), )
     preco_final_dec = (preco_dec - desconto_dec).quantize(
         Decimal('0.00'), rounding=ROUND_HALF_UP
     )

```

</details>

<details>
<summary><code>desconto.x_calcular_desconto__mutmut_39</code> (survived)</summary>

```diff
# desconto.x_calcular_desconto__mutmut_39: survived
--- src/desconto.py
+++ src/desconto.py
@@ -31,6 +31,6 @@
         Decimal('0.00'), rounding=ROUND_HALF_UP
     )
     preco_final_dec = (preco_dec - desconto_dec).quantize(
-        Decimal('0.00'), rounding=ROUND_HALF_UP
+        Decimal('0.00'), rounding=None
     )
     return {"desconto": float(desconto_dec), "preco_final": float(preco_final_dec)}

```

</details>

<details>
<summary><code>desconto.x_calcular_desconto__mutmut_41</code> (survived)</summary>

```diff
# desconto.x_calcular_desconto__mutmut_41: survived
--- src/desconto.py
+++ src/desconto.py
@@ -31,6 +31,5 @@
         Decimal('0.00'), rounding=ROUND_HALF_UP
     )
     preco_final_dec = (preco_dec - desconto_dec).quantize(
-        Decimal('0.00'), rounding=ROUND_HALF_UP
-    )
+        Decimal('0.00'), )
     return {"desconto": float(desconto_dec), "preco_final": float(preco_final_dec)}

```

</details>


---

