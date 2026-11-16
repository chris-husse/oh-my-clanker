"""Unit-tier seam (spec 2026-11-16 §5): embedding is always on in the product,
but a unit test never installs GitNexus's embedding runtime unless it carries
@pytest.mark.real_embeddings. The marked tests and the real-tool E2E cover the
embedding paths; everything else pins index behavior keyword-only."""

import pytest


@pytest.fixture(autouse=True)
def _keyword_only_unless_real_embeddings(request, monkeypatch):
    if request.node.get_closest_marker("real_embeddings") is None:
        monkeypatch.setattr("omc.gitnexus.ensure_embeddings", lambda ctx, **kw: False)
