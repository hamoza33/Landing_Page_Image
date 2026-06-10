"""Pydantic schemas shared across services."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SECTION_KEYS: tuple[str, ...] = (
    "hero",
    "features",
    "before_after",
    "testimonials",
    "faq",
    "lifestyle",
    "education",
    "closing",
)


class ProductBrief(BaseModel):
    """Structured output of the vision/analyzer step."""

    name: str
    category: str
    materials: list[str] = Field(default_factory=list)
    target_user: str = ""
    primary_use: str = ""
    benefits: list[str] = Field(default_factory=list)
    visual_style_keywords: list[str] = Field(default_factory=list)


class HeroCopy(BaseModel):
    headline: str
    subhead: str
    cta: str


class FeatureItem(BaseModel):
    title: str
    description: str


class FeaturesCopy(BaseModel):
    headline: str
    items: list[FeatureItem]


class BeforeAfterCopy(BaseModel):
    headline: str
    before: str
    after: str


class Testimonial(BaseModel):
    name: str
    location: str
    quote: str


class TestimonialsCopy(BaseModel):
    headline: str
    items: list[Testimonial]


class FaqItem(BaseModel):
    question: str
    answer: str


class FaqCopy(BaseModel):
    headline: str
    items: list[FaqItem]


class LifestyleCopy(BaseModel):
    headline: str
    body: str


class EducationCopy(BaseModel):
    headline: str
    body: str


class ClosingCopy(BaseModel):
    headline: str
    body: str
    cta: str


class LandingCopy(BaseModel):
    """Full 8-section Arabic copy for the landing page."""

    hero: HeroCopy
    features: FeaturesCopy
    before_after: BeforeAfterCopy
    testimonials: TestimonialsCopy
    faq: FaqCopy
    lifestyle: LifestyleCopy
    education: EducationCopy
    closing: ClosingCopy

    def section(self, key: str) -> BaseModel:
        return getattr(self, key)


JobStatus = Literal["pending", "running", "done", "error"]
SectionStatus = Literal["pending", "running", "done", "error"]


class JobSection(BaseModel):
    """Per-section state for a job."""

    key: str
    index: int
    status: SectionStatus = "pending"
    prompt: str = ""
    image_path: str | None = None
    error: str | None = None


class JobRecord(BaseModel):
    id: str
    created_at: float = 0.0  # unix epoch
    status: JobStatus = "pending"
    step: str = "queued"
    error: str | None = None
    product_name: str | None = None  # filled after analyze
    upload_path: str | None = None  # the user's original photo
    sections: list[JobSection] = Field(default_factory=list)
    copy_path: str | None = None
    brief_path: str | None = None
