"""Unit tests for per-layer schema validation before merge (issue #5110 Slice 3)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.workspace_cmd import config_group
from bernstein.core.config.config_schema import LayerValidationError, validate_layer_partial
from bernstein.core.config.home import BernsteinHome, resolve_config
from bernstein.core.config.run_overlay import resolve_effective_mapping
from bernstein.core.config.seed_config import SeedError
from bernstein.core.config.seed_parser import parse_seed

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A minimal project with git structure and .sdd/config.yaml."""
    (tmp_path / ".git").mkdir(parents=True)
    (tmp_path / ".sdd").mkdir(parents=True)
    (tmp_path / ".sdd" / "config.yaml").write_text("cli: codex\nmax_agents: 3\n", encoding="utf-8")
    (tmp_path / "bernstein.yaml").write_text("goal: Test goal\n", encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate tests from real ~/.bernstein and leaked environment variables."""
    monkeypatch.setenv("BERNSTEIN_HOME", str(tmp_path / "home"))
    for k in ("CLI", "EFFORT", "MAX_AGENTS", "MODEL", "BUDGET", "CONFIG_OVERLAY", "CONFIG_OVERRIDE"):
        monkeypatch.delenv(f"BERNSTEIN_{k}", raising=False)
    yield


def test_validate_layer_partial_valid_section() -> None:
    validate_layer_partial({"quality_gates": {"enabled": True}}, layer_name="test")


def test_validate_layer_partial_invalid_section() -> None:
    with pytest.raises(LayerValidationError) as exc_info:
        validate_layer_partial({"quality_gates": {"enabled": "not_a_bool"}}, layer_name="test")
    assert "quality_gates.enabled" in str(exc_info.value)


def test_validate_layer_partial_invalid_scalar() -> None:
    with pytest.raises(LayerValidationError) as exc_info:
        validate_layer_partial({"max_agents": "bad_int"}, layer_name="test")
    assert "max_agents" in str(exc_info.value)


def test_validate_layer_partial_missing_required_fields_ignored() -> None:
    validate_layer_partial({"remote": {"port": 2222}}, layer_name="test")
    validate_layer_partial({"smtp": {"username": "bot"}}, layer_name="test")


def test_invalid_overlay_section_names_the_overlay_file(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = project / "overlay.yaml"
    overlay.write_text("quality_gates:\n  enabled: nope\n", encoding="utf-8")
    monkeypatch.setenv("BERNSTEIN_CONFIG_OVERLAY", str(overlay))
    with pytest.raises(LayerValidationError) as exc:
        resolve_effective_mapping({"goal": "Test goal"}, config_path=project / "bernstein.yaml")
    msg = str(exc.value)
    assert "run-overlay" in msg and str(overlay) in msg and "quality_gates.enabled" in msg


def test_invalid_inline_override_names_the_env_var(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERNSTEIN_CONFIG_OVERRIDE", '{"notify": {"webhook": 42}}')
    with pytest.raises(LayerValidationError) as exc:
        resolve_effective_mapping({"goal": "Test goal"}, config_path=project / "bernstein.yaml")
    msg = str(exc.value)
    assert "inline-override" in msg and "$BERNSTEIN_CONFIG_OVERRIDE" in msg and "notify.webhook" in msg


def test_valid_partial_layer_passes(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = project / "valid_overlay.yaml"
    overlay.write_text("max_agents: 4\n", encoding="utf-8")
    monkeypatch.setenv("BERNSTEIN_CONFIG_OVERLAY", str(overlay))
    merged = resolve_effective_mapping({"goal": "Base goal", "max_agents": 2}, config_path=project / "bernstein.yaml")
    assert merged["goal"] == "Base goal" and merged["max_agents"] == 4


def test_parse_seed_surfaces_layer_validation_error(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = project / "overlay_bad.yaml"
    overlay.write_text("quality_gates:\n  enabled: invalid_bool\n", encoding="utf-8")
    monkeypatch.setenv("BERNSTEIN_CONFIG_OVERLAY", str(overlay))
    with pytest.raises(SeedError) as exc:
        parse_seed(project / "bernstein.yaml")
    msg = str(exc.value)
    assert "run-overlay" in msg and str(overlay) in msg and "quality_gates.enabled" in msg


def test_validate_layer_partial_field_aware_constraints() -> None:
    with pytest.raises(LayerValidationError) as exc:
        validate_layer_partial({"max_agents": -1}, layer_name="test")
    assert "greater than or equal to 1" in str(exc.value)

    with pytest.raises(LayerValidationError) as exc:
        validate_layer_partial({"cli": "unsupported_cli"}, layer_name="test")
    assert "claude" in str(exc.value)


def test_invalid_session_env_var_reported_with_session_layer_name(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BERNSTEIN_MAX_AGENTS", "nope")
    with pytest.raises(LayerValidationError) as exc:
        resolve_config("max_agents", home=BernsteinHome.default(), project_dir=project)
    assert exc.value.layer_name == "session"
    assert "$BERNSTEIN_MAX_AGENTS" in str(exc.value) and "max_agents" in str(exc.value)


def test_invalid_session_env_var_field_constraint_rejected(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERNSTEIN_MAX_AGENTS", "-1")
    with pytest.raises(LayerValidationError) as exc:
        resolve_config("max_agents", home=BernsteinHome.default(), project_dir=project)
    assert exc.value.layer_name == "session"
    assert "$BERNSTEIN_MAX_AGENTS" in str(exc.value) and "greater than or equal to 1" in str(exc.value)


def test_invalid_project_layer_reported_with_project_layer_name(project: Path) -> None:
    (project / ".sdd" / "config.yaml").write_text("max_agents: -5\n", encoding="utf-8")
    with pytest.raises(LayerValidationError) as exc:
        resolve_config("max_agents", home=BernsteinHome.default(), project_dir=project)
    assert exc.value.layer_name == "project"
    assert ".sdd" in str(exc.value) and "greater than or equal to 1" in str(exc.value)


def test_cli_config_get_surfaces_clean_error_on_invalid_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI commands catch LayerValidationError and surface clean error without traceback (#5110)."""
    monkeypatch.setenv("BERNSTEIN_MAX_AGENTS", "nope")
    result = CliRunner().invoke(config_group, ["get", "max_agents"])
    assert result.exit_code == 1
    assert "Config layer 'session'" in result.output
    assert "$BERNSTEIN_MAX_AGENTS" in result.output
    assert "Traceback" not in result.output


def test_shadowed_layer_not_validated_when_winning_layer_valid(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shadowed lower layer is not validated when higher-precedence winning layer is valid (#5110)."""
    (project / ".sdd" / "config.yaml").write_text("max_agents: -5\n", encoding="utf-8")
    monkeypatch.setenv("BERNSTEIN_MAX_AGENTS", "3")
    res = resolve_config("max_agents", home=BernsteinHome.default(), project_dir=project)
    assert res["value"] == 3
    assert res["source"] == "session"
