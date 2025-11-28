def check_landscape_pattern(n, h):
    if n < 2:
        return 0
    
    peaks_and_valleys = 0
    last_state = None
    
    for i in range(n):
        if i > 0 and i < n - 1:
            if h[i] > h[i - 1] and h[i] > h[i + 1]:
                if last_state == 'peak':
                    return 0
                last_state = 'peak'
                peaks_and_valleys += 1
            elif h[i] < h[i - 1] and h[i] < h[i + 1]:
                if last_state == 'valley':
                    return 0
                last_state = 'valley'
                peaks_and_valleys += 1
            elif h[i] == h[i - 1] or h[i] == h[i + 1]:
                return 0
    
    return 1 if peaks_and_valleys > 0 else 0