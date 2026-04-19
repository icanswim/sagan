import requests
import streamlit as st
import os

# 1. Setup Page
st.set_page_config(page_title="Sagan Dashboard", layout="wide")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

# CSS to stabilize the layout height
st.markdown("""
    <style>
        section[data-testid="stSidebar"] { width: 700px !important; }
        .stCodeBlock { font-size: 0.75rem !important; min-height: 400px; }
        .status-box { min-height: 80px; }
    </style>
""", unsafe_allow_html=True)

st.title("🚀 Sagan Dashboard")

# 2. Sidebar - Everything must stay inside this 'with' block
with st.sidebar:
    st.subheader("📝 Training Monitor")
    stream_enabled = st.toggle("Live Stream", value=True)
    
    @st.fragment(run_every="5s")
    def sync_sidebar_fragment(enabled):
        # using a container inside the fragment ensures elements are replaced, not added
        with st.container():
            try:
                res = requests.get(f"{BACKEND_URL}/job_status", timeout=1.5)
                if res.status_code == 200:
                    job = res.json()
                    st.caption(f"🟢 Connected to: {BACKEND_URL}")
                    st.markdown(f"**Job:** `{job.get('name', 'N/A')}` | **Status:** :{job.get('color', 'grey')}[{job.get('status', 'Unknown')}]")
                else:
                    st.caption(f"🔴 Backend Error: {res.status_code}")
            except Exception:
                st.caption("❌ Backend unreachable...")

            st.divider()
            
            if enabled:
                try:
                    log_res = requests.get(f"{BACKEND_URL}/get_log", timeout=2.0)
                    if log_res.status_code == 200:
                        logs = log_res.json()
                        combined = "\n".join([f"=== {k} ===\n{v}" for k, v in logs.items()])
                        st.code(combined[-8000:], language="text")
                    else:
                        st.info("Logs temporarily unavailable.")
                except Exception:
                    st.warning("Connecting to logs...")

    # EXECUTION: Call the fragment while STILL inside the sidebar context
    sync_sidebar_fragment(stream_enabled)

# 3. Main UI Tabs
t1, t2 = st.tabs(["💬 Inference", "🛠️ Training Control"])

with t1:
    prompt = st.text_area("Prompt", placeholder="To be or not to be...", height=150)
    if st.button("Generate", type="primary"):
        with st.spinner("Thinking..."):
            try:
                res = requests.post(f"{BACKEND_URL}/prompt", json={"content": prompt}, timeout=30)
                if res.status_code == 200:
                    st.write(res.json().get("response"))
                else:
                    err = res.json().get("detail", {})
                    st.error(f"Backend Error: {err.get('message', 'Unknown Error')}")
                    
                    if "traceback" in err:
                        with st.expander("🔍 View Full Stack Trace"):
                            st.code(err["traceback"], language="python")
            except Exception as e:
                st.error(f"Failed to reach backend: {e}")

with t2:
    st.info("Triggering training launches a GKE Job.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔥 Start Training", type="primary", use_container_width=True):
            requests.post(f"{BACKEND_URL}/train")
    with col2:
        if st.button("🛑 Stop Training", type="secondary", use_container_width=True):
            requests.delete(f"{BACKEND_URL}/stop_train")
    
    if st.button("🔄 Sync Backend with New Weights", use_container_width=True):
        res = requests.post(f"{BACKEND_URL}/reload_model")
        st.toast(res.json().get("status", "Syncing..."))