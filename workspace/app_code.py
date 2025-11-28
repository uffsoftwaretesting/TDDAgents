def validador(s):
    import re
    
    if not s or not re.match(r'^\d{3}.\d{3}.\d{3}-\d{2}$', s) and not re.match(r'^\d{11}$', s):
        return 'INVALIDO'
    
    s = re.sub(r'\D', '', s)  # Remove caracteres não numéricos
    if len(s) != 11 or s == '00000000000':
        return 'INVALIDO'
    
    def calcular_digitos_verificadores(cpf):
        for i in range(9, 11):
            soma = sum(int(cpf[j]) * ((i + 1) - j) for j in range(i))
            digito = 11 - (soma % 11)
            digito = digito if digito < 10 else 0
            if digito != int(cpf[i]):
                return False
        return True

    return 'VALIDO' if calcular_digitos_verificadores(s) else 'INVALIDO'