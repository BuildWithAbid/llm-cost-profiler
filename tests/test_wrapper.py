"""Tests for the wrapper proxy pattern."""

import sys
import types
from unittest.mock import MagicMock

from llm_cost_profiler.wrapper import (
    ClientProxy,
    ProviderAdapter,
    ResourceProxy,
    _get_call_site,
    _hash_messages,
)


class TestHashMessages:
    def test_deterministic(self):
        h1 = _hash_messages([{"role": "user", "content": "hello"}])
        h2 = _hash_messages([{"role": "user", "content": "hello"}])
        assert h1 == h2

    def test_different_messages(self):
        h1 = _hash_messages([{"role": "user", "content": "hello"}])
        h2 = _hash_messages([{"role": "user", "content": "world"}])
        assert h1 != h2

    def test_none_returns_none(self):
        assert _hash_messages(None) is None


class TestCallSite:
    def test_returns_tuple(self):
        site, func = _get_call_site()
        # Should find this test function
        assert site is not None
        assert "test_wrapper" in site
        assert func is not None


class TestClientProxy:
    def test_basic_attribute_access(self):
        mock_client = MagicMock()
        mock_client.api_key = "sk-test"
        adapter = ProviderAdapter()
        proxy = ClientProxy(mock_client, adapter)

        # Non-resource attributes pass through
        assert proxy.api_key == "sk-test"

    def test_repr(self):
        mock_client = MagicMock()
        adapter = ProviderAdapter()
        proxy = ClientProxy(mock_client, adapter)
        assert "ClientProxy" in repr(proxy)


class TestResourceProxy:
    def test_callable_interception(self):
        """Test that tracked methods get intercepted."""
        mock_resource = MagicMock()

        # Simulate a response with usage info
        mock_response = MagicMock()
        mock_resource.create = MagicMock(return_value=mock_response)

        adapter = ProviderAdapter()
        adapter.tracked_methods = {"create"}

        proxy = ResourceProxy(mock_resource, adapter, store_prompts=False)

        # The create method should be intercepted (returns a wrapper function)
        create_fn = proxy.create
        assert callable(create_fn)

    def test_non_tracked_passthrough(self):
        """Non-tracked methods pass through unchanged."""
        mock_resource = MagicMock()
        mock_resource.list = MagicMock(return_value=[1, 2, 3])

        adapter = ProviderAdapter()
        adapter.tracked_methods = {"create"}

        proxy = ResourceProxy(mock_resource, adapter, store_prompts=False)
        result = proxy.list()
        assert result == [1, 2, 3]


class TestProviderAdapter:
    def test_extract_model(self):
        adapter = ProviderAdapter()
        assert adapter.extract_model({"model": "gpt-4o"}) == "gpt-4o"
        assert adapter.extract_model({}) == "unknown"

    def test_extract_tokens_default(self):
        adapter = ProviderAdapter()
        assert adapter.extract_tokens(MagicMock()) == (0, 0)

    def test_extract_error_type(self):
        adapter = ProviderAdapter()
        assert adapter.extract_error_type(ValueError("test")) == "ValueError"
