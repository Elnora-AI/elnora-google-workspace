"""Tests for the optional knowledge-base connector config (gw_config)."""

from __future__ import annotations

import gw_config


def test_internal_domains_default_empty(monkeypatch):
    monkeypatch.delenv("GW_INTERNAL_DOMAINS", raising=False)
    assert gw_config.internal_domains() == set()


def test_internal_domains_parsed(monkeypatch):
    monkeypatch.setenv("GW_INTERNAL_DOMAINS", "Acme.com, mail.acme.com , ")
    assert gw_config.internal_domains() == {"acme.com", "mail.acme.com"}


def test_slack_and_transcripts_default_unset(monkeypatch):
    monkeypatch.delenv("GW_SLACK_USER_ID", raising=False)
    monkeypatch.delenv("GW_SLACK_CLI_BIN", raising=False)
    monkeypatch.delenv("GW_TRANSCRIPT_DIRS", raising=False)
    assert gw_config.slack_user_id() is None
    assert gw_config.slack_cli_bin() is None
    assert gw_config.transcript_dirs() == []


def test_config_and_cache_dir_default(monkeypatch):
    monkeypatch.delenv("GW_CONFIG_DIR", raising=False)
    assert gw_config.config_dir().name == "gw"
    assert gw_config.cache_dir().name == "cache"
    assert gw_config.cache_dir().parent == gw_config.config_dir()


def test_config_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_CONFIG_DIR", str(tmp_path / "cfg"))
    assert gw_config.config_dir() == tmp_path / "cfg"


def test_parse_frontmatter():
    fm = gw_config.parse_frontmatter("---\nvault_path: /v\ncompany_dir: co\n---\nbody")
    assert fm == {"vault_path": "/v", "company_dir": "co"}
    assert gw_config.parse_frontmatter("no frontmatter") == {}


def test_find_kb_config_explicit_env(monkeypatch, tmp_path):
    cfg = tmp_path / "kb.md"
    cfg.write_text("---\nvault_path: /v\n---\n", encoding="utf-8")
    monkeypatch.setenv("GW_KB_CONFIG", str(cfg))
    assert gw_config.find_kb_config() == cfg
    assert gw_config.kb_configured() is True


def test_find_kb_config_absent_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_KB_CONFIG", str(tmp_path / "missing.md"))
    assert gw_config.find_kb_config() is None
    assert gw_config.kb_configured() is False


def test_find_kb_config_walks_up_from_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("GW_KB_CONFIG", raising=False)
    project = tmp_path / "project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    cfg = project / ".claude" / "knowledge-base.local.md"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("---\nvault_path: /v\n---\n", encoding="utf-8")
    monkeypatch.chdir(nested)
    assert gw_config.find_kb_config() == cfg
