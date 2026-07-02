from langchain_openai import ChatOpenAI
from app.core.config import get_settings

settings = get_settings()

llm = ChatOpenAI(
    model=settings.openai_model,
    base_url=settings.openai_base_url,
    api_key=settings.openai_api_key,
)

