from application.dto.validation_result import ValidationResult

def test_validation_result_attributes():
    vr = ValidationResult(cpf_original='raw_value', cpf_formatado='111.111.111-11', valid=True)
    assert vr.cpf_original == 'raw_value'
    assert vr.cpf_formatado == '111.111.111-11'
    assert vr.valid is True
