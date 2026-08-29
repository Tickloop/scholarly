from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = PROJECT_ROOT / ".data"
    database_url: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    ui_host: str = "127.0.0.1"
    ui_port: int = 5173
    trueforge_url: str = "http://localhost:8790"
    research_map_mcp_url: str = "http://127.0.0.1:8000/mcp"

    openai_api_key: SecretStr | None = None
    brightdata_api_key: SecretStr | None = None
    openai_research_model: str = "gpt-5.6-terra"
    openai_fast_model: str = "gpt-5.6-terra"
    semantic_scholar_api_key: SecretStr | None = None
    openalex_email: str | None = None

    worker_concurrency: int = Field(default=3, ge=1, le=3)
    reviewer_concurrency: int = Field(default=2, ge=1, le=2)
    discovery_max_batches: int = Field(default=5, ge=1, le=5)
    autonomous_paper_limit: int = Field(default=50, ge=1, le=50)
    upstream_max_attempts: int = Field(default=3, ge=1, le=3)
    retry_base_seconds: float = 0.25
    upstream_max_retry_delay_seconds: float = Field(default=30.0, ge=0, le=30)
    job_lease_seconds: int = 60
    job_heartbeat_seconds: int = 20

    paper_download_timeout_seconds: float = 30.0
    paper_download_max_bytes: int = 25 * 1024 * 1024
    paper_download_max_redirects: int = 3

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'research_map.sqlite3').resolve()}"

    @property
    def allowed_ui_origins(self) -> list[str]:
        hosts = {self.ui_host}
        if self.ui_host in {"127.0.0.1", "localhost", "0.0.0.0"}:
            hosts.update({"127.0.0.1", "localhost"})
        return sorted(f"http://{host}:{self.ui_port}" for host in hosts)


@lru_cache
def get_settings() -> Settings:
    return Settings()
