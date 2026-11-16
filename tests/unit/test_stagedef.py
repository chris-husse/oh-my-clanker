import errno
import os
import re

import pytest

from omc import stagedef
from omc.stagedef import (
    HEADINGS,
    STAGE_TEMPLATE,
    Limits,
    StageBroken,
    check_hook,
    format_duration,
    hook_path,
    hook_state,
    parse_duration,
    parse_instructions,
    refresh_template,
    show_instructions,
)
from omc.toolctx import ToolContext

from . import _stagekit


def _ctx():
    return ToolContext.from_env(_stagekit.hermetic_env())


def _old_skill(root, stage):
    skill = root / ".omc" / "skills" / stage / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: check\n---\n")


def _sections(**bodies):
    """Instructions from VALID_INSTRUCTIONS with the named sections' bodies replaced."""
    names = {
        "limits": "Limits",
        "done": "Done when",
        "passed": "Passed when",
        "fixing": "Fixing failures",
        "subset": "Rerunning a subset",
    }
    current = {
        "limits": "Timeout: none\nQuiet timeout: none",
        "done": "The hook exits.",
        "passed": "Every unit test passes.",
        "fixing": "Read the log; never edit the stage itself.",
        "subset": "Pass test paths as arguments.",
    }
    current.update(bodies)
    return "".join(
        f"## {names[key]}\n{body}\n\n" for key, body in current.items() if body is not None
    )


def _refused(text):
    with pytest.raises(StageBroken) as exc:
        parse_instructions(text)
    return str(exc.value)


# --- hook states (§6.2) ---


def test_hook_states(tmp_path):
    absent = tmp_path / "absent"
    absent.mkdir()
    assert hook_state(absent, "check") == "absent"
    check_hook(absent, "check")

    unmigrated = tmp_path / "unmigrated"
    _old_skill(unmigrated, "check")
    assert hook_state(unmigrated, "check") == "unmigrated"
    with pytest.raises(StageBroken) as exc:
        check_hook(unmigrated, "check")
    assert str(exc.value) == (
        "CRITICAL STOP: migrate this stage with /omc:integrate "
        "(.omc/skills/check/SKILL.md exists, .omc/bin/check does not)"
    )

    present = tmp_path / "present"
    hook = _stagekit.write_hook(present, "build")
    assert hook == hook_path(present, "build") == present / ".omc/bin/build"
    assert hook_state(present, "build") == "present"
    check_hook(present, "build")

    broken = tmp_path / "broken"
    _stagekit.write_hook(broken, "verify", executable=False)
    assert hook_state(broken, "verify") == "not-executable"
    with pytest.raises(StageBroken) as exc:
        check_hook(broken, "verify")
    assert str(exc.value) == "CRITICAL STOP: .omc/bin/verify is not executable"

    both = tmp_path / "both"
    _old_skill(both, "check")
    _stagekit.write_hook(both, "check")
    assert hook_state(both, "check") == "present"
    check_hook(both, "check")


@pytest.mark.parametrize("old_skill", [False, True], ids=["alone", "beside-old-skill"])
def test_hook_with_sh_extension_is_unmigrated(tmp_path, old_skill):
    # U14: stage hooks have no extension; a `.sh` hook is never run, and never unconfigured
    if old_skill:
        _old_skill(tmp_path, "build")
    old = _stagekit.write_hook(tmp_path, "build")
    old.rename(old.with_name("build.sh"))
    assert hook_state(tmp_path, "build") == "unmigrated"
    with pytest.raises(StageBroken) as exc:
        check_hook(tmp_path, "build")
    assert str(exc.value) == (
        "CRITICAL STOP: rename .omc/bin/build.sh to .omc/bin/build (stage hooks have no extension)"
    )


# --- instruction parsing (§6.3) ---


def test_valid_instructions_parse():
    parsed = parse_instructions(_stagekit.VALID_INSTRUCTIONS)
    assert parsed.limits == Limits(timeout_s=None, quiet_s=None, fix_cycles=None)
    assert parsed.subset == "Pass test paths as arguments."
    assert parsed.warnings == ()
    assert parsed.text == _stagekit.VALID_INSTRUCTIONS


_REFUSED = object()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Fix cycles: 4", 4),
        ("Fix cycles: 10", 10),
        # no bound of the stage's own: omc's default (the layer's ten) applies
        (None, None),
        ("Fix cycles: none", None),
        ("Fix cycles: 11", _REFUSED),
        ("Fix cycles: 0", _REFUSED),
        ("Fix cycles: four", _REFUSED),
        ("Fix cycles: \u0664", _REFUSED),  # ARABIC-INDIC DIGIT FOUR: decimal, but not ASCII
    ],
)
def test_fix_cycles(line, expected):
    limits = "Timeout: 45m\nQuiet timeout: 5m" + (f"\n{line}" if line else "")
    text = _sections(limits=limits)
    if expected is _REFUSED:
        assert _refused(text) == f'CRITICAL STOP: ## Limits: Fix cycles must be 1 to 10 in "{line}"'
    else:
        assert parse_instructions(text).limits == Limits(2700.0, 300.0, expected)


