from fastapi import APIRouter

from api.v1.endpoints import parser, frontend

api_router = APIRouter()

api_router.include_router(
    parser.router,
    prefix="/parser",
    tags=["parser"],
)

api_router.include_router(
    frontend.router,
    tags=["frontend"],
)