"""Server-side settings. They come from environment variables (or Streamlit secrets, which
Streamlit exposes to the app only on the server). Nothing here is ever shown to, or editable
by, a visitor of the app."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _get(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v is None or v == "":
        try:                                   # .streamlit/secrets.toml, if the app runs under Streamlit
            import streamlit as st
            v = st.secrets.get(key, None)
        except Exception:
            v = None
    return str(v) if v not in (None, "") else default


def _flag(key: str, default: bool = False) -> bool:
    v = _get(key, "")
    return default if v == "" else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    vlm_base_url: str          # OpenAI-compatible endpoint of the extraction model; "" = extraction not configured
    vlm_model: str
    vlm_api_key: str
    data_dir: Path             # feedback database and lookup caches
    offline: bool              # True: never call PubChem (bundled data and caches only)
    max_upload_mb: int
    max_pdf_pages: int
    admin_token: str           # "" = the feedback download page is disabled


def load() -> Settings:
    return Settings(
        vlm_base_url=_get("GMS_VLM_BASE_URL").strip(),
        vlm_model=_get("GMS_VLM_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct").strip(),
        vlm_api_key=_get("GMS_VLM_API_KEY", "EMPTY"),
        data_dir=Path(_get("GMS_DATA_DIR", "data/runtime")),
        offline=_flag("GMS_OFFLINE"),
        max_upload_mb=int(_get("GMS_MAX_UPLOAD_MB", "25")),
        max_pdf_pages=int(_get("GMS_MAX_PDF_PAGES", "80")),
        admin_token=_get("GMS_ADMIN_TOKEN"),
    )
