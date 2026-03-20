from __future__ import annotations

import pytest

from flow_core.config.models import PipelineConfig, ProviderMap
from flow_core.orchestration.cache import InMemoryQuoteCache
from flow_core.orchestration.pipeline import QuantPipelineService
from flow_core.storage.parquet_store import ParquetStore


def test_live_strict_requires_quantcore_when_cpp_backends_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    orig_find_spec = __import__("importlib.util").util.find_spec

    def _fake_find_spec(name: str):  # noqa: ANN001
        if name == "quantcore":
            return None
        return orig_find_spec(name)

    monkeypatch.setattr("flow_core.orchestration.pipeline.importlib.util.find_spec", _fake_find_spec)

    with pytest.raises(RuntimeError, match="runtime_mode=live_strict requires quantcore"):
        QuantPipelineService(
            adapter=None,  # type: ignore[arg-type]
            provider_map=ProviderMap(provider="test", required_fields=[], field_map={}),
            config=PipelineConfig(runtime_mode="live_strict", ssvi_backend="cpp", fdm_backend="cpp"),
            cache=InMemoryQuoteCache(),
            parquet_store=ParquetStore(str(tmp_path / "raw")),
            derived_store=None,
        )
