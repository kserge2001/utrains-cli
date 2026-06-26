"""Lightweight tests that don't need Ollama or a network — just verify the wiring."""

import pytest

from utrains import agent, executor, prompts
from utrains.system_info import recommend_model, system_summary


def test_model_recommendation_by_ram():
    assert recommend_model(4) == "llama3.2:3b"
    assert recommend_model(12) == "llama3.1:8b"
    assert recommend_model(24) == "qwen2.5:14b"
    assert recommend_model(64) == "qwen2.5:32b"
    assert recommend_model(None) == "llama3.1:8b"  # unknown RAM → safe default


def test_dangerous_detection():
    assert executor.is_dangerous("rm -rf /")
    assert executor.is_dangerous("git push --force origin main")
    assert executor.is_dangerous("DROP TABLE users")
    assert not executor.is_dangerous("ls -la")
    assert not executor.is_dangerous("docker ps")


def test_run_command_captures_output():
    result = executor.run_command("echo hello-utrains")
    assert result["returncode"] == 0
    assert "hello-utrains" in result["stdout"]


def test_md_renders_table_and_code(monkeypatch):
    """The classic markdown renderer turns a table + fenced code into boxed output."""
    from utrains import ui
    monkeypatch.setattr(ui, "_enabled", lambda: False)   # plain text, easy to assert
    out = ui.md("## Title\n| Step | Status |\n| --- | --- |\n| Build | ✅ done |\n"
                "```\nkubectl get pods\n```")
    assert "Title" in out
    assert "Step" in out and "Status" in out and "Build" in out
    assert "│" in out                       # table drawn as a box
    assert "kubectl get pods" in out        # code block preserved


def test_fuzzy_cd_resolves_loose_name(tmp_path, monkeypatch):
    """A loosely-typed `cd` resolves to the closest real folder; exact/absolute/
    non-cd commands are left untouched."""
    (tmp_path / "GITHUB_ACTION_DEVOPS").mkdir()
    (tmp_path / ".github").mkdir()
    monkeypatch.chdir(tmp_path)
    assert executor.closest_existing_dir("github_action") == "GITHUB_ACTION_DEVOPS"
    assert executor.resolve_cd_command("cd github_action") == (
        'cd "GITHUB_ACTION_DEVOPS"', "GITHUB_ACTION_DEVOPS")
    assert executor.resolve_cd_command("Set-Location ./github_action")[1] == "GITHUB_ACTION_DEVOPS"
    assert executor.resolve_cd_command("cd GITHUB_ACTION_DEVOPS")[1] is None   # already real
    assert executor.resolve_cd_command("ls -la")[1] is None                    # not a cd


def test_cd_persists_across_commands(tmp_path):
    """A `cd` in one tracked command must carry over to the next, and the cwd
    marker must never leak into the output the user/model sees."""
    import os
    start = os.getcwd()
    target = str(tmp_path)
    try:
        r1 = executor.run_command(f'cd "{target}"', track_cwd=True)
        assert "UTRAINS_CWD" not in r1["stdout"]          # marker hidden
        assert os.path.samefile(os.getcwd(), target)      # process moved
        # a brand-new command starts in the persisted directory
        out = []
        executor.run_command("pwd" if os.name != "nt" else "(Get-Location).Path",
                             on_output=lambda t, term: out.append(t), track_cwd=True)
        assert os.path.samefile(" ".join(out).strip(), target)
    finally:
        os.chdir(start)


def test_parse_step_handles_clean_json():
    step = agent._parse_step('{"thought":"x","command":"ls","done":false}')
    assert step["command"] == "ls"
    assert step["done"] is False


def test_parse_step_handles_wrapped_json():
    step = agent._parse_step('Sure! {"command": null, "done": true, "final_answer": "ok"}')
    assert step["done"] is True
    assert step["final_answer"] == "ok"


def test_parse_step_falls_back_to_text():
    step = agent._parse_step("no json here")
    assert step["done"] is True  # never hang the loop


def test_system_prompt_lists_machine():
    prompt = prompts.build_system_prompt(system_summary())
    assert "utrains" in prompt
    assert "JSON" in prompt
    assert "CREDENTIALS" in prompt  # trusts existing aws/kube/gh logins


def test_prompt_without_mcp_omits_tool_field():
    prompt = prompts.build_system_prompt(system_summary())
    assert '"tool"' not in prompt


def test_prompt_with_mcp_and_memory():
    prompt = prompts.build_system_prompt(
        system_summary(),
        context="prod cluster is eks-east",
        mcp_tools=[{"name": "github.create_issue", "description": "open an issue"}],
    )
    assert "github.create_issue" in prompt
    assert "eks-east" in prompt
    assert '"tool"' in prompt  # contract gains the MCP fields


def test_parse_step_handles_tool_call():
    step = agent._parse_step(
        '{"thought":"x","tool":"github.list_repos","tool_args":{"org":"acme"},"done":false}')
    assert step["tool"] == "github.list_repos"
    assert step["tool_args"] == {"org": "acme"}


def test_ui_style_plain_when_not_tty():
    from utrains import ui
    # In the test runner stdout isn't a real terminal, so no escape codes leak.
    assert ui.style("hello", "accent") == "hello"


def test_memory_context_empty_when_disabled(monkeypatch):
    from utrains import memory
    monkeypatch.setattr(memory, "is_enabled", lambda: False)
    assert memory.build_context(["did a thing"]) == ""


def test_tui_noise_filter():
    from utrains import tui
    assert tui._is_noise("   ")
    assert tui._is_noise("- \\ | /")          # pure spinner frames
    assert not tui._is_noise("Downloading 45%")


def test_tui_app_constructs():
    from utrains import tui
    app = tui.UtrainsApp(model="test-model")
    assert app.model == "test-model"
    assert app._busy is False


def test_provider_detection():
    from utrains import providers
    assert providers.detect_provider("claude-opus-4-8") == "anthropic"
    assert providers.detect_provider("claude-sonnet-4-6") == "anthropic"
    assert providers.detect_provider("gpt-4.1") == "openai"
    assert providers.detect_provider("o3-mini") == "openai"
    assert providers.detect_provider("qwen2.5:14b") == "ollama"
    assert providers.detect_provider("llama3.2:3b") == "ollama"


def test_provider_missing_key_errors(monkeypatch):
    from utrains import providers
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "hi"}]
    for model in ("claude-opus-4-8", "gpt-4.1"):
        with pytest.raises(providers.ProviderError):
            providers.chat(model, msgs)


def test_anthropic_system_split():
    from utrains import providers
    system, convo = providers._split_system([
        {"role": "system", "content": "A"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ])
    assert system == "A"
    assert [m["role"] for m in convo] == ["user", "assistant"]