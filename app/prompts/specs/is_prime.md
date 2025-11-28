Implemente a função is_prime que recebe um número inteiro n e retorna True se ele for um número primo, ou False caso contrário.

⚙️ DEFINIÇÃO:
Um número primo é aquele maior que 1 que possui exatamente dois divisores positivos distintos: 1 e ele mesmo. Exemplos: 2, 3, 5, 7, 11.

⚠️ REQUISITOS:
1. O parâmetro n deve ser do tipo inteiro (int). Caso contrário, retornar 'invalid input'.
2. Se n for menor ou igual a 1, retornar False (números ≤ 1 não são primos por definição).
3. A verificação de divisores deve ser feita apenas até a raiz quadrada de n, incluindo otimização para pular números pares após o 2.
4. A função deve retornar True se n for primo e False caso contrário.
5. A função deve lidar corretamente com números negativos e zero.

💡 EXEMPLOS:
>>> is_prime(2)
True

>>> is_prime(9)
False

>>> is_prime(17)
True

>>> is_prime(1)
False

>>> is_prime('10')
'invalid input'
