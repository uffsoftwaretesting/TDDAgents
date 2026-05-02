- Os agentes TDD criaram complexidade desnecessária como por exemplo criar um Dockerfile para isolar os testes em métodos numéricos simples.

- Utilização de métodos descontinuados de bibliotecas sem especificar a versão da depenencia utilizada, necessitando de intervenção manual. (workspace_output_trapezoid_3_tdd-6b8d5a687a7b542d)

- Os prompts foram gerados com base no framework de engenharia de prompt AUTOMAT

- Utilizamos a biblioteca scipy, com o método runge kutta 45, como validadora numérica para todas as soluções geradas pelos agente, cujo foco é resolver Equações Diferenciais Ordinárias. A função _compute_scipy_reference chama em tempo de execução a biblioteca, recebe os parâmetros e computa o valor correspondente. Este valor é posteriormente comparado com os resultados da função gerada pelos agentes. O processo inicia com o cálculo do erro entre a solução gerada pelos agentes e o resultado do scipy. A função responsável é _err(result). Chamamos esta mesma função de cálculo de erro para fazer o teste de pointwise_check, onde utilizamos a np.testing.assert_allclose, biblioteca Numpy, para verificar se o resultado dos agentes está dentro de uma faixa tolerável de erro absoluto. O valor de erro absoluto está definido no arquivo "ground_truth.json" juntamente com outros dados e metadados concernetes aos testes, tais como intervalo, condicao inicial e observações.
