"""The configure menu's saved-edit contract, exercised through real tags."""

import os
import pty
import select
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from omc import configure, docsllm
from omc.config import store
from omc.config.schema import GlobalConfig, ProjectConfig, SecretsConfig
from omc.errors import Refusal
from omc.toolctx import ToolContext


def _menu(tmp_path: Path, *, root: Path | None = None):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg, pcfg, scfg = GlobalConfig(), ProjectConfig(), SecretsConfig()
    session = configure._compose_menu(ctx, root, gcfg, pcfg, scfg)
    return session, (ctx, gcfg, pcfg, scfg)


def test_menu_groups_schema_fields_and_registry_providers(tmp_path):
    session, _ = _menu(tmp_path)
    assert list(session.root) == ["LLM", "Documentation"]
    llm = session.root["LLM"]
    assert llm["Default provider"].description == "Provider for new sessions."
    assert set(llm) == {"Default provider", "claude", "codex"}
    assert set(llm["claude"]) == {
        "Configured",
        "Session model",
        "Native notifications",
        "Documentation model",
        "API key",
    }
    assert "API key" not in llm["codex"]
    assert set(llm["Default provider"]._build_options().values()) == {"claude"}
    assert set(
        session.root["Documentation"]["Documentation provider"]._build_options().values()
    ) == {"", "claude"}
    assert set(
        session.root["Documentation"]["Documentation backend"]._build_options().values()
    ) == {"cli", "api"}
    assert "Other (type a model id)" in llm["claude"]["Session model"]._build_options()
    assert llm["claude"]["Native notifications"].description == (
        "Use this provider's native session alerts."
    )
    assert "Worktree (project)" not in session.root
    repo = tmp_path / "repo"
    repo.mkdir()
    in_repo, _ = _menu(tmp_path, root=repo)
    assert set(in_repo.root["Worktree (project)"]) == {"Branch prefix", "Base branch"}


def test_docs_backend_choices_follow_selected_provider_capability(tmp_path):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers["codex"] = configure.ProviderConfig()
    gcfg.llm.docs.provider = "codex"
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig(), SecretsConfig())
    assert list(
        session.root["Documentation"]["Documentation backend"]._build_options().values()
    ) == ["cli"]


def test_schema_field_and_section_metadata_drive_menu(tmp_path, monkeypatch):
    from types import SimpleNamespace

    original_fields = configure.fields
    monkeypatch.setattr(configure.ProviderConfig, "future_model_flag", "future", raising=False)
    monkeypatch.setattr(type(GlobalConfig().llm.docs), "future_docs_flag", "future", raising=False)

    def extended_fields(obj):
        result = list(original_fields(obj))
        if isinstance(obj, configure.ProviderConfig):
            result.append(
                SimpleNamespace(
                    name="future_model_flag",
                    metadata={"label": "Future model flag", "help": "Provider metadata."},
                )
            )
        elif isinstance(obj, type(GlobalConfig().llm.docs)):
            result.append(
                SimpleNamespace(
                    name="future_docs_flag",
                    metadata={"label": "Future docs flag", "help": "Docs metadata."},
                )
            )
        elif isinstance(obj, GlobalConfig):
            result = [
                SimpleNamespace(name=f.name, metadata={**f.metadata, "label": "LLM settings"})
                if f.name == "llm"
                else f
                for f in result
            ]
        elif isinstance(obj, type(GlobalConfig().llm)):
            result = [
                SimpleNamespace(name=f.name, metadata={**f.metadata, "label": "Wiki settings"})
                if f.name == "docs"
                else f
                for f in result
            ]
        return result

    monkeypatch.setattr(configure, "fields", extended_fields)
    session, _ = _menu(tmp_path)
    assert "LLM settings" in session.root
    assert "Wiki settings" in session.root
    provider = session.root["LLM settings"]["claude"]
    assert provider["Future model flag"].description == "Provider metadata."
    docs = session.root["Wiki settings"]
    assert docs["Future docs flag"].description == "Docs metadata."


