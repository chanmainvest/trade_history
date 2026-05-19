"""Extractor package — imports trigger registry decoration."""

from trade_history.extractors import registry  # noqa: F401

# Import each institution's extractor module to trigger @ExtractorRegistry.register
from trade_history.extractors.cibc import imperial_service, investors_edge  # noqa: F401
from trade_history.extractors.hsbc import investdirect  # noqa: F401
from trade_history.extractors.rbc import direct_investing  # noqa: F401
from trade_history.extractors.td import webbroker  # noqa: F401
