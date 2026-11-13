"""Download of the stored feedback, protected by a token that is set on the server (GMS_ADMIN_TOKEN)."""

import hmac

import streamlit as st

from gms import feedback
from ui import state

state.init()
st.title("Feedback data")
cfg = state.app_settings()
if not cfg.admin_token:
    st.info("The feedback download is switched off on this server. The administrator can switch it on by "
            "setting GMS_ADMIN_TOKEN. The data can also be exported on the server with scripts/export_feedback.py.")
    st.stop()
token = st.text_input("Administrator token", type="password")
if token and hmac.compare_digest(token.encode(), cfg.admin_token.encode()):
    path = cfg.data_dir / "feedback.db"
    rows = feedback.rows(path)
    st.write(f"{len(rows)} feedback record(s).")
    st.download_button("Download CSV", feedback.to_csv(path), "feedback.csv", "text/csv")
elif token:
    st.error("The token is not correct.")