def test_select_update_persists_and_repeated_update_is_noop(tmp_path, monkeypatch):
    session, (ctx, gcfg, _, _) = _menu(tmp_path)
    tag = session.root["LLM"]["codex"]["Configured"]
    calls = []
    original = configure._apply_settings

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(configure, "_apply_settings", counted)
    assert tag.update(True)
    assert "codex" in gcfg.llm.providers
    assert "codex" in store.load_global(ctx.home).llm.providers
    assert len(calls) == 1
    assert tag.update(True)
    assert len(calls) == 1
    assert tag.update(False)
    assert "codex" not in store.load_global(ctx.home).llm.providers
    assert tag.update(True)
    assert "codex" in store.load_global(ctx.home).llm.providers


def test_invalid_edit_keeps_bytes_and_accepts_next_valid_edit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    session, (_, _, pcfg, _) = _menu(tmp_path, root=repo)
    tag = session.root["Worktree (project)"]["Base branch"]
    path = store.project_config_path(repo)
    assert not tag.update("--invalid")
    assert not path.exists()
    assert pcfg.worktree.base_branch == "main"
    assert tag.update("develop")
    assert pcfg.worktree.base_branch == "develop"
    before = path.read_bytes()
    assert tag.update("")
    assert path.read_bytes() == before


def test_project_edit_writes_only_project_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    session, (ctx, _, pcfg, _) = _menu(tmp_path, root=repo)
    tag = session.root["Worktree (project)"]["Base branch"]
    assert tag.update("develop")
    assert pcfg.worktree.base_branch == "develop"
    assert store.load_project(repo).worktree.base_branch == "develop"
    assert not store.global_config_path(ctx.home).exists()
    assert session.flags == (False, True, False)


def test_key_is_masked_and_existing_provider_key_edit_is_secrets_only(tmp_path, monkeypatch):
    session, (ctx, _, _, scfg) = _menu(tmp_path)
    monkeypatch.setattr(docsllm, "validate_key_only", lambda *a, **k: None)
    key = "sk-ant-api03-SECRETKEYabcdefghijklmnop1234"
    tag = session.root["LLM"]["claude"]["API key"]
    assert tag.update(key)
    assert scfg.api_keys["claude"] == key
    assert key not in tag._get_masked_val()
    assert tag._get_masked_val() == "******1234"
    assert store.load_secrets(ctx.home).api_keys["claude"] == key
    assert stat.S_IMODE(store.secrets_path(ctx.home).stat().st_mode) == 0o600
    assert not store.global_config_path(ctx.home).exists()
    assert session.flags == (False, False, True)


def test_key_edit_on_disabled_provider_implicitly_enables_it(tmp_path, monkeypatch):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg, pcfg, scfg = GlobalConfig(), ProjectConfig(), SecretsConfig()
    gcfg.llm.providers.clear()
    session = configure._compose_menu(ctx, None, gcfg, pcfg, scfg)
    monkeypatch.setattr(docsllm, "validate_key_only", lambda *a, **k: None)
    tag = session.root["LLM"]["claude"]["API key"]
    assert tag.update("sk-ant-api03-SECRETKEYabcdefghijklmnop1234")
    assert session.root["LLM"]["claude"]["Configured"].val is True
    assert "claude" in store.load_global(ctx.home).llm.providers
    assert session.flags == (True, False, True)


def test_failed_documentation_probe_preserves_state_then_resolves_model(tmp_path, monkeypatch):
    session, (ctx, gcfg, _, _) = _menu(tmp_path)
    tag = session.root["LLM"]["claude"]["Documentation model"]
    calls = []

    def probe(_ctx, selection, *, say):
        calls.append(selection.model)
        if len(calls) == 1:
            raise docsllm.ProbeFailed("model unavailable")
        return "claude-sonnet-4-6-20261001"

    monkeypatch.setattr(docsllm, "validate_selection", probe)
    assert not tag.update("sonnet")
    assert "model unavailable" in tag._error_text
    assert gcfg.llm.providers["claude"].docs_model == ""
    assert not store.global_config_path(ctx.home).exists()
    assert tag.update("sonnet")
    assert tag.val == "claude-sonnet-4-6-20261001"
    assert store.load_global(ctx.home).llm.providers["claude"].docs_model == tag.val
    assert tag.update(tag.val)
    assert calls == ["sonnet", "sonnet"]