def test_durations():
    assert parse_duration("90s") == 90.0
    assert parse_duration("45m") == 2700.0
    assert parse_duration("2h") == 7200.0
    assert parse_duration("none") is None
    for bad in ("5 minutes", "45m\n", "\u0664\u0665m", "45M", " 45m"):
        with pytest.raises(ValueError):
            parse_duration(bad)
    assert _refused(_sections(limits="Timeout: 5 minutes\nQuiet timeout: none")) == (
        'CRITICAL STOP: ## Limits: duration does not parse in "Timeout: 5 minutes"'
    )
    assert format_duration(2700.0) == "45m"
    assert format_duration(7200.0) == "2h"
    assert format_duration(90.0) == "90s"
    assert format_duration(0) == "0s"
    assert format_duration(0.5) == "0.5s"


@pytest.mark.parametrize(
    ("text", "sentence"),
    [
        (
            _sections() + "## Notes\nextra\n",
            'CRITICAL STOP: unknown heading "## Notes"',
        ),
        (
            _sections(passed=None),
            'CRITICAL STOP: required heading "## Passed when" missing',
        ),
        (
            "\n  \nIntro line\n" + _sections(),
            'CRITICAL STOP: text before the first heading: "Intro line"',
        ),
        (
            _sections(subset="x\n\n## Done when\ny"),
            'CRITICAL STOP: repeated heading "## Done when"',
        ),
        (
            _sections(done=None) + "## Done when\nlate\n",
            'CRITICAL STOP: heading "## Done when" out of order',
        ),
    ],
    ids=["extra", "missing", "before-first", "repeated", "out-of-order"],
)
def test_structure_refusals(text, sentence):
    assert _refused(text) == sentence


def test_limits_unknown_line():
    text = _sections(limits="Timeout: none\nQuiet timeout: none\nRetries: 3")
    assert _refused(text) == 'CRITICAL STOP: ## Limits has an unknown line "Retries: 3"'
    repeated = _sections(limits="Timeout: none\nQuiet timeout: none\nTimeout: 5m")
    assert _refused(repeated) == 'CRITICAL STOP: ## Limits has an unknown line "Timeout: 5m"'
    missing = _sections(limits="Timeout: none")
    assert _refused(missing) == 'CRITICAL STOP: ## Limits is missing "Quiet timeout:"'
    missing = _sections(limits="Quiet timeout: none")
    assert _refused(missing) == 'CRITICAL STOP: ## Limits is missing "Timeout:"'


def test_section_over_5kb():
    body = "é" * 2561  # 5122 UTF-8 bytes, 2561 characters
    assert _refused(_sections(fixing=body)) == (
        'CRITICAL STOP: section "## Fixing failures" is 5122 bytes, over the 5120-byte limit'
    )
    parse_instructions(_sections(fixing="é" * 2560))


def test_total_over_20kb():
    chunk = "x" * 5100
    text = _sections(done=chunk, passed=chunk, fixing=chunk, subset=chunk)
    size = len(text.encode())
    assert size > 20480
    assert _refused(text) == (
        f"CRITICAL STOP: instructions are {size} bytes, over the 20480-byte limit"
    )


def test_crlf_and_empty_section_are_valid():
    text = _sections(done="").replace("\n", "\r\n")
    parsed = parse_instructions(text)
    assert parsed.limits == Limits(None, None, None)
    assert "\r" not in parsed.text
    assert parsed.subset == "Pass test paths as arguments."
    # A `### ` line and a `## ` line inside a code fence: the first is content,
    # the second is still a heading.
    assert parse_instructions(_sections(done="### Details\nthe hook exits")).subset is not None
    fenced = _sections(done="```\n## Example\n```")
    assert _refused(fenced) == 'CRITICAL STOP: unknown heading "## Example"'


def test_leading_bom_is_stripped():
    parsed = parse_instructions("\ufeff" + _stagekit.VALID_INSTRUCTIONS)
    assert parsed.text == _stagekit.VALID_INSTRUCTIONS
    assert parsed.limits == Limits(None, None, None)


def test_subset_heading_optional():
    parsed = parse_instructions(_sections(subset=None))
    assert parsed.subset is None
    assert parsed.limits == Limits(None, None, None)


# --- show_instructions ---


def test_show_instructions_answers(tmp_path):
    _stagekit.write_hook(tmp_path, "check")
    parsed = show_instructions(_ctx(), tmp_path, "check")
    assert parsed.text == _stagekit.VALID_INSTRUCTIONS
    assert parsed.warnings == ()


def test_show_instructions_slow_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(stagedef, "SHOW_STOP_S", 0.5)
    _stagekit.write_hook(tmp_path, "verify", show_delay=1)
    with pytest.raises(StageBroken) as exc:
        show_instructions(_ctx(), tmp_path, "verify")
    assert str(exc.value) == (
        "CRITICAL STOP: .omc/bin/verify --show-instructions did not answer within 0.5s"
    )


