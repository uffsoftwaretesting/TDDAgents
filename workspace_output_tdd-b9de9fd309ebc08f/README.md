# Roman Converter

A Python package to convert Roman numerals (I to MMMCMXCIX) into integers.

## Installation

Clone the repository and install in editable mode:

```bash
pip install -e .
```

## Usage

```python
from roman_converter import roman_to_int

print(roman_to_int('I'))        # 1
print(roman_to_int('MCMXCIV'))  # 1994
```

## Testing

Run the test suite with pytest:

```bash
pytest
```
