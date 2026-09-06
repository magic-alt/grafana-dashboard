import pytest
from obs_platform.config import DatabaseSettings, TelemetrySettings, env_bool


def test_database_settings_support_platform_env_names():
    settings = DatabaseSettings.from_env(
        {
            "POSTGRES_HOST": "db.internal",
            "POSTGRES_PORT": "5433",
            "POSTGRES_DB": "observability",
            "POSTGRES_USER": "reader",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_SSLMODE": "require",
        }
    )

    assert settings.host == "db.internal"
    assert settings.port == 5433
    assert settings.name == "observability"
    assert settings.user == "reader"
    assert settings.password == "secret"
    assert settings.sslmode == "require"
    assert settings.psycopg_kwargs()["dbname"] == "observability"


def test_database_settings_reject_invalid_port():
    with pytest.raises(ValueError, match="POSTGRES_PORT must be an integer"):
        DatabaseSettings.from_env({"POSTGRES_PORT": "not-a-port"})


def test_database_settings_validate_secret():
    settings = DatabaseSettings.from_env({})
    with pytest.raises(ValueError, match="password"):
        settings.validate(require_password=True)


def test_telemetry_settings_build_standard_resource_contract():
    settings = TelemetrySettings.from_env(
        "worker",
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318/",
            "OBS_SERVICE_NAMESPACE": "magic-alt",
            "DEPLOYMENT_ENVIRONMENT": "ci",
            "SERVICE_VERSION": "abc123",
            "OBS_PROJECT": "ragbot",
        },
    )

    assert settings.enabled is True
    assert settings.traces_endpoint == "http://collector:4318/v1/traces"
    assert settings.resource_attributes() == {
        "service.name": "worker",
        "service.namespace": "magic-alt",
        "service.version": "abc123",
        "deployment.environment": "ci",
        "project.name": "ragbot",
    }


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_bool_truthy(value):
    assert env_bool(value)
