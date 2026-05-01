import logging

from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse

from src.domain.ports.cpf_validator import CPFValidator
from src.infrastructure.cpf_validator.docbr_validator import ValidateDocbrCpfValidator
from src.application.ports.validate_cpf_usecase import ValidateCpfUseCase
from src.application.use_cases.validate_cpf import ValidateCpfInteractor

# Configure logger for the application module
e_logger = logging.getLogger(__name__)


def get_cpf_validator() -> CPFValidator:
    """
    DI provider for CPFValidator.
    Returns a concrete ValidateDocbrCpfValidator instance.
    """
    return ValidateDocbrCpfValidator()


def get_validate_usecase(
    validator: CPFValidator = Depends(get_cpf_validator)
) -> ValidateCpfUseCase:
    """
    DI provider for ValidateCpfUseCase.
    Receives a CPFValidator and returns a ValidateCpfInteractor.
    """
    return ValidateCpfInteractor(validator)


def create_app() -> FastAPI:
    """
    Application factory. Instantiates FastAPI with metadata, registers global exception handler, and includes API routes.
    """
    app = FastAPI(
        title="API de Validação de CPF",
        version="1.0.0"
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Log the unhandled exception with stack trace
        e_logger.error("Unhandled exception", exc_info=exc)
        # Return generic internal server error response
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    # Include API routes
    from src.interfaces.api.routes.validate_cpf import router
    app.include_router(router)

    # Customize OpenAPI schema to inline request/response schemas in path
    from fastapi.openapi.utils import get_openapi

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        # Inline the ValidateCpfRequest and ValidateCpfResponse schemas
        req = schema['components']['schemas']['ValidateCpfRequest']
        resp = schema['components']['schemas']['ValidateCpfResponse']
        post_op = schema['paths']['/validate-cpf']['post']
        post_op['requestBody']['content']['application/json']['schema'] = req
        post_op['responses']['200']['content']['application/json']['schema'] = resp
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
    return app