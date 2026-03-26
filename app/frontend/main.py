import requests
import streamlit as st
import os

# 1. Setup Page & CSS for better "Log Reshaping"
st.set_page_config(page_title="Sagan Dashboard", layout="wide")

st.markdown("""
    <style>
        section[data-testid="stSidebar"] { width: 700px !important; }
        .stCodeBlock { font-size: 0.75rem !important; }
    </style>
""", unsafe_allow_html=True)

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

st.title("🚀 Sagan Dashboard")
st.caption(f"Connected to backend at: {BACKEND_URL}")

@st.fragment(run_every="5s")
def sync_logs_fragment():
    st.subheader("📝 Training Monitor")
    stream_enabled = st.toggle("Live Stream", value=True)
    log_area = st.empty()
    
    if stream_enabled:
        try:
            res = requests.get(f"{BACKEND_URL}/get_log", timeout=3.0)
            if res.status_code == 200:
                data = res.json()
                if not data:
                    log_area.info("Logs are currently empty.")
                else:
                    combined_text = ""
                    for filename, content in data.items():
                        combined_text += f"=== {filename} ===\n{content}\n\n"
                    # Show the last 10,000 characters
                    log_area.code(combined_text[-10000:], language="text")
            else:
                log_area.warning(f"Backend Status: {res.status_code}")
        except Exception:
            log_area.warning("Connecting to backend...")

with st.sidebar:
    sync_logs_fragment()

# 3. Main UI Tabs
t1, t2 = st.tabs(["💬 Inference", "🛠️ Training Control"])

with t1:
    prompt_input = st.text_area("Hey Shakespeare...", placeholder="To be or not to be...", height=150)
    if st.button("Generate Text", type="primary"):
        if not prompt_input:
            st.warning("Please enter a prompt first.")
        else:
            with st.spinner("Generating..."):
                try:
                    res = requests.post(f"{BACKEND_URL}/prompt", json={"content": prompt_input}, timeout=15)
                    if res.status_code == 200:
                        st.success("Result:")
                        # FIX: Matches your backend's 'response' key
                        st.write(res.json().get("response", "No response field in JSON."))
                    else:
                        st.error(f"Error: {res.text}")
                except Exception as e:
                    st.error(f"Failed to reach backend: {e}")

with t2:
    st.info("Triggering training launches a GKE Job.")
    if st.button("🔥 Start Training", type="primary"):
        try:
            res = requests.post(f"{BACKEND_URL}/train", timeout=10)
            data = res.json()
            st.success(f"{data.get('message')} (ID: {data.get('job_id')})")
        except Exception as e:
            st.error(f"Failed to launch training: {e}")
