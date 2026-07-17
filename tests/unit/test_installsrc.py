from omc.installsrc import install_source, version_string


def _receipt_env(tmp_path, body: str):
    d = tmp_path / "uvt" / "omc"
    d.mkdir(parents=True)
    (d / "uv-receipt.toml").write_text(body)
    return {"UV_TOOL_DIR": str(tmp_path / "uvt"), "HOME": str(tmp_path)}


def test_source_directory(tmp_path):
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", directory = "/checkout/omc" }]\n',
    )
    assert install_source(env) == ("/checkout/omc", False)


def test_source_git(tmp_path):
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", git = "https://github.com/x/omc" }]\n',
    )
    src, remote = install_source(env)
    assert src == "https://github.com/x/omc" and remote


def test_source_unknown(tmp_path):
    assert install_source({"HOME": str(tmp_path)}) == ("unknown", False)


def test_version_string(tmp_path):
    assert version_string({"HOME": str(tmp_path)}).startswith("omc ")
    assert "from unknown" in version_string({"HOME": str(tmp_path)})


def test_source_non_utf8_receipt_is_unknown(tmp_path):
    d = tmp_path / "uvt" / "omc"
    d.mkdir(parents=True)
    (d / "uv-receipt.toml").write_bytes(b"\xff\xfe\x00broken")
    env = {"UV_TOOL_DIR": str(tmp_path / "uvt"), "HOME": str(tmp_path)}
    assert install_source(env) == ("unknown", False)


def test_source_malformed_requirements_is_unknown(tmp_path):
    env = _receipt_env(tmp_path, '[tool]\nrequirements = "abc"\n')
    assert install_source(env) == ("unknown", False)


def test_source_git_redacts_credentials(tmp_path):
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", '
        'git = "https://oauth2:glpat-abc123@gitlab.example.com/x/omc" }]\n',
    )
    src, remote = install_source(env)
    assert remote
    assert "glpat-abc123" not in src
    assert src == "https://[REDACTED]@gitlab.example.com/x/omc"
