"""Quarrix configuration.

Extractions go to ``output_dir``, the output folder chosen by the user (in the
window, with ``--output-dir`` or in the configuration file). There is no default
folder: until one is chosen, the window asks for it on the first drop. The pack
built by ``quarrix dist`` can then be copied to ``publish_dir`` (another folder,
for example a public Git repository).

Configuration file lookup order:
1. path given explicitly (``--config``);
2. environment variable ``QUARRIX_CONFIG``;
3. ``./quarrix.toml``;
4. ``~/.config/quarrix/config.toml`` (``%APPDATA%\\quarrix\\config.toml`` on Windows).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ENGINES = ("auto", "docling", "pymupdf")


def user_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "quarrix" / "config.toml"


class NoOutputDir(ValueError):
    """No output folder has been chosen."""


@dataclass
class LicenceDefaults:
    """Licence values applied to a new document, to be corrected afterwards."""

    status: str = "unknown"  # unknown | public-domain | open | permission | proprietary
    distribution: str = "derived"  # none | derived | full
    holder: str = ""


@dataclass
class Config:
    output_dir: Path | None = None  # output folder chosen by the user
    publish_dir: Path | None = None  # copy of the compiled pack only (never the sources)
    engine: str = "auto"
    image_scale: float = 2.0  # figure rendering scale (2.0 ≈ 144 dpi)
    ocr: bool = False  # OCR for scanned PDFs (Docling only)
    chart_data: bool = False  # charts -> CSV with a vision model (Docling, heavy)
    licence: LicenceDefaults = field(default_factory=LicenceDefaults)
    source: Path | None = None  # file the configuration was read from

    @property
    def output(self) -> Path:
        """Output folder; clear error if it has not been chosen yet."""
        if self.output_dir is None:
            raise NoOutputDir(
                "No output folder chosen: choose one in the window, with "
                "'quarrix --output-dir FOLDER …' or 'quarrix init FOLDER'.")
        return self.output_dir

    @property
    def sources_dir(self) -> Path:
        return self.output / "sources"

    @property
    def dist_dir(self) -> Path:
        return self.output / "dist"


def _candidates(explicit: str | os.PathLike | None) -> list[Path]:
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit))
    if env := os.environ.get("QUARRIX_CONFIG"):
        paths.append(Path(env))
    paths.append(Path.cwd() / "quarrix.toml")
    paths.append(user_config_path())
    return paths


def load_config(explicit: str | os.PathLike | None = None) -> Config:
    for path in _candidates(explicit):
        if path.is_file():
            return _from_file(path)
    if explicit:
        raise FileNotFoundError(f"Configuration file not found: {explicit}")
    return Config()


def _from_file(path: Path) -> Config:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    cfg = Config(source=path)
    if "output_dir" in raw:
        root = Path(os.path.expandvars(raw["output_dir"])).expanduser()
        if not root.is_absolute():
            root = (path.parent / root).resolve()
        cfg.output_dir = root
    if raw.get("publish_dir"):
        pub = Path(os.path.expandvars(raw["publish_dir"])).expanduser()
        cfg.publish_dir = pub if pub.is_absolute() else (path.parent / pub).resolve()
    engine = raw.get("engine", cfg.engine)
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, got {engine!r}")
    cfg.engine = engine
    cfg.image_scale = float(raw.get("image_scale", cfg.image_scale))
    cfg.ocr = bool(raw.get("ocr", cfg.ocr))
    cfg.chart_data = bool(raw.get("chart_data", cfg.chart_data))
    lic = raw.get("licence", {})
    cfg.licence = LicenceDefaults(
        status=lic.get("status", cfg.licence.status),
        distribution=lic.get("distribution", cfg.licence.distribution),
        holder=lic.get("holder", cfg.licence.holder),
    )
    return cfg


def write_user_config(output_dir: Path) -> Path:
    """Create (or replace) the user configuration with a new ``output_dir``."""
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.is_file():
        existing = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("output_dir")
        )
    root = str(output_dir).replace("\\", "/")
    path.write_text(f'output_dir = "{root}"\n{existing}\n'.rstrip() + "\n", encoding="utf-8")
    return path
