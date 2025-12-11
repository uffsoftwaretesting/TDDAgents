Implemente a função fizzbuzz que recebe um número inteiro positivo n e retorna uma lista de strings representando os números de 1 até n, aplicando as seguintes regras:

⚙️ REGRAS:
1. Para cada número i de 1 até n:
   - Se i for divisível por 3 e por 5, adicione 'FizzBuzz' à lista.
   - Se i for divisível apenas por 3, adicione 'Fizz' à lista.
   - Se i for divisível apenas por 5, adicione 'Buzz' à lista.
   - Caso contrário, adicione o próprio número (como string).

⚠️ REQUISITOS:
1. O parâmetro n deve ser um número inteiro positivo (> 0).
2. Se n <= 0 ou não for um número inteiro, retornar 'invalid input'.
3. O retorno deve ser uma lista de strings (por exemplo: ['1', '2', 'Fizz', ...]).
4. Não usar bibliotecas externas.
5. A função deve ter complexidade O(n).

💡 EXEMPLOS:
>>> fizzbuzz(5)
['1', '2', 'Fizz', '4', 'Buzz']

>>> fizzbuzz(15)
['1', '2', 'Fizz', '4', 'Buzz', 'Fizz', '7', '8', 'Fizz', 'Buzz', '11', 'Fizz', '13', '14', 'FizzBuzz']