def test_show_instructions_warns_over_warn_s(tmp_path, monkeypatch):
    monkeypatch.setattr(stagedef, "SHOW_WARN_S", 0.2)
    monkeypatch.setattr(stagedef, "SHOW_STOP_S", 5)
    _stagekit.write_hook(tmp_path, "build", show_delay=0.4)
    parsed = show_instructions(_ctx(), tmp_path, "build")
    assert len(parsed.warnings) == 1
    assert re.fullmatch(
        r"\.omc/bin/build --show-instructions took \d+\.\ds \(over 0\.2s\)", parsed.warnings[0]
    )
    assert parsed.limits == Limits(None, None, None)


def test_show_instructions_nonzero_and_empty(tmp_path):
    hook = _stagekit.write_hook(tmp_path, "check")
    hook.write_text("#!/bin/sh\nexit 3\n")
    with pytest.raises(StageBroken) as exc:
        show_instructions(_ctx(), tmp_path, "check")
    assert str(exc.value) == "CRITICAL STOP: .omc/bin/check --show-instructions exited 3"

    _stagekit.write_hook(tmp_path, "check", instructions="")
    with pytest.raises(StageBroken) as exc:
        show_instructions(_ctx(), tmp_path, "check")
    assert str(exc.value) == "CRITICAL STOP: .omc/bin/check --show-instructions printed nothing"


@pytest.mark.parametrize(
    ("script", "code"),
    [("exit 0\n", errno.ENOEXEC), ("#!/nonexistent/sh\nexit 0\n", errno.ENOENT)],
    ids=["no-shebang", "missing-interpreter"],
)
def test_show_instructions_hook_that_cannot_start(tmp_path, script, code):
    hook = _stagekit.write_hook(tmp_path, "check")
    hook.write_text(script)
    with pytest.raises(StageBroken) as exc:
        show_instructions(_ctx(), tmp_path, "check")
    assert str(exc.value) == (
        f"CRITICAL STOP: .omc/bin/check --show-instructions could not start: {os.strerror(code)}"
    )


# --- template ---


def test_template_headings_match_validator():
    headings = tuple(line[3:] for line in STAGE_TEMPLATE.splitlines() if line.startswith("## "))
    assert headings == HEADINGS
    for example in ("Timeout: 45m", "Quiet timeout: 5m", "Fix cycles: 4"):
        assert example in STAGE_TEMPLATE.splitlines()
    assert "none" in STAGE_TEMPLATE
    # a stage's bound only lowers omc's default, the layer's ten cycles
    flat = " ".join(STAGE_TEMPLATE.split())
    assert "it lowers omc's default of 10 fix cycles for this stage" in flat


def _without_delete_block(template: str) -> str:
    """The template with its `Delete this paragraph:` block (up to the blank line) cut."""
    head, sep, tail = template.partition("Delete this paragraph:")
    assert sep, "the template marks no paragraph to delete"
    return head + tail.split("\n\n", 1)[1]


def test_template_parses_once_its_marked_guidance_is_deleted():
    """The template's shape is valid instructions once the marked `## Limits` paragraph
    is gone, which is what integrate tells a drafter to do."""
    parsed = parse_instructions(_without_delete_block(STAGE_TEMPLATE))
    assert parsed.limits == Limits(2700.0, 300.0, 4)
    assert parsed.subset


def test_template_verbatim_is_refused_at_its_marked_guidance():
    """The validator stays strict: the guidance is marked, so the refusal names it."""
    limits = STAGE_TEMPLATE.split("## Limits\n", 1)[1].split("\n## ", 1)[0].split("\n")
    guidance = next(line for line in limits if line.startswith("Delete this paragraph: "))
    with pytest.raises(StageBroken) as info:
        parse_instructions(STAGE_TEMPLATE)
    assert str(info.value) == f'CRITICAL STOP: ## Limits has an unknown line "{guidance}"'


def test_template_done_when_says_the_result_word_is_one_write():
    """The supervisor reads the result file as soon as it holds a word, so the hook
    writes the whole word at once."""
    done = " ".join(STAGE_TEMPLATE.split("## Done when\n", 1)[1].split("\n## ", 1)[0].split())
    assert 'in one write (`echo failed > "$OMC_STAGE_RESULT"`)' in done


def test_refresh_template_writes_only_into_existing_bin(tmp_path):
    template = tmp_path / ".omc" / "bin" / "INSTRUCTIONS.template.md"
    assert refresh_template(tmp_path) is False
    assert not template.exists()
    assert not template.parent.exists()

    template.parent.mkdir(parents=True)
    assert refresh_template(tmp_path) is True
    assert template.read_text() == STAGE_TEMPLATE
    assert template.stat().st_mode & 0o777 == 0o644
    assert refresh_template(tmp_path) is False

    template.write_text("edited\n")
    template.chmod(0o600)
    assert refresh_template(tmp_path) is True
    assert template.read_text() == STAGE_TEMPLATE
    assert template.stat().st_mode & 0o777 == 0o644
    assert sorted(p.name for p in template.parent.iterdir()) == ["INSTRUCTIONS.template.md"]
