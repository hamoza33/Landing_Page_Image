"""Backwards-compatible facade — delegates to :class:`ProductResearcher`.

Older code paths (tests, regenerate helpers) imported ``ProductAnalyzer``.
This wrapper preserves the import path while the real work happens in
``researcher.py`` (vision + optional Tavily web research + consolidation).
"""

from __future__ import annotations

from app.config import Settings, settings as default_settings
from app.schemas import ProductBrief
from app.services.researcher import ProductResearcher
from app.services.yunwu_client import YunwuClient


class ProductAnalyzer:
    def __init__(
        self,
        client: YunwuClient | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self._researcher = ProductResearcher(client=client, settings=self.settings)

    async def analyze(self, image_bytes: bytes, *, mime: str = "image/jpeg") -> ProductBrief:
        return await self._researcher.research(image_bytes, mime=mime)
