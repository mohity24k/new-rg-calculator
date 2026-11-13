"""Download of the stored feedback, protected by a token that is set on the server (GMS_ADMIN_TOKEN)."""

import hmac
import time

import streamlit as st

from gms import feedback
from ui import state

state.init()
st.title("Feedback data")
cfg = state.app_settings()
if not cfg.admin_token:
    st.info("The feedback download is switched off on this server. The administrator can switch it on by "
            "setting GMS_ADMIN_TOKEN. The data can also be exported on the server: "
            "`python scripts/export_feedback.py` (in the container: `docker exec <name> python scripts/export_feedback.py "
            "--out /data/feedback.csv`).")
    st.stop()
MAX_ATTEMPTS = 5
st.session_state.setdefault("admin_failures", 0)
if st.session_state["admin_failures"] >= MAX_ATTEMPTS:
    st.error("Too many wrong tokens in this session. Reload the page to try again.")
    st.stop()
token = st.text_input("Administrator token", type="password")
if token and hmac.compare_digest(token.encode(), cfg.admin_token.encode()):
    path = cfg.data_dir / "feedback.db"
    rows = feedback.rows(path)
    st.write(f"{len(rows)} feedback record(s).")
    st.download_button("Download CSV", feedback.to_csv(path), "feedback.csv", "text/csv")
elif token:
    st.session_state["admin_failures"] += 1
    time.sleep(1.0)                           # slows down guessing
    st.error("The token is not correct.")
