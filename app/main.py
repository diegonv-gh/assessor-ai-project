from fastapi import FastAPI
from app.routes import chat, identidade, perfil, sessions
from app.config import validar_config, FRONTEND_DIR
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


app = FastAPI(
    title="Assessor AI",
    description="Assessor financeiro e de agenda com LangChain e LangGraph",
    version="0.1.0",
)

for _problema in validar_config():
    print(f'[config] ATENÇÃO: {_problema}')

@app.get("/health")
def health() -> dict:
    problemas = validar_config()
    return {
        "status": "ok" if not problemas else "atencao",
        "problemas_de_configuracao": problemas,
    }

app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(perfil.router)
app.include_router(identidade.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# frontend

if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/", tags=["infra"])
    def raiz() -> dict:
        return{
            "mensagem": "API do assessor no ar. o frontend ainda n foi criado"
        }
