"""Document classification: categories, authors, types and subjects.

A document is classified along four axes:

1. ``category``: category of the source (government, manufacturer, standards body…);
2. ``author``: author or publisher, plus an optional ``distributor``;
3. ``type``: document type, linked to its own Docling configuration;
4. ``subjects``: subjects (free list, with a suggested vocabulary).

The suggested categories, types and subjects live in ``<output_dir>/types.toml``,
created from ``default_types.toml`` on first use, so the classification is
versioned together with the extracted data.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import tomli_w

TYPES_FILE = "types.toml"
DEFAULT_TYPE = "other"


@dataclass
class HeadingSettings:
    use_bookmarks: bool = True
    use_numbering: bool = True
    use_style: bool = True
    max_level: int = 6
    numbering_keywords: dict[str, list[str]] = field(default_factory=dict)
    numbering_schemes: list[str] | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "HeadingSettings":
        return cls(
            use_bookmarks=bool(d.get("use_bookmarks", True)),
            use_numbering=bool(d.get("use_numbering", True)),
            use_style=bool(d.get("use_style", True)),
            max_level=int(d.get("max_level", 6)),
            numbering_keywords={k: list(v) for k, v in d.get("numbering_keywords", {}).items()},
            numbering_schemes=list(d["numbering_schemes"]) if d.get("numbering_schemes") else None,
        )

    def to_dict(self) -> dict:
        d = {"use_bookmarks": self.use_bookmarks, "use_numbering": self.use_numbering,
             "use_style": self.use_style, "max_level": self.max_level}
        if self.numbering_schemes:
            d["numbering_schemes"] = self.numbering_schemes
        if self.numbering_keywords:
            d["numbering_keywords"] = self.numbering_keywords
        return d


@dataclass
class DoclingSettings:
    ocr: bool = False
    force_full_page_ocr: bool = False
    ocr_lang: list[str] = field(default_factory=list)
    table_mode: str = "accurate"  # accurate | fast
    images_scale: float = 2.0
    picture_classification: bool = False
    chart_data: bool = False
    formulas: bool = False
    headings: HeadingSettings = field(default_factory=HeadingSettings)

    @classmethod
    def from_dict(cls, d: dict) -> "DoclingSettings":
        mode = d.get("table_mode", "accurate")
        if mode not in ("accurate", "fast"):
            raise ValueError(f"table_mode must be 'accurate' or 'fast', got {mode!r}")
        return cls(
            ocr=bool(d.get("ocr", False)),
            force_full_page_ocr=bool(d.get("force_full_page_ocr", False)),
            ocr_lang=list(d.get("ocr_lang", [])),
            table_mode=mode,
            images_scale=float(d.get("images_scale", 2.0)),
            picture_classification=bool(d.get("picture_classification", False)),
            chart_data=bool(d.get("chart_data", False)),
            formulas=bool(d.get("formulas", False)),
            headings=HeadingSettings.from_dict(d.get("headings", {})),
        )

    def to_dict(self) -> dict:
        d = {"ocr": self.ocr, "table_mode": self.table_mode, "images_scale": self.images_scale,
             "picture_classification": self.picture_classification,
             "chart_data": self.chart_data, "formulas": self.formulas}
        if self.force_full_page_ocr:
            d["force_full_page_ocr"] = True
        if self.ocr_lang:
            d["ocr_lang"] = self.ocr_lang
        d["headings"] = self.headings.to_dict()
        return d

    def key(self) -> str:
        """Stable key to reuse an engine already loaded with the same settings."""
        return tomli_w.dumps(self.to_dict())


@dataclass
class DocType:
    id: str
    label: str
    description: str = ""
    category: str = "other"
    licence_status: str = "unknown"
    distribution: str = "derived"
    subjects: list[str] = field(default_factory=list)
    docling: DoclingSettings = field(default_factory=DoclingSettings)

    @classmethod
    def from_dict(cls, tid: str, d: dict) -> "DocType":
        return cls(
            id=tid,
            label=d.get("label", tid),
            description=d.get("description", ""),
            category=d.get("category", "other"),
            licence_status=d.get("licence_status", "unknown"),
            distribution=d.get("distribution", "derived"),
            subjects=list(d.get("subjects", [])),
            docling=DoclingSettings.from_dict(d.get("docling", {})),
        )

    def to_dict(self) -> dict:
        return {"label": self.label, "description": self.description, "category": self.category,
                "licence_status": self.licence_status, "distribution": self.distribution,
                "subjects": self.subjects, "docling": self.docling.to_dict()}


@dataclass
class Classification:
    category: str = ""
    author: str = ""
    distributor: str = ""
    type: str = ""
    subjects: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Classification":
        d = d or {}
        return cls(category=d.get("category", ""), author=d.get("author", ""),
                   distributor=d.get("distributor", ""), type=d.get("type", ""),
                   subjects=list(d.get("subjects", [])))

    def to_dict(self) -> dict:
        return {"category": self.category, "author": self.author, "distributor": self.distributor,
                "type": self.type, "subjects": self.subjects}

    def merged_over(self, base: "Classification") -> "Classification":
        """Non-empty values of ``self`` on top of ``base``."""
        return Classification(
            category=self.category or base.category,
            author=self.author or base.author,
            distributor=self.distributor or base.distributor,
            type=self.type or base.type,
            subjects=self.subjects or base.subjects,
        )


@dataclass
class Registry:
    categories: dict[str, str]
    subjects: list[str]
    types: dict[str, DocType]
    path: Path | None = None

    def get(self, tid: str | None) -> DocType:
        if tid and tid in self.types:
            return self.types[tid]
        return self.types.get(DEFAULT_TYPE) or DocType(DEFAULT_TYPE, "Other document")

    def add_type(self, t: DocType) -> None:
        if not t.id or not t.id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("A type id may only contain letters, digits, - and _.")
        self.types[t.id] = t

    def remove_type(self, tid: str) -> None:
        if tid == DEFAULT_TYPE:
            raise ValueError(f"The '{DEFAULT_TYPE}' type cannot be removed.")
        self.types.pop(tid, None)

    def to_dict(self) -> dict:
        return {"schema": "1.0", "categories": self.categories,
                "subjects": {"list": self.subjects},
                "types": {tid: t.to_dict() for tid, t in self.types.items()}}

    def save(self, path: Path | None = None) -> Path:
        path = path or self.path
        if path is None:
            raise ValueError("No path to save the types to.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomli_w.dumps(self.to_dict()), encoding="utf-8")
        self.path = path
        return path


def _parse(raw: dict, path: Path | None) -> Registry:
    types = {tid: DocType.from_dict(tid, d) for tid, d in raw.get("types", {}).items()}
    if DEFAULT_TYPE not in types:
        types[DEFAULT_TYPE] = DocType(DEFAULT_TYPE, "Other document")
    return Registry(
        categories=dict(raw.get("categories", {})),
        subjects=list(raw.get("subjects", {}).get("list", [])),
        types=types,
        path=path,
    )


def default_registry() -> Registry:
    text = resources.files("quarrix").joinpath("default_types.toml").read_text(encoding="utf-8")
    return _parse(tomllib.loads(text), None)


def load_registry(output_dir: Path | None, create: bool = True) -> Registry:
    """Load ``<output_dir>/types.toml``; create it from the defaults if missing.

    Without an output folder (not chosen yet), returns the defaults.
    """
    if output_dir is None:
        return default_registry()
    path = output_dir / TYPES_FILE
    if path.is_file():
        return _parse(tomllib.loads(path.read_text(encoding="utf-8")), path)
    reg = default_registry()
    reg.path = path
    if create:
        text = resources.files("quarrix").joinpath("default_types.toml").read_text(encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")  # keeps the explanatory comments
    return reg


def default_classification(reg: Registry, tid: str | None) -> Classification:
    t = reg.get(tid)
    return Classification(category=t.category, type=t.id, subjects=list(t.subjects))
