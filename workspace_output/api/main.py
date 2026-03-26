from fastapi import FastAPI
from api.routers.cpf import router as cpf_router

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "ok"}

# Include CPF validation endpoints
app.include_router(cpf_router)
