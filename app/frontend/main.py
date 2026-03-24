import streamlit as st
import requests
import os
import time

# Use the service name defined in your k8s manifest
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

st.set_page_config(page_title="Sagan Dashboard", layout="wide")
st.title("🚀 Sagan Frontend")
st.caption(f"Connected to backend at: {BACKEND_URL}")

# --- Sidebar Log Streamer ---
with st.sidebar:
    st.header("📝 Training Monitor")
    log_area = st.empty()
    
    # Checkbox to toggle streaming
    stream_enabled = st.checkbox("Live Stream Log", value=True)
    
    if stream_enabled:
        try:
            # Short timeout to keep the UI responsive
            res = requests.get(f"{BACKEND_URL}/get_log", timeout=1.5)
            if res.status_code == 200:
                data = res.json()
                log_text = data.get("log", "")
                if not log_text:
                    log_area.info("Log file is currently empty.")
                else:
                    # Uses code block for better readability of logs
                    log_area.code(log_text, language="text")
            elif res.status_code == 503:
                log_area.warning("Storage volume not mounted yet.")
            else:
                log_area.error(f"Backend returned status: {res.status_code}")
        except Exception:
            log_area.warning("Waiting for backend connection...")

# --- Main UI Tabs ---
t1, t2 = st.tabs(["💬 Inference", "🛠️ Training Control"])

with t1:
    prompt_input = st.text_area("Hey Shakespeare...", placeholder="Once upon a time...", height=150)
    if st.button("Generate Text", type="primary"):
        if not prompt_input:
            st.warning("Please enter a prompt first.")
        else:
            with st.spinner("Generating..."):
                try:
                    res = requests.post(f"{BACKEND_URL}/prompt", json={"content": prompt_input}, timeout=10)
                    if res.status_code == 200:
                        st.success("Result:")
                        st.write(res.json().get("output", "No output received."))
                    else:
                        st.error(f"Error: {res.text}")
                except Exception as e:
                    st.error(f"Failed to reach backend: {e}")

with t2:
    st.info("Triggering training launches a dedicated Kubernetes Job in the 'sagan-app' namespace.")
    if st.button("🔥 Start Retraining"):
        try:
            res = requests.post(f"{BACKEND_URL}/train", timeout=5)
            msg = res.json().get("message", "Job triggered.")
            job_id = res.json().get("job_id", "unknown")
            st.success(f"{msg} (ID: {job_id})")
        except Exception as e:
            st.error(f"Failed to launch training: {e}")

# Automated refresh for logs
if stream_enabled:
    time.sleep(2)
    st.rerun()
