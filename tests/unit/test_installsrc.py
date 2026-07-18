from omc import _buildinfo, installsrc
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


def test_package_root_is_the_omc_package_dir():
    from omc.installsrc import package_root

    root = package_root()
    assert root.is_dir()
    assert (root / "__init__.py").is_file()
    assert root.name == "omc"


def test_provenance_fallback_is_unknown():
    prov = installsrc.provenance()
    assert set(prov) == {"branch", "commit", "source"}
    # the checked-in fallback ships all-unknown; a stamped build overwrites it
    assert prov["branch"] == "unknown"
    assert prov["commit"] == "unknown"
    assert prov["source"] == "unknown"
    prov["branch"] = "mutated"
    assert installsrc.provenance()["branch"] == "unknown"  # fresh dict per call


def _prov(monkeypatch, branch="unknown", commit="unknown", source="unknown"):
    monkeypatch.setattr(_buildinfo, "BRANCH", branch)
    monkeypatch.setattr(_buildinfo, "COMMIT", commit)
    monkeypatch.setattr(_buildinfo, "SOURCE", source)


def test_version_plain_when_provenance_unknown(tmp_path, monkeypatch):
    _prov(monkeypatch)
    out = version_string({"HOME": str(tmp_path)})
    assert "(" not in out and out.endswith("from unknown")


def test_version_with_provenance_directory_install(tmp_path, monkeypatch):
    _prov(monkeypatch, "main", "abc1234", "git@github.com:x/omc.git")
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", directory = "/checkout/omc" }]\n',
    )
    out = version_string(env)
    assert "(main@abc1234)" in out
    assert "from /checkout/omc" in out
    assert out.endswith("(origin git@github.com:x/omc.git)")


def test_version_remote_git_install_omits_origin(tmp_path, monkeypatch):
    _prov(monkeypatch, "main", "abc1234", "https://github.com/x/omc")
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", git = "https://github.com/x/omc" }]\n',
    )
    out = version_string(env)
    assert "(main@abc1234)" in out
    assert "origin" not in out  # from-URL already IS the remote


def test_version_origin_is_redacted(tmp_path, monkeypatch):
    # belt+braces: display-side redaction even if a credentialed URL reached _buildinfo
    _prov(monkeypatch, "main", "abc1234", "https://oauth2:tok@gitlab.example.com/x/omc")
    env = _receipt_env(
        tmp_path,
        '[tool]\nrequirements = [{ name = "omc", directory = "/checkout/omc" }]\n',
    )
    out = version_string(env)
    assert "tok" not in out
    assert "(origin https://[REDACTED]@gitlab.example.com/x/omc)" in out
