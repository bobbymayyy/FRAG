"""
Tests for the plugin packaging itself, not the FRAG library.

These exist because manifest mistakes fail late and confusingly: a plugin
with a malformed .mcp.json installs fine and then has no tools, and a
catalog whose `source` points at the wrong directory fails only when
someone tries to install it. Catching these in CI is much cheaper.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

# .../repo/plugins/frag/src/tests/test_manifests.py
_HERE = Path(__file__).resolve()
PLUGIN_ROOT = _HERE.parents[2]
REPO_ROOT = _HERE.parents[4]

MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
MCP_CONFIG = PLUGIN_ROOT / ".mcp.json"
SKILL = PLUGIN_ROOT / "skills" / "frag-retrieval" / "SKILL.md"
SERVER_SCRIPT = PLUGIN_ROOT / "scripts" / "frag-server"
PYPROJECT = PLUGIN_ROOT / "src" / "pyproject.toml"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_all_manifests_exist() -> None:
    for path in (MARKETPLACE, PLUGIN_MANIFEST, MCP_CONFIG, SKILL, SERVER_SCRIPT, PYPROJECT):
        assert path.exists(), f"missing {path.relative_to(REPO_ROOT)}"


def test_manifests_are_valid_json() -> None:
    for path in (MARKETPLACE, PLUGIN_MANIFEST, MCP_CONFIG):
        load(path)


def test_catalog_source_points_at_a_real_plugin_dir() -> None:
    catalog = load(MARKETPLACE)
    for entry in catalog["plugins"]:
        target = (REPO_ROOT / entry["source"]).resolve()
        assert target.is_dir(), f"{entry['name']}: source {entry['source']} is not a directory"
        assert (target / ".claude-plugin" / "plugin.json").exists(), (
            f"{entry['name']}: source has no plugin manifest"
        )


def test_plugin_manifest_has_no_version_field() -> None:
    """Deliberate: with `version` absent, Claude Code resolves the version
    from the git commit SHA, so every push to a tracked marketplace branch
    reaches sessions. Adding a `version` field silently switches to manual
    bump updates and would break push-to-deploy."""
    assert "version" not in load(PLUGIN_MANIFEST)


def test_plugin_manifest_has_only_recognized_fields() -> None:
    """`claude plugin validate --strict` runs in CI and treats unrecognized
    fields as errors, so a stray comment key fails the build."""
    recognized = {
        "$schema", "name", "displayName", "version", "description", "author",
        "homepage", "repository", "license", "keywords", "metadata",
        "defaultEnabled", "skills", "commands", "agents", "workflows", "hooks",
        "mcpServers", "outputStyles", "lspServers", "experimental",
        "userConfig", "channels", "dependencies",
    }
    unknown = set(load(PLUGIN_MANIFEST)) - recognized
    assert not unknown, f"unrecognized manifest fields would fail --strict: {sorted(unknown)}"


def test_tokens_are_declared_sensitive() -> None:
    """Sensitive values go to Claude Code's credential storage instead of
    plaintext settings. A token that loses this flag is a security regression."""
    user_config = load(PLUGIN_MANIFEST)["userConfig"]
    for key in ("github_token", "gitea_token"):
        assert user_config[key].get("sensitive") is True, f"{key} must be sensitive"


def test_mcp_server_does_not_depend_on_exec_bit() -> None:
    """The server is invoked as `python3 <script>` rather than executing the
    script directly, so a checkout that loses the executable bit still works."""
    server = load(MCP_CONFIG)["mcpServers"]["frag"]
    assert server["command"] == "python3"
    assert server["args"] == ["${CLAUDE_PLUGIN_ROOT}/scripts/frag-server"]


def test_frag_home_lives_in_persistent_data_dir() -> None:
    """CLAUDE_PLUGIN_ROOT is version-scoped and swept after updates. If
    FRAG_HOME pointed there, every plugin update would wipe every repo index."""
    env = load(MCP_CONFIG)["mcpServers"]["frag"]["env"]
    assert "${CLAUDE_PLUGIN_DATA}" in env["FRAG_HOME"]
    assert "${CLAUDE_PLUGIN_ROOT}" not in env["FRAG_HOME"]


def test_user_config_cannot_block_mcp_config_substitution() -> None:
    """Claude exports userConfig as CLAUDE_PLUGIN_OPTION_<KEY>. Consume those
    in the launcher rather than interpolating userConfig inside .mcp.json, so
    an unset option cannot prevent the process from spawning."""
    declared = set(load(PLUGIN_MANIFEST)["userConfig"])
    mcp_blob = json.dumps(load(MCP_CONFIG))
    launcher = SERVER_SCRIPT.read_text()

    assert "${user_config." not in mcp_blob
    for key in declared:
        option_env = f"CLAUDE_PLUGIN_OPTION_{key.upper()}"
        assert option_env in launcher, f"{key} is declared but {option_env} is not consumed"


def test_baseline_runtime_has_no_required_pypi_dependencies() -> None:
    """MCP startup occurs inside Claude's sandbox. Baseline FRAG must launch
    directly from bundled source without downloading packages first."""
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    assert project.get("dependencies", []) == []


def test_launcher_contains_no_package_bootstrap() -> None:
    """A first-run pip/venv bootstrap can fail under bubblewrap/network
    restrictions before MCP has a chance to initialize."""
    source = SERVER_SCRIPT.read_text()
    assert "import subprocess" not in source
    assert "subprocess." not in source
    assert '"-m", "venv"' not in source
    assert '"pip"' not in source


def test_skill_frontmatter_is_parseable() -> None:
    text = SKILL.read_text()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "SKILL.md has no YAML frontmatter block"
    front = match.group(1)
    assert re.search(r"^name:\s*\S", front, re.M), "frontmatter needs a name"
    assert re.search(r"^description:\s*\S", front, re.M), "frontmatter needs a description"


def test_skill_name_matches_its_directory() -> None:
    front = re.match(r"^---\n(.*?)\n---\n", SKILL.read_text(), re.S).group(1)
    name = re.search(r"^name:\s*(\S+)", front, re.M).group(1).strip("\"'")
    assert name == SKILL.parent.name


@pytest.mark.parametrize("tool", ["frag_search", "frag_resolve", "frag_status"])
def test_skill_only_references_tools_that_exist(tool: str) -> None:
    """Guards against the skill drifting to mention a tool the server
    doesn't expose."""
    from frag import mcp_server  # noqa: PLC0415

    assert hasattr(mcp_server, tool)
