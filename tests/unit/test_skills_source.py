import pytest

from omc.errors import OmcError
from omc.skills_source import skill_text


def test_skill_text_resolves_slug_skill():
    text = skill_text("slug")
    assert "OMC_SLUG" in text and "$ARGUMENTS" in text


def test_skill_text_missing():
    with pytest.raises(OmcError, match="no-such-skill"):
        skill_text("no-such-skill")
