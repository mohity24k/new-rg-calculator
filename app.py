"""Green Metrics Studio: Streamlit entry point.

Run with:  streamlit run app.py
The logic is in the `gms` package; the pages in `ui/pages` only display it.
"""

import streamlit as st

import gms
from gms.extraction import service
from ui import components as ui
from ui import state

st.set_page_config(page_title="Green Metrics Studio", page_icon=":material/eco:", layout="wide",
                   initial_sidebar_state="auto")
state.init()
ui.css()

pages = {
    "Analyse a reaction": [
        st.Page("ui/pages/reaction.py", title="1. Reaction", icon=":material/science:", default=True),
        st.Page("ui/pages/scorecard.py", title="2. Score card", icon=":material/fact_check:"),
        st.Page("ui/pages/diagnosis.py", title="3. Diagnosis", icon=":material/troubleshoot:"),
        st.Page("ui/pages/alternatives.py", title="4. Greener alternatives", icon=":material/swap_horiz:"),
        st.Page("ui/pages/compare.py", title="5. Compare and export", icon=":material/compare_arrows:"),
    ],
    "Reference": [
        st.Page("ui/pages/hazard_ratios.py", title="Life-cycle hazard ratios", icon=":material/public:"),
        st.Page("ui/pages/about.py", title="Methods and limitations", icon=":material/info:"),
        st.Page("ui/pages/admin.py", title="Feedback data", icon=":material/lock:"),
    ],
}
page = st.navigation(pages)

with st.sidebar:
    st.markdown("### Green Metrics Studio")
    a = st.session_state.get("analysis")
    if a:
        g = a["score"].greenness
        st.markdown(f"**Current reaction**  \n{ui.esc(a['reaction'].name or 'unnamed')}")
        st.markdown(ui.badge(g["label"], ui.VERDICT_COLOUR[g["verdict"]]), unsafe_allow_html=True)
    else:
        st.caption("No reaction analysed yet.")
    ex = service.status(state.app_settings())
    st.caption(("PDF extraction: available" if ex.online else
                "PDF extraction: offline" if ex.configured else "PDF extraction: not set up")
               + f"  \nVersion {gms.__version__}")

page.run()
