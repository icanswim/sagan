import requests
import streamlit as st
import os


st.set_page_config(page_title="Sagan Dashboard", layout="wide")
if "local_logs" not in st.session_state:
    st.session_state.local_logs = {}
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

st.markdown("""
    <style>
        .stCodeBlock { font-size: 0.75rem !important; height: 350px !important; }
        .footer-container { border-top: 1px solid #444; padding-top: 20px; margin-top: 20px; }
    </style>
""", unsafe_allow_html=True)

st.title("🚀 Sagan")
st.caption("A utility for serving data science applications.")

t1, t2 = st.tabs(["💬 Inference", "🛠️ Training Control"])

with t1:
    prompt = st.text_area("Ask Shakespeare...", placeholder="To be or not to be...", height=150)
    if st.button("Generate", type="primary"):
        with st.spinner("Thinking..."):
            try:
                res = requests.post(f"{BACKEND_URL}/prompt", json={"content": prompt}, timeout=120)
                st.write(res.json().get("response"))
            except Exception as e:
                st.error(f"Inference failed: {e}")

with t2:
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

# training monitor
st.markdown('<div class="footer-container"></div>', unsafe_allow_html=True)
st.subheader("📝 Training Monitor")

stream_enabled = st.toggle("Live Stream", value=True)

@st.fragment(run_every="5s")
def sync_footer_fragment(enabled):
    # Use a local dict for the current render to avoid double-printing
    current_logs = {}

    with st.container():
        try:
            # 1. Fetch Job Status
            res = requests.get(f"{BACKEND_URL}/job_status", timeout=1.5)
            if res.status_code == 200:
                job = res.json()
                st.markdown(f"**Job:** `{job.get('name', 'N/A')}` | **Status:** :{job.get('color', 'grey')}[{job.get('status', 'Unknown')}]")
            
            # 2. Fetch Logs into local variable
            if enabled:
                log_res = requests.get(f"{BACKEND_URL}/get_log", timeout=2.0)
                if log_res.status_code == 200:
                    current_logs = log_res.json()
                    # Sync to session state for persistence across tab changes
                    st.session_state.local_logs = current_logs

            # 3. Render only from the local variable
            display_logs = current_logs if current_logs else st.session_state.get("local_logs", {})
            
            if display_logs:
                # Sort keys so windows stay in the same order
                for filename in sorted(display_logs.keys()):
                    content = display_logs[filename]
                    
                    if "train" in filename:
                        st.caption(f"🔥 Training Stream GKE Live: {filename}")
                        st.code(content, language="text")
                    
                    elif "main" in filename:
                        st.caption(f"🖥️ Backend Activity: {filename}")
                        st.code(content, language="text")
            else:
                st.info("waiting for logs from backend...")

        except Exception as e:
            st.error(f"fragment error: {str(e)}")

sync_footer_fragment(stream_enabled)

