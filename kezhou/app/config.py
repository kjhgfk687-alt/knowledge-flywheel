"""全局配置：全部走环境变量（前缀 KEZHOU_），带开发默认值。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 与知源契约 v1.0 第 2 节一致的 tenant_id 规则，客舟侧同样用它校验渠道配置
TENANT_ID_PATTERN = r"^[a-z0-9][a-z0-9_]{1,31}$"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KEZHOU_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- 存储 ----
    db_path: str = "db/kezhou.sqlite"
    # LangGraph checkpointer 库。留空 = 自动用 db_path 同目录的 <name>_checkpoints.sqlite。
    # 必须与业务库分文件：同一文件上 AsyncSqliteSaver 与业务连接多路写入会竞争写锁，
    # 实测间歇性 ~11s 停顿（2026-09-22 对照实验：同文件 11.07s / 分文件 0.07s）。
    checkpoint_db_path: str = ""

    # ---- 知源 /retrieve 连接（契约 v1.1）----
    # 检索实现切换：mock = MockRetrievalClient（阶段一/演示）；zhiyuan = 真 REST 客户端（阶段二）
    retrieve_provider: str = "mock"
    zhiyuan_base_url: str = "http://localhost:8100"
    zhiyuan_api_key: str = "zs-kz-dev-key-001"
    # 客户端超时 3.5s > 知源侧总预算 3s（向量尝试 + 关键词降级，504 由知源先抛）。
    # 多出的余量是刻意设计：让知源的 504 错误信封能到达并被归类为 RETRIEVAL_TIMEOUT，
    # 而不是客户端裸超时无法区分。知源侧预算变更时，此处必须同步（契约第 4/5 节）。
    retrieve_timeout_seconds: float = 3.5
    # 阶段二启用的重试开关（真实配置参数，非注释占位）：0 = 失败即兜底转人工
    retrieve_max_retries: int = 0

    # 阶段一 Mock 检索场景：ok | empty | degraded | error | timeout
    retrieve_mock_scenario: str = "ok"

    # ---- 案例回写（契约 v1.3 §7 POST /api/v1/cases）----
    # dryrun = 只记录不真调（测试/演示）；zhiyuan = 真 REST 调知源
    writeback_provider: str = "dryrun"

    # ---- LLM ----
    llm_provider: str = "mock"  # mock | openai_compatible
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_model: str = "glm-4-flash"

    # ---- 阈值 ----
    intent_confidence_threshold: float = 0.6
    confidence_low_threshold: float = 0.55

    # ---- 三维风控（鉴定状态 × 品类 × 金额）----
    auto_refund_max: float = 1000.0      # 已鉴定 + 金额≤此值 → 自动流程
    high_value_threshold: float = 5000.0  # 未鉴定 + 金额≥此值 → 强制转人工
    refund_dispute_keywords: list[str] = ["假货", "真伪", "仿品", "是不是真的", "怀疑是假的"]

    # ---- 记忆 ----
    history_window: int = 10
    long_term_update_interval: int = 5
    emotion_keywords: list[str] = [
        "投诉", "315", "垃圾", "骗子", "举报", "工商", "消协", "差评", "退我钱",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
