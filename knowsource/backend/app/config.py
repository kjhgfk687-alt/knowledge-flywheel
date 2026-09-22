"""应用配置：全部环境变量集中在此，pydantic-settings 加载 .env。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "KnowSource API"
    app_version: str = "0.1.0"
    debug: bool = True

    database_url: str = "postgresql+psycopg://ks:ks@localhost:5432/knowsource"

    chroma_dir: str = "./data/chroma"

    embedding_provider: str = "fastembed"  # fastembed | openai_compatible
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dim: int = 512
    # EMBEDDING_PROVIDER=openai_compatible 时启用
    openai_base_url: str | None = None
    openai_api_key: str | None = None

    # 契约 §1：与客舟共享的服务间密钥
    ks_api_key: str = "zs-kz-dev-key-001"

    # 契约 §5：检索预算
    vector_timeout_ms: int = 800
    retrieval_total_budget_ms: int = 3000
    # 相关性下限：低于该分的证据视为"不相关"被丢弃（支撑契约 retrieved=false 语义）
    min_score: float = 0.05

    # 切分参数
    chunk_size: int = 400
    chunk_overlap: int = 50
    top_k_max: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
