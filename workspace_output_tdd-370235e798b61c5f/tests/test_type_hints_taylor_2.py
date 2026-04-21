import inspect
from taylor_2.taylor_2 import taylor_2

def test_all_parameters_have_type_annotations():
    sig = inspect.signature(taylor_2)
    expected_params = ['f', 'df', 't0', 'y0', 't_final', 'h']
    for name in expected_params:
        param = sig.parameters.get(name)
        assert param is not None, f"Parameter '{name}' missing from signature"
        assert param.annotation is not inspect._empty, \
            f"Parameter '{name}' must have a type annotation"

def test_return_annotation_is_float():
    sig = inspect.signature(taylor_2)
    ret = sig.return_annotation
    assert ret is not inspect._empty, "Return type must be annotated"
    assert ret == float, f"Return annotation must be 'float', got {ret}"
