from omc import __version__
from omc.errors import ConfigError, OmcError, Refusal


def test_error_rcs():
    assert OmcError("x").rc == 1
    assert Refusal("x").rc == 2
    assert ConfigError("x").rc == 1
    assert isinstance(Refusal("x"), OmcError)


def test_version_present():
    assert __version__
