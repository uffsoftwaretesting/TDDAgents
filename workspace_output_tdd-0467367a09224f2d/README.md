# diferencas_finitas_bvp

Um pacote para resolver problemas de valor de contorno de segunda ordem -u''(x) = f(x) usando o método de diferenças finitas.

## Installation

Você pode instalar o pacote diretamente do PyPI:

```bash
pip install diferencas_finitas_bvp
```

## Usage

```python
import numpy as np
from diferencas_finitas_bvp.core import diferencas_finitas_bvp

# Define a função fonte f(x)
f = lambda x: np.ones_like(x)

# Defina domínio e condições de contorno
a, b = 0.0, 1.0
bc = {"u_a": 0.0, "u_b": 0.0}

# Número de nós internos e ponto de avaliação
N = 10
x_alvo = 0.5

# Chama a função
u_val = diferencas_finitas_bvp(f, a, b, bc, N, x_alvo)
print(f"u({x_alvo}) = {u_val}")
```