def validate_cpf(s):
    import re
    s = s.strip()
    if not re.match(r'^\d{3}\.\d{3}\.\d{3}-\d{2}$', s) and not re.match(r'^\d{11}$', s):
        return False
    s = re.sub(r'\D', '', s)
    if len(s) != 11 or not s.isdigit() or s == s[0] * 11:
        return False
    
    def calculate_digit(cpf, factor):
        total = 0
        for i in range(factor):
            total += int(cpf[i]) * (factor + 1 - i)
        digit = 11 - (total % 11)
        return digit if digit < 10 else 0

    first_digit = calculate_digit(s, 9)
    second_digit = calculate_digit(s + str(first_digit), 10)
    
    return s[-2:] == f'{first_digit}{second_digit}'