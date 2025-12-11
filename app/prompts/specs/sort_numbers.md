Implemente a função sort_numbers que recebe uma lista de números inteiros e retorna uma nova lista com os mesmos elementos em ordem crescente.

⚙️ DEFINIÇÃO:
A ordenação deve ser feita de forma que o menor número apareça primeiro e o maior por último. A função deve preservar todos os elementos originais, sem removê-los ou alterá-los, apenas reordenando.

⚠️ REQUISITOS:
1. O parâmetro de entrada deve ser uma lista (list) contendo apenas valores inteiros (int).
   - Caso a entrada não seja uma lista, ou contenha elementos não inteiros, retornar 'invalid input'.
2. A função deve retornar uma **nova lista**, sem modificar a lista original (sem efeitos colaterais).
3. É permitido o uso de métodos ou funções internas de ordenação do Python (ex: sorted, list.sort).
4. Implementações manuais de ordenação (ex: bubble sort, insertion sort) também são aceitas, desde que mantenham a complexidade esperada.
5. A função deve lidar corretamente com listas vazias (retornar []).
6. Números negativos devem ser ordenados corretamente antes dos positivos.

💡 EXEMPLOS:
>>> sort_numbers([3, 1, 4, 1, 5, 9])
[1, 1, 3, 4, 5, 9]

>>> sort_numbers([-2, 0, 10, -5])
[-5, -2, 0, 10]

>>> sort_numbers([])
[]

>>> sort_numbers([3, 'a', 2])
'invalid input'
