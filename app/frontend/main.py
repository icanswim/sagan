import streamlit as st
import requests
import os
import time

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

st.set_page_config(page_title="Sagan Dashboard", layout="wide")
st.title("🚀 Sagan Frontend")
st.caption(f"Connected to backend at: {BACKEND_URL}")

# --- Sidebar Log Streamer ---
with st.sidebar:
    st.header("📝 Training Monitor")
    log_area = st.empty()
    if st.checkbox("Live Stream Logs", value=True):
        try:
            res = requests.get(f"{BACKEND_URL}/get_logs", timeout=2)
            if res.status_code == 200:
                log_area.code(res.json().get("logs", "No logs yet."), language="text")
        except:
            log_area.caption("Connecting to backend logs...")

# --- Main UI Tabs ---
t1, t2 = st.tabs(["💬 Inference", "🛠️ Training Control"])

with t1:
    prompt = st.text_area("Hey Shakespeare...", placeholder="Once upon a time...")
    if st.button("Generate Text"):
        with st.spinner("Processing..."):
            res = requests.post(f"{BACKEND_URL}/prompt", json={"content": prompt})
            st.success("Result:")
            st.write(res.json().get("output", "No output."))

with t2:
    st.info("Triggering training launches a dedicated GKE Job.")
    if st.button("🔥 Start Retraining"):
        res = requests.post(f"{BACKEND_URL}/train")
        st.info(res.json().get("message", "Job triggered."))

# Refresh cycle for live logs
time.sleep(2)
st.rerun()
