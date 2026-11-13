"""Shared display helpers. All text that comes from the user or from a paper is escaped before
it is placed in HTML."""

from __future__ import annotations

import html
import math
import re

import streamlit as st

from gms.metrics import registry

RATING = {  # word, symbol, colour: the word and symbol carry the meaning, not the colour alone
    "good": ("Good", "✓", "#1f7a4d"),
    "moderate": ("Moderate", "~", "#a86400"),
    "poor": ("Poor", "✕", "#b3261e"),
    "n/a": ("Not available", "–", "#6b7280"),
}
VERDICT_COLOUR = {"green": "#1f7a4d", "borderline": "#a86400", "not_green": "#b3261e", "unknown": "#6b7280"}
CONFIDENCE_COLOUR = {"high": "#1f7a4d", "moderate": "#a86400", "low": "#6b7280"}

CSS = """
<style>
.gms-card {border:1px solid #d9e0dc; border-left-width:6px; border-radius:8px; padding:10px 12px;
           margin-bottom:10px; background:#ffffff;}
.gms-card .l {font-size:0.80rem; color:#4b5563;}
.gms-card .v {font-size:1.35rem; font-weight:700; line-height:1.3;}
.gms-card .u {font-size:0.85rem; font-weight:400; color:#4b5563;}
.gms-card .r {font-size:0.80rem; font-weight:600;}
.gms-badge {display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.78rem; font-weight:600;
            color:#ffffff; margin-right:6px; white-space:nowrap;}
.gms-est {background:#5b4bc4;}
.gms-verdict {border-radius:10px; padding:14px 18px; color:#ffffff; margin-bottom:8px;}
.gms-verdict .t {font-size:1.6rem; font-weight:700;}
.gms-verdict .s {font-size:0.95rem;}
</style>
"""


def css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x), quote=True)


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|<>~$])")


def md(x) -> str:
    """Text for st.markdown / st.caption / st.warning: Markdown characters are escaped, so a
    name from a paper or from the user cannot become a link, an image or formatting."""
    return _MD_SPECIAL.sub(r"\\\1", str(x))


def fmt(value, digits: int = 3) -> str:
    """Number for display: handles None, text, infinity and very small or large values."""
    if value is None:
        return "n/a"
    if isinstance(value, str):
        return value
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(v):
        return "n/a"
    if math.isinf(v):
        return "∞" if v > 0 else "-∞"
    if v == 0:
        return "0"
    if abs(v) >= 1e5 or abs(v) < 1e-3:
        return f"{v:.{digits}g}"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def signed(value) -> str:
    if value is None:
        return "n/a"
    if abs(value) < 5e-10:
        return "0"
    return ("+" if value > 0 else "−") + fmt(abs(value))


def badge(text: str, colour: str) -> str:
    return f'<span class="gms-badge" style="background:{colour}">{esc(text)}</span>'


def est_badge() -> str:
    return '<span class="gms-badge gms-est" title="This value depends on an estimated amount">estimate</span>'


def metric_card(key: str, value, estimated: bool = False) -> None:
    m = registry.get(key)
    word, sym, colour = RATING[registry.rating(key, value)]
    st.markdown(
        f'<div class="gms-card" style="border-left-color:{colour}">'
        f'<div class="l">{esc(m.label)} ({esc(m.short)}) {est_badge() if estimated else ""}</div>'
        f'<div class="v">{esc(fmt(value))} <span class="u">{esc(m.unit)}</span></div>'
        f'<div class="r" style="color:{colour}">{sym} {word}</div></div>', unsafe_allow_html=True)


def verdict_banner(g: dict) -> None:
    colour = VERDICT_COLOUR[g["verdict"]]
    score = f"Combined score {g['score']:.2f} of 1.00" if g["score"] is not None else "Not enough data for a combined score"
    st.markdown(f'<div class="gms-verdict" style="background:{colour}"><div class="t">{esc(g["label"])}</div>'
                f'<div class="s">{esc(score)}</div></div>', unsafe_allow_html=True)


def disclaimer() -> None:
    st.caption("Decision support for trained chemists. It does not replace a risk assessment, the safety data "
               "sheet or a laboratory test. Suggested changes are untested for your substrates.")
