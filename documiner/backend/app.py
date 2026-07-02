import os
import sys
import importlib
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


APP_DIR = Path(__file__).with_name("app")
FRONTEND_FILE = Path(__file__).resolve().parent.parent / "frontend" / "chat.html"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

api_router = importlib.import_module("api").api_router


def create_app() -> FastAPI:
    app = FastAPI(title="Documiner Backend")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    async def serve_frontend() -> FileResponse:
        return FileResponse(FRONTEND_FILE)

    app.include_router(api_router)
    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    reload = os.getenv('FAST_ENV', 'production') == 'development'
    uvicorn.run("app:app", host='0.0.0.0', port=port, reload=reload)