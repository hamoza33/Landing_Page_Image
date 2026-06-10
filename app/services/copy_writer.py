"""Arabic copy generator — one Yunwu Responses-API call for all 8 sections."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.schemas import LandingCopy, ProductBrief
from app.services.yunwu_client import YunwuClient, YunwuError
from app.config import Settings, settings as default_settings

log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class CopyWriter:
    """Generate Arabic GCC landing-page copy from a product brief."""

    def __init__(
        self,
        client: YunwuClient | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or default_settings
        self.client = client or YunwuClient(self.settings)
        self._system = (_PROMPTS_DIR / "copy_system_ar.txt").read_text(encoding="utf-8")

    async def generate(self, brief: ProductBrief) -> LandingCopy:
        """Generate 8-section Arabic copy for the given brief."""

        user_text = self._build_user_prompt(brief)
        text = await self.client.respond(
            instructions=self._system,
            user_content=user_text,
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        return _parse_copy(text)

    @staticmethod
    def _build_user_prompt(brief: ProductBrief) -> str:
        lines = [
            "معلومات المنتج:",
            f"- الاسم: {brief.name}",
            f"- الفئة: {brief.category}",
            f"- الاستخدام: {brief.primary_use}",
            f"- المستخدم المستهدف: {brief.target_user}",
        ]
        if brief.materials:
            lines.append(f"- المواد/المكونات: {', '.join(brief.materials)}")
        if brief.benefits:
            lines.append(f"- الفوائد: {', '.join(brief.benefits)}")
        lines.append(
            "\nأنشئ نصوص صفحة الهبوط كاملة لهذا المنتج بصيغة JSON حسب التعليمات."
        )
        return "\n".join(lines)


# --------------------------------------------------------------------- helpers


def _parse_copy(text: str) -> LandingCopy:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            first, rest = cleaned.split("\n", 1)
            if first.lower().strip() in {"json", "json5"}:
                cleaned = rest
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise YunwuError(f"CopyWriter returned non-JSON: {text[:400]}") from exc
    try:
        return LandingCopy.model_validate(data)
    except ValidationError as exc:
        raise YunwuError(f"CopyWriter JSON failed schema: {exc}") from exc
