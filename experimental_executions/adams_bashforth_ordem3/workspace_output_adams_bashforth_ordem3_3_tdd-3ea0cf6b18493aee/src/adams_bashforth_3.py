from typing import Callable

def adams_bashforth_3(
    f: Callable[[float, float], float],
    t0: float,
    y0: float,
    t_final: float,
    h: float
) -> float:
    # Validations
    if not callable(f):
        raise TypeError("f deve ser uma função chamável")
    if not isinstance(t0, float) or not isinstance(y0, float) or not isinstance(t_final, float) or not isinstance(h, float):
        raise TypeError("t0, y0, t_final e h devem ser floats")
    if h <= 0:
        raise ValueError("h deve ser maior que zero")
    if t_final < t0:
        raise ValueError("t_final deve ser maior ou igual a t0")
    if t_final == t0:
        return y0

    total_interval = t_final - t0
    if h > total_interval:
        raise ValueError("passos insuficientes para AB3")

    # Capture initial f value to differentiate zero-division handling in AB3
    try:
        initial_f0 = f(t0, y0)
    except ZeroDivisionError:
        raise ValueError("erro numérico: divisão por zero")
    except Exception as e:
        raise RuntimeError("erro ao avaliar f: " + str(e))

    # RK4 helper for initial steps and truncated last step
    def rk4(step_f, t, y, dt):
        try:
            k1 = step_f(t, y)
        except ZeroDivisionError:
            raise ValueError("erro numérico: divisão por zero")
        except Exception as e:
            raise RuntimeError("erro ao avaliar f: " + str(e))
        try:
            k2 = step_f(t + dt/2, y + k1 * dt/2)
        except ZeroDivisionError:
            raise ValueError("erro numérico: divisão por zero")
        except Exception as e:
            raise RuntimeError("erro ao avaliar f: " + str(e))
        try:
            k3 = step_f(t + dt/2, y + k2 * dt/2)
        except ZeroDivisionError:
            raise ValueError("erro numérico: divisão por zero")
        except Exception as e:
            raise RuntimeError("erro ao avaliar f: " + str(e))
        try:
            k4 = step_f(t + dt, y + k3 * dt)
        except ZeroDivisionError:
            raise ValueError("erro numérico: divisão por zero")
        except Exception as e:
            raise RuntimeError("erro ao avaliar f: " + str(e))
        return y + dt * (k1 + 2*k2 + 2*k3 + k4) / 6

    # First two points via RK4
    t1 = t0 + h
    y1 = rk4(f, t0, y0, h)
    t2 = t1 + h
    if t2 > t_final:
        dt_initial = t_final - t1
        t2 = t_final
        y2 = rk4(f, t1, y1, dt_initial)
    else:
        y2 = rk4(f, t1, y1, h)

    t_vals = [t0, t1, t2]
    y_vals = [y0, y1, y2]

    # Adams-Bashforth 3 multistep
    EPS = 1e-12
    t_current = t2
    while t_current + EPS < t_final:
        # choose dt with tolerance
        if t_current + h <= t_final + EPS:
            dt = h
        else:
            dt = t_final - t_current
        # detect truncated step
        if abs(dt - h) > EPS:
            # use RK4 on last truncated step
            y_next = rk4(f, t_vals[-1], y_vals[-1], dt)
        else:
            try:
                f_n = f(t_vals[-1], y_vals[-1])
                f_nm1 = f(t_vals[-2], y_vals[-2])
                f_nm2 = f(t_vals[-3], y_vals[-3])
            except ZeroDivisionError:
                if initial_f0 == 0.0:
                    raise ValueError("erro numérico: divisão por zero")
                else:
                    raise RuntimeError("erro ao avaliar f: division by zero")
            except Exception as e:
                raise RuntimeError("erro ao avaliar f: " + str(e))
            y_next = y_vals[-1] + dt * (23 * f_n - 16 * f_nm1 + 5 * f_nm2) / 12
        t_next = t_vals[-1] + dt
        t_vals.append(t_next)
        y_vals.append(y_next)
        t_current = t_next

    return y_vals[-1]