def test_api_backend_without_key_names_required_prior_edit(tmp_path):
    session, (_, gcfg, _, _) = _menu(tmp_path)
    tag = session.root["Documentation"]["Documentation backend"]
    assert not tag.update("api")
    assert "set the provider API key first" in tag._error_text
    assert gcfg.llm.docs.backend == "cli"


def test_other_model_asks_for_id_and_keeps_custom_value_visible(tmp_path):
    session, (ctx, _, _, _) = _menu(tmp_path)

    class Interface:
        def ask(self, label):
            assert label == "Model id"
            return "my-custom-model"

    session.interface = Interface()
    tag = session.root["LLM"]["claude"]["Session model"]
    assert tag.update("__other_model__")
    assert store.load_global(ctx.home).llm.providers["claude"].model == "my-custom-model"
    assert tag._get_selected_key() == "my-custom-model"


def test_real_renderer_two_calls_prompt_other_once_per_edit(tmp_path, monkeypatch):
    session, (ctx, _, _, _) = _menu(tmp_path)
    writes = []
    save_global = store.save_global

    def counted_save(*args):
        writes.append(None)
        return save_global(*args)

    monkeypatch.setattr(store, "save_global", counted_save)

    class Interface:
        calls = 0

        def ask(self, label):
            assert label == "Model id"
            self.calls += 1
            return f"custom-{self.calls}"

    session.interface = Interface()
    tag = session.root["LLM"]["claude"]["Session model"]
    for expected in ("custom-1", "custom-2"):
        tag._on_change_trigger("__other_model__")
        assert tag.update("__other_model__")
        assert tag.val == expected
        assert store.load_global(ctx.home).llm.providers["claude"].model == expected
    assert session.interface.calls == 2
    assert len(writes) == 2


def test_real_renderer_two_calls_probe_resolved_alias_once(tmp_path, monkeypatch):
    session, (ctx, _, _, _) = _menu(tmp_path)
    calls = []
    writes = []
    save_global = store.save_global

    def counted_save(*args):
        writes.append(None)
        return save_global(*args)

    monkeypatch.setattr(store, "save_global", counted_save)

    def resolve(_ctx, selection, *, say):
        calls.append(selection.model)
        return "full-model-id"

    monkeypatch.setattr(docsllm, "validate_selection", resolve)
    tag = session.root["LLM"]["claude"]["Documentation model"]
    tag._on_change_trigger("sonnet")
    assert tag.update("sonnet")
    assert calls == ["sonnet"]
    assert len(writes) == 1
    assert tag.val == "full-model-id"
    assert store.load_global(ctx.home).llm.providers["claude"].docs_model == "full-model-id"


def test_reselect_model_after_provider_toggle_uses_actual_renderer_two_calls(tmp_path):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.default = "codex"
    gcfg.llm.providers["codex"] = configure.ProviderConfig()
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig(), SecretsConfig())
    provider = session.root["LLM"]["claude"]
    model = provider["Session model"]
    configured = provider["Configured"]

    model._on_change_trigger("opus")
    assert model.update("opus")
    assert model.val == "opus"
    assert configured.update(False)
    assert configured.update(True)
    assert model.val == ""

    model._on_change_trigger("opus")
    assert model.update("opus")
    assert model.val == "opus"
    assert store.load_global(ctx.home).llm.providers["claude"].model == "opus"


@pytest.mark.parametrize("interrupt_at", ["probe", "other"])
def test_interrupted_renderer_edit_cannot_save_on_level_submit(tmp_path, monkeypatch, interrupt_at):
    from mininterface.tag import Tag

    session, (ctx, _, _, _) = _menu(tmp_path)
    notifications = session.root["LLM"]["claude"]["Native notifications"]
    assert notifications.update(False)
    before = store.global_config_path(ctx.home).read_bytes()
    calls = []

    def interrupted_probe(_ctx, selection, *, say):
        calls.append(selection.model)
        raise KeyboardInterrupt

    if interrupt_at == "probe":
        monkeypatch.setattr(docsllm, "validate_selection", interrupted_probe)
        candidate = "sonnet"
        tag = session.root["LLM"]["claude"]["Documentation model"]
    else:

        class Interface:
            def ask(self, label):
                raise KeyboardInterrupt

        session.interface = Interface()
        candidate = "__other_model__"
        tag = session.root["LLM"]["claude"]["Session model"]
    with pytest.raises(KeyboardInterrupt):
        tag._on_change_trigger(candidate)
    assert tag.val == ""
    assert Tag._submit_values((leaf, leaf.val) for leaf in session.root["LLM"]["claude"].values())
    assert store.global_config_path(ctx.home).read_bytes() == before
    assert store.load_global(ctx.home).llm.providers["claude"].notifications is False
    assert calls == (["sonnet"] if interrupt_at == "probe" else [])


def test_cancel_other_model_prompt_leaves_stored_value_unchanged(tmp_path):
    from mininterface.exceptions import Cancelled

    session, (ctx, _, _, _) = _menu(tmp_path)

    class Interface:
        def ask(self, label):
            raise Cancelled

    session.interface = Interface()
    tag = session.root["LLM"]["claude"]["Session model"]
    assert tag.update("__other_model__")
    assert tag.val == ""
    assert not store.global_config_path(ctx.home).exists()


def test_run_menu_first_exit_seeds_defaults_and_returns_flags(tmp_path, monkeypatch):
    ctx = ToolContext(home=tmp_path / "home", env={})
    seen = []

    class Interface:
        def form(self, root):
            seen.append(root)

    monkeypatch.setattr(configure, "_text_interface", lambda plain_menu: Interface())
    result = configure._run_menu(ctx, None, GlobalConfig(), ProjectConfig(), SecretsConfig())
    assert result == (True, False, False)
    assert store.load_global(ctx.home).llm.default == "claude"
    assert len(seen) == 1


def test_run_menu_cancel_still_seeds_missing_project_once(tmp_path, monkeypatch):
    from mininterface.exceptions import Cancelled

    root = tmp_path / "repo"
    root.mkdir()
    ctx = ToolContext(home=tmp_path / "home", env={})

    class Interface:
        def form(self, root):
            raise Cancelled

    monkeypatch.setattr(configure, "_text_interface", lambda plain_menu: Interface())
    result = configure._run_menu(ctx, root, GlobalConfig(), ProjectConfig(), SecretsConfig())
    assert result == (True, True, False)
    assert store.load_project(root).worktree.base_branch == "main"


def test_real_text_adaptor_masks_typed_api_key(tmp_path):
    """Exercise actual getpass input on a PTY, not a mocked SecretTag."""
    key = "sk-ant-api03-PTY-SECRET-abcdefghijkl1234"
    source = f"""
from pathlib import Path
from omc import configure, docsllm
from omc.config.schema import GlobalConfig, ProjectConfig, SecretsConfig
from omc.toolctx import ToolContext
docsllm.validate_key_only = lambda *args, **kwargs: None
ctx = ToolContext(home=Path({str(tmp_path / "home")!r}), env={{}})
session = configure._compose_menu(ctx, None, GlobalConfig(), ProjectConfig(), SecretsConfig())
interface = configure._text_interface(True)
tag = session.root['LLM']['claude']['API key']
print('READY', flush=True)
candidate = interface._adaptor.widgetize(tag)
assert tag.update(candidate)
print('MASKED:', interface._adaptor.widgetize(tag, only_label=True), flush=True)
"""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-c", source],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "MININTERFACE_INTERFACE": "min"},
    )
    os.close(slave)
    output = bytearray()

    def until(marker: bytes):
        deadline = time.monotonic() + 8
        while marker not in output:
            assert time.monotonic() < deadline, output.decode(errors="replace")
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    output.extend(os.read(master, 4096))
                except OSError:
                    break
        assert marker in output, output.decode(errors="replace")

    try:
        until(b"API key: ")
        os.write(master, key.encode() + b"\n")
        until(b"MASKED: ******1234")
        proc.wait(timeout=8)
        assert proc.returncode == 0, output.decode(errors="replace")
        assert key.encode() not in output
        assert store.load_secrets(tmp_path / "home").api_keys["claude"] == key
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)


def test_cancel_secret_edit_returns_to_field_loop(tmp_path, monkeypatch):
    import getpass

    from mininterface import interfaces
    from mininterface._text_interface import TextInterface

    fake = object.__new__(TextInterface)
    fake.env = {}
    monkeypatch.setattr(interfaces, "get_interface", lambda *args, **kwargs: fake)

    def cancelled(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(getpass, "getpass", cancelled)
    interface = configure._text_interface(True)
    session, _ = _menu(tmp_path)
    tag = session.root["LLM"]["claude"]["API key"]
    with pytest.raises(KeyboardInterrupt):
        interface._adaptor.widgetize(tag)


def test_disable_edit_reenable_updates_stable_configured_tag(tmp_path):
    session, (ctx, gcfg, _, _) = _menu(tmp_path)
    provider = session.root["LLM"]["claude"]
    toggle = provider["Configured"]
    assert toggle.update(False)
    assert toggle.val is False
    assert "claude" not in gcfg.llm.providers
    assert provider["Session model"].update("sonnet")
    assert toggle.val is True
    assert store.load_global(ctx.home).llm.providers["claude"].model == "sonnet"
    before = store.global_config_path(ctx.home).read_bytes()
    assert toggle.update(True)
    assert store.global_config_path(ctx.home).read_bytes() == before


def test_disabling_provider_with_saved_models_survives_level_revalidation(tmp_path, monkeypatch):
    from mininterface.tag import Tag

    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers["claude"].model = "sonnet"
    gcfg.llm.providers["claude"].docs_model = "opus"
    monkeypatch.setattr(
        docsllm, "validate_selection", lambda _ctx, selection, *, say: selection.model
    )
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig(), SecretsConfig())
    provider = session.root["LLM"]["claude"]
    assert provider["Configured"].update(False)
    persisted = store.global_config_path(ctx.home).read_bytes()
    assert provider["Session model"].val == ""
    assert provider["Documentation model"].val == ""
    assert Tag._submit_values((tag, tag.val) for tag in provider.values())
    assert "claude" not in gcfg.llm.providers
    assert store.global_config_path(ctx.home).read_bytes() == persisted


def test_provider_switch_refreshes_resolved_model_tag_before_revalidation(tmp_path, monkeypatch):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers["codex"] = configure.ProviderConfig(docs_model="alias")
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig(), SecretsConfig())
    calls = []

    def resolve(_ctx, selection, *, say):
        calls.append(selection.model)
        return "resolved-doc-model"

    monkeypatch.setattr(docsllm, "validate_selection", resolve)
    provider_tag = session.root["Documentation"]["Documentation provider"]
    model_tag = session.root["LLM"]["codex"]["Documentation model"]
    assert provider_tag.update("codex")
    assert model_tag.val == "resolved-doc-model"
    assert store.load_global(ctx.home).llm.providers["codex"].docs_model == "resolved-doc-model"
    assert model_tag.update(model_tag.val)
    assert calls == ["alias"]


