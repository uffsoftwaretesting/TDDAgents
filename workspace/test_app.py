import pytest
from app_code import rate_limiter
import time

def test_valid_input():
    assert rate_limiter(max_requests=5, time_window=60, client_id="client_123") == "200 OK"
    assert rate_limiter(max_requests=5, time_window=60, client_id="client_123") == "200 OK"
    assert rate_limiter(max_requests=5, time_window=60, client_id="client_123") == "200 OK"
    assert rate_limiter(max_requests=5, time_window=60, client_id="client_123") == "200 OK"
    assert rate_limiter(max_requests=5, time_window=60, client_id="client_123") == "200 OK"
    assert rate_limiter(max_requests=5, time_window=60, client_id="client_123") == "429 Too Many Requests"

def test_rate_limiter_within_limit():
    assert rate_limiter(max_requests=5, time_window=60, client_id="client_456") == "200 OK"

def test_rate_limiter_exceed_limit():
    # Testando requisições que excedem o limite
    for _ in range(5):
        rate_limiter(max_requests=5, time_window=60, client_id="client_789")
    assert rate_limiter(max_requests=5, time_window=60, client_id="client_789") == "429 Too Many Requests"

def test_reset_counter_after_time_window():
    client_id = "client_999"
    max_requests = 2
    time_window = 1  # 1 segundo

    # Primeiras duas requisições devem ser bem-sucedidas
    assert rate_limiter(max_requests=max_requests, time_window=time_window, client_id=client_id) == "200 OK"
    assert rate_limiter(max_requests=max_requests, time_window=time_window, client_id=client_id) == "200 OK"
    
    # A terceira requisição deve ser rejeitada
    assert rate_limiter(max_requests=max_requests, time_window=time_window, client_id=client_id) == "429 Too Many Requests"
    
    # Espera o tempo da janela expirar
    time.sleep(time_window)
    
    # Após o tempo, a primeira requisição deve ser bem-sucedida novamente
    assert rate_limiter(max_requests=max_requests, time_window=time_window, client_id=client_id) == "200 OK"

def test_exact_limit_requests():
    client_id = "client_1000"
    max_requests = 3
    time_window = 10  # 10 segundos

    # Enviando exatamente max_requests requisições
    assert rate_limiter(max_requests=max_requests, time_window=time_window, client_id=client_id) == "200 OK"
    assert rate_limiter(max_requests=max_requests, time_window=time_window, client_id=client_id) == "200 OK"
    assert rate_limiter(max_requests=max_requests, time_window=time_window, client_id=client_id) == "200 OK"
    
    # A próxima requisição deve ser rejeitada
    assert rate_limiter(max_requests=max_requests, time_window=time_window, client_id=client_id) == "429 Too Many Requests"