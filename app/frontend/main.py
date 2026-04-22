import requests
import streamlit as st
import os

# 1. Setup
st.set_page_config(page_title="Sagan Dashboard", layout="wide")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

# CSS Fixes
st.markdown("""
    <style>
        .stCodeBlock { font-size: 0.75rem !important; height: 300px !important; }
        .footer-container { border-top: 1px solid #444; padding-top: 20px; }
    </style>
""", unsafe_allow_html=True)

st.title("🚀 Sagan")
st.caption("A utility for serving data science applications.")

# 2. Main Dashboard (Top)
t1, t2 = st.tabs(["💬 Inference", "🛠️ Training Control"])

with t1:
    prompt = st.text_area("Ask Shakespeare...", placeholder="To be or not to be?", height=150)
    if st.button("Generate", type="primary"):
        with st.spinner("Thinking..."):
            try:
                res = requests.post(f"{BACKEND_URL}/prompt", json={"content": prompt}, timeout=120)
                st.write(res.json().get("response"))
            except Exception as e:
                st.error(f"Inference failed: {e}")

with t2:
    # Use a subheader or bold markdown instead of st.info
    st.subheader("⚙️ Google Kubernetes Engine (GKE) Cluster Control") 
    st.caption("Train the Shakespeare GPT chatbot and update the model...")

    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🔥 Start Training", use_container_width=True, type="primary"):
            requests.post(f"{BACKEND_URL}/train")
            
    with col2:
        if st.button("🛑 Stop Training", use_container_width=True, type="secondary"):
            requests.delete(f"{BACKEND_URL}/stop_train")
            
    with col3:
        if st.button("🔄 Sync Weights", use_container_width=True):
            res = requests.post(f"{BACKEND_URL}/reload_model")
            st.toast(res.json().get("status", "Syncing..."))

# --- BOTTOM SECTION: TRAINING MONITOR ---
st.markdown('<div class="footer-container"></div>', unsafe_allow_html=True)
st.subheader("📝 Training Monitor")

# Initialize session state for logs
if "local_logs" not in st.session_state:
    st.session_state.local_logs = ""

stream_enabled = st.toggle("Live Stream", value=True)

@st.fragment(run_every="5s")
def sync_footer_fragment(enabled):
    with st.container():
        try:
            # 1. Always attempt to fetch status
            res = requests.get(f"{BACKEND_URL}/job_status", timeout=1.5)
            job = res.json() if res.status_code == 200 else {}
            st.markdown(f"**Job:** `{job.get('name', 'N/A')}` | **Status:** :{job.get('color', 'grey')}[{job.get('status', 'Unknown')}]")
            
            # 2. Fetch Logs automatically
            if enabled:
                log_res = requests.get(f"{BACKEND_URL}/get_log", timeout=2.0)
                if log_res.status_code == 200:
                    logs = log_res.json()
                    st.session_state.local_logs = "\n".join([f"=== {k} ===\n{v}" for k, v in logs.items()])

            # 3. Display Logs
            if st.session_state.local_logs:
                st.code(st.session_state.local_logs[-4000:], language="text")
            else:
                st.info("Waiting for training data...")

        except Exception:
            st.caption("Searching for backend...")

# Execute the monitor
sync_footer_fragment(stream_enabled)

