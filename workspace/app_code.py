def convert_roman_to_integer(roman):
    if not roman:
        raise ValueError("Entrada inválida")
    
    roman_to_int = {
        'I': 1,
        'V': 5,
        'X': 10,
        'L': 50,
        'C': 100,
        'D': 500,
        'M': 1000
    }
    
    total = 0
    prev_value = 0
    
    for char in reversed(roman):
        if char not in roman_to_int:
            raise ValueError("Entrada inválida")
        
        value = roman_to_int[char]
        if value < prev_value:
            total -= value
        else:
            total += value
        prev_value = value
    
    return total