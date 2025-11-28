Implemente a função roman_to_int que recebe uma string representando um numeral romano (ex: 'IX', 'MCMXCIV') e retorna o valor inteiro correspondente. A função deve suportar os símbolos I, V, X, L, C, D, M e aplicar corretamente a regra de subtração (ex: IV = 4, CM = 900).

⚠️ REQUISITOS:
1. Apenas os símbolos I, V, X, L, C, D, M são válidos.
2. Repetições máximas:
   - I, X, C, M podem repetir até 3 vezes consecutivas (III ✅, IIII ❌)
   - V, L, D NÃO podem repetir NUNCA (VV ❌, LL ❌, DD ❌)
3. Ordem válida: símbolos maiores devem vir antes dos menores, exceto em subtrações.
4. Subtrações válidas: apenas I antes de V ou X, X antes de L ou C, C antes de D ou M.
5. Para entradas inválidas, retornar 'not a valid roman number.
6. String vazia deve retornar 0.
7. Desconsiderar maiúsculas ou minúsculas (converter tudo para uppercase).
