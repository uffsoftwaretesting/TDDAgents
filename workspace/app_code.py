def validate_cpf(s):
    s = s.replace('.', '').replace('-', '')
    if len(s) != 11 or not s.isdigit():
        return False
    if s == s[0] * 11:
        return False
    
    def calculate_digit(cpf, factor):
        total = 0
        for i in range(factor):
            total += int(cpf[i]) * (factor + 1 - i)
        digit = 11 - (total % 11)
        return digit if digit < 10 else 0

    first_digit = calculate_digit(s, 9)
    second_digit = calculate_digit(s + str(first_digit), 10)
    
    return s[-2:] == f"{first_digit}{second_digit}"