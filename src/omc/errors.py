class OmcError(Exception):
    """Expected failure: printed without traceback, exit code 1."""

    rc = 1


class Refusal(OmcError):
    """Deliberate refusal (bad input, unmet precondition): exit code 2."""

    rc = 2


class ConfigError(OmcError):
    """Invalid or unreadable config file."""
