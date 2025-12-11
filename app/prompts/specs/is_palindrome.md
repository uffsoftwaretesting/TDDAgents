Implemente a função is_palindrome que recebe uma string e retorna True se ela for um palíndromo (ou seja, se pode ser lida da mesma forma de trás para frente), ou False caso contrário.

⚙️ DEFINIÇÃO:
Uma string é considerada palíndromo se, após remover espaços, pontuações e ignorar diferenças de maiúsculas e minúsculas, sua sequência de caracteres for igual à sua inversa.

⚠️ REQUISITOS:
1. A função deve ignorar espaços (' '), vírgulas, pontos, exclamações, interrogações e outros sinais de pontuação.
2. A comparação não deve ser sensível a maiúsculas/minúsculas (ex: 'A' == 'a').
3. Caracteres acentuados (como 'á', 'ã', 'ç') devem ser considerados normalmente — ou seja, não há necessidade de removê-los.
4. Se a string for vazia, retornar True (string vazia é considerada palíndromo por definição).
5. Não utilizar bibliotecas externas.

💡 EXEMPLOS:
>>> is_palindrome('Ame a ema')
True

>>> is_palindrome('Socorram-me, subi no ônibus em Marrocos!')
True

>>> is_palindrome('OpenAI')
False
