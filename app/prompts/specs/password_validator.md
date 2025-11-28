Implemente a função is_strong_password que recebe uma string representando uma senha e retorna True se ela for considerada forte, ou False caso contrário.

⚙️ DEFINIÇÃO:
Uma senha é considerada forte se atender a critérios mínimos de segurança, garantindo complexidade e resistência contra ataques de força bruta.

⚠️ REQUISITOS:
1. A senha deve conter pelo menos 8 caracteres.
2. Deve incluir pelo menos uma letra maiúscula (A–Z).
3. Deve incluir pelo menos uma letra minúscula (a–z).
4. Deve conter pelo menos um dígito numérico (0–9).
5. Deve conter pelo menos um caractere especial (ex: !, @, #, $, %, &, *).
6. Não pode conter espaços em branco.
7. A função deve retornar False se a entrada for vazia ou não for uma string.

💡 EXEMPLOS:
>>> is_strong_password('Abc123!@#')
True

>>> is_strong_password('senha123')
False

>>> is_strong_password('A1!')
False
