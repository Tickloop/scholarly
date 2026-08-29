from pathlib import Path
from types import SimpleNamespace

from research_map_backend.agents import bootstrap
from research_map_backend.integrations.trueforge import openai_provider_manifest


def test_bootstrap_entrypoint_prefers_project_dotenv_without_printing_key(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env_file = tmp_path / ".env"
    project_key = "project-file-key"
    env_file.write_text(f"OPENAI_API_KEY={project_key}\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-process-key")
    monkeypatch.setattr(bootstrap, "PROJECT_ENV_FILE", env_file)
    captured: dict[str, str] = {}

    async def fake_bootstrap(settings):
        captured["key"] = openai_provider_manifest(settings)["auth"]["api_key"]
        return SimpleNamespace(
            provider_name="openai",
            mcp_server_name="research-map",
            agent_ids={},
        )

    monkeypatch.setattr(bootstrap, "bootstrap_trueforge", fake_bootstrap)

    bootstrap.main()

    output = capsys.readouterr().out
    assert captured["key"] == project_key
    assert project_key not in output
    assert "stale-process-key" not in output


def test_bootstrap_without_project_dotenv_keeps_deployment_environment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "deployment-key")

    settings = bootstrap.bootstrap_settings(tmp_path / "missing.env")

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "deployment-key"
