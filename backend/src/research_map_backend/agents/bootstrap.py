import asyncio
from pathlib import Path

from dotenv import dotenv_values

from research_map_backend.integrations.trueforge import bootstrap_trueforge
from research_map_backend.settings import PROJECT_ROOT, Settings

PROJECT_ENV_FILE = PROJECT_ROOT / ".env"


def bootstrap_settings(env_file: Path | None = None) -> Settings:
    """Build settings with an explicit project .env overriding stale ambient values."""
    project_env = env_file or PROJECT_ENV_FILE
    overrides = {
        key.lower(): value
        for key, value in dotenv_values(project_env).items()
        if value is not None and key.lower() in Settings.model_fields
    }
    return Settings(**overrides)


def main() -> None:
    result = asyncio.run(bootstrap_trueforge(bootstrap_settings()))
    print(f"Configured provider: {result.provider_name}")
    print(f"Configured MCP server: {result.mcp_server_name}")
    for name, agent_id in result.agent_ids.items():
        print(f"Configured agent: {name} ({agent_id})")


if __name__ == "__main__":
    main()