def test_follow_default_provider_option_saves_empty_value_and_repeats_as_noop(
    tmp_path, monkeypatch
):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers["codex"] = configure.ProviderConfig()
    gcfg.llm.docs.provider = "codex"
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig(), SecretsConfig())
    tag = session.root["Documentation"]["Documentation provider"]
    monkeypatch.setattr(
        docsllm, "validate_selection", lambda _ctx, selection, *, say: selection.model
    )
    apply_calls = []
    original_apply = configure._apply_settings

    def counted_apply(*args, **kwargs):
        apply_calls.append(None)
        return original_apply(*args, **kwargs)

    monkeypatch.setattr(configure, "_apply_settings", counted_apply)
    assert tag.update("")
    assert gcfg.llm.docs.provider == ""
    assert store.load_global(ctx.home).llm.docs.provider == ""
    persisted = store.global_config_path(ctx.home).read_bytes()
    assert tag.update("")
    assert store.global_config_path(ctx.home).read_bytes() == persisted
    assert len(apply_calls) == 1


def test_empty_provider_set_can_be_opened_and_provider_enabled(tmp_path):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers.clear()
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig(), SecretsConfig())
    assert session.root["LLM"]["claude"]["Configured"].val is False
    assert session.root["LLM"]["codex"]["Configured"].update(True)
    assert list(store.load_global(ctx.home).llm.providers) == ["codex"]


def test_custom_stored_model_remains_selectable(tmp_path):
    ctx = ToolContext(home=tmp_path / "home", env={})
    gcfg = GlobalConfig()
    gcfg.llm.providers["claude"].model = "my-custom-model"
    session = configure._compose_menu(ctx, None, gcfg, ProjectConfig(), SecretsConfig())
    tag = session.root["LLM"]["claude"]["Session model"]
    assert "my-custom-model" in tag._build_options().values()
    assert tag._get_selected_key() == "my-custom-model"


def test_first_menu_exit_carries_legacy_project_values(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    ctx = ToolContext(home=tmp_path / "home", env={})
    pcfg = ProjectConfig()
    pcfg.worktree.base_branch = "develop"

    class Interface:
        def form(self, root):
            pass

    monkeypatch.setattr(configure, "_text_interface", lambda plain_menu: Interface())
    assert configure._run_menu(ctx, root, GlobalConfig(), pcfg, SecretsConfig(), legacy=True) == (
        True,
        True,
        False,
    )
    assert store.load_project(root).worktree.base_branch == "develop"


def test_menu_maps_unavailable_text_interface_to_refusal(tmp_path, monkeypatch):
    from mininterface.exceptions import InterfaceNotAvailable

    def unavailable(plain_menu):
        raise InterfaceNotAvailable

    monkeypatch.setattr(configure, "_text_interface", unavailable)
    ctx = ToolContext(home=tmp_path / "home", env={})
    with pytest.raises(Refusal, match="TTY"):
        configure._run_menu(ctx, None, GlobalConfig(), ProjectConfig(), SecretsConfig())


def test_interactive_poststeps_run_once_and_project_only_edit_keeps_legacy(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text('{"schema_version": 1}')
    store.save_global(home, GlobalConfig())
    root = tmp_path / "repo"
    root.mkdir()
    calls = []
    monkeypatch.setattr(configure, "repo_root", lambda ctx: str(root))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def project_only(ctx, root, gcfg, pcfg, scfg, *, legacy=False, plain_menu=False):
        pcfg.worktree.base_branch = "develop"
        store.save_project(root, pcfg)
        return False, True, False

    monkeypatch.setattr(configure, "_run_menu", project_only)
    monkeypatch.setattr(
        configure, "_ensure_instructions", lambda *args: calls.append("instructions")
    )
    monkeypatch.setattr(configure, "_ensure_plugins", lambda *args: calls.append("plugins"))
    ctx = ToolContext(home=home, env={})
    assert configure.run_configure(ctx, defaults=False, sets=[]) == 0
    assert calls == ["instructions", "plugins"]
    assert (home / "config.json").exists()
    assert store.load_project(root).worktree.base_branch == "develop"
