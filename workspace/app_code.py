def rate_limiter(max_requests, time_window, client_id):
    import time
    
    if not hasattr(rate_limiter, 'clients'):
        rate_limiter.clients = {}
    
    current_time = time.time()
    
    if client_id not in rate_limiter.clients:
        rate_limiter.clients[client_id] = {'count': 0, 'start_time': current_time}
    
    client_data = rate_limiter.clients[client_id]
    
    if current_time - client_data['start_time'] > time_window:
        client_data['count'] = 0
        client_data['start_time'] = current_time
    
    if client_data['count'] < max_requests:
        client_data['count'] += 1
        return "200 OK"
    
    return "429 Too Many Requests"