"""
visualization.py
-----------------
Plotly chart builders and color-coding helpers for the Streamlit UI.
"""

import plotly.graph_objects as go
import streamlit as st

GREEN = "#2ecc71"
YELLOW = "#f1c40f"
RED = "#e74c3c"


def metric_color(value: float, good_threshold: float, moderate_threshold: float,
                  higher_is_better: bool = True) -> str:
    """
    Return a hex color: green = ideal, yellow = moderate, red = unfavorable.

    If higher_is_better=True: value >= good_threshold -> green,
        >= moderate_threshold -> yellow, else red.
    If higher_is_better=False (e.g., E-factor, PMI): value <= good_threshold -> green,
        <= moderate_threshold -> yellow, else red.
    """
    if value is None:
        return YELLOW
    if higher_is_better:
        if value >= good_threshold:
            return GREEN
        elif value >= moderate_threshold:
            return YELLOW
        else:
            return RED
    else:
        if value <= good_threshold:
            return GREEN
        elif value <= moderate_threshold:
            return YELLOW
        else:
            return RED


def render_colored_metric(label: str, value: float, unit: str, color: str, help_text: str = ""):
    """Render a single metric as a colored 'card' using HTML in Streamlit."""
    st.markdown(
        f"""
        <div style="border-left: 6px solid {color}; padding: 10px 14px; margin-bottom:10px;
                    background-color: rgba(120,120,120,0.08); border-radius: 6px;">
            <div style="font-size: 0.82rem; color: gray; text-transform: uppercase;
                        letter-spacing: 0.03em;">{label}</div>
            <div style="font-size: 1.5rem; font-weight: 700;">{value:,.2f} <span
                        style="font-size:0.9rem; font-weight:400;">{unit}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if help_text:
        st.caption(help_text)


def radar_chart(route_a_name: str, route_a_values: dict,
                 route_b_name: str, route_b_values: dict,
                 title: str = "Reaction Route Comparison") -> go.Figure:
    """
    Build a Plotly radar/spider chart comparing two reaction routes across a
    shared set of (0-100 normalized) metrics, e.g. AE, RME, CE, Eco-Scale, 1/PMI.
    """
    categories = list(route_a_values.keys())
    categories_closed = categories + [categories[0]]

    values_a = [route_a_values.get(c, 0) for c in categories]
    values_a += [values_a[0]]
    values_b = [route_b_values.get(c, 0) for c in categories]
    values_b += [values_b[0]]

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values_a, theta=categories_closed, fill="toself", name=route_a_name,
        line_color="#2E86AB", opacity=0.75,
    ))
    fig.add_trace(go.Scatterpolar(
        r=values_b, theta=categories_closed, fill="toself", name=route_b_name,
        line_color="#E67E22", opacity=0.75,
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=True,
        title=title,
        height=550,
        margin=dict(t=60, b=40, l=40, r=40),
    )
    return fig


def eco_scale_gauge(score: float, title: str = "Eco-Scale Score") -> go.Figure:
    """Build a Plotly gauge indicator for a single Eco-Scale score (0-100)."""
    color = metric_color(score, good_threshold=75, moderate_threshold=50, higher_is_better=True)
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        title={"text": title},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 50], "color": "#fdecea"},
                {"range": [50, 75], "color": "#fef9e7"},
                {"range": [75, 100], "color": "#eafaf1"},
            ],
        },
    ))
    fig.update_layout(height=320, margin=dict(t=50, b=10, l=30, r=30))
    return fig


def penalty_breakdown_bar(penalty_dict: dict, title: str = "Eco-Scale Penalty Breakdown") -> go.Figure:
    """Horizontal bar chart of individual Eco-Scale penalty contributions."""
    labels = [k for k in penalty_dict if k not in ("Route", "Total Penalty", "Eco-Scale Score")]
    values = [penalty_dict[k] for k in labels]
    fig = go.Figure(go.Bar(x=values, y=labels, orientation="h", marker_color="#E67E22"))
    fig.update_layout(title=title, height=350, margin=dict(t=50, b=30, l=10, r=10),
                       xaxis_title="Penalty points")
    return fig
