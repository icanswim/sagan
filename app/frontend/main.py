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

t1, t2, t3 = st.tabs(["💬 inference", "🛠️ training control", "📜 history"])

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
    st.subheader("⚙️ google kubernetes engine training control")
    st.caption("Adjust hyperparameters for the Shakespeare GPT model.")

    with st.form("training_params"):
        batch_size = st.number_input(
            "batch size", value=64, min_value=8, max_value=168, step=8, 
            help="8 <= bs <= 168")
        epoch = st.number_input(
            "epochs", value=1, min_value=1, max_value=10, step=1, 
            help="1 <= epochs <= 10")
        n_samples = st.number_input(
            "samples (n)", value=2000, min_value=1000, max_value=300000, step=1000, 
            help="100 <= n <= 300k")

        submitted = st.form_submit_button("🔥 start training", type="primary", use_container_width=True)
        
        if submitted:
            payload = {
                "batch_size": batch_size, 
                "epoch": epoch, 
                "n": n_samples
            }
            try:
                res = requests.post(f"{BACKEND_URL}/train", json=payload, timeout=10)
                if res.status_code == 200:
                    st.success("Training job submitted!")
                else:
                    try:
                        error_detail = res.json().get("detail", "Unknown error")
                        st.error(f"❌ {error_detail}")
                    except:
                        st.error(f"Backend error: {res.status_code}")
            except Exception as e:
                st.error(f"Failed to connect to backend: {e}")

    sc1, sc2 = st.columns(2)
    with sc1:
        if st.button("🛑 stop training", use_container_width=True, type="secondary"):
            try:
                requests.delete(f"{BACKEND_URL}/stop_train")
                st.info("stop signal sent...")
            except:
                st.error("could not reach backend...")
    with sc2:
        if st.button("🔄 sync weights", use_container_width=True):
            try:
                res = requests.post(f"{BACKEND_URL}/reload_model")
                st.toast(res.json().get("status", "syncing..."))
            except:
                st.error("sync failed.")

with t3:
    st.subheader("📜 past training runs")

    @st.fragment(run_every="10s")
    def refresh_history():
        if st.button("🗑️ clear all history", type="secondary", use_container_width=True):
            try:
                res = requests.delete(f"{BACKEND_URL}/history/clear", timeout=5)
                if res.status_code == 200:
                    st.toast("History wiped!")
            except Exception as e:
                st.error(f"request failed: {e}")
        try:
            res = requests.get(f"{BACKEND_URL}/history", timeout=2)
            if res.status_code == 200:
                data = res.json() 
                if data:
                    st.dataframe(
                        data, 
                        use_container_width=True, 
                        hide_index=True,
                        column_order=("job_name", "batch_size", "epoch", "n", "status", "test_loss", "created_at", "training_time"),
                        column_config={
                            "job_name": st.column_config.TextColumn("job name"),
                            "test_loss": st.column_config.NumberColumn("loss 📉", format="%.4f"),
                            "training_time": st.column_config.TextColumn("training time"),
                        }
                    )
                else:
                    st.info("no training history found.")
            else:
                st.error(f"backend error: {res.status_code}")
        except Exception as e:
            st.error(f"could not load history: {e}")

    refresh_history()
# training monitor
st.markdown('<div class="footer-container"></div>', unsafe_allow_html=True)
st.subheader("📝 training monitor")

stream_enabled = st.toggle("live stream", value=True)

@st.fragment(run_every="5s")
def sync_footer_fragment(enabled):
    # use a local dict for the current render to avoid double-printing
    current_logs = {}

    with st.container():
        try:
            # fetch job status
            res = requests.get(f"{BACKEND_URL}/job_status", timeout=1.5)
            if res.status_code == 200:
                job = res.json()
                st.markdown(f"**Job:** `{job.get('name', 'N/A')}` | **Status:** :{job.get('color', 'grey')}[{job.get('status', 'Unknown')}]")
            
            # fetch logs, set local variable
            if enabled:
                log_res = requests.get(f"{BACKEND_URL}/get_log", timeout=2.0)
                if log_res.status_code == 200:
                    current_logs = log_res.json()
                    # sync to session state for persistence across tab changes
                    st.session_state.local_logs = current_logs

            # render only from the local variable
            display_logs = current_logs if current_logs else st.session_state.get("local_logs", {})
            
            if display_logs:
                # sort keys so windows stay in the same order
                for filename in sorted(display_logs.keys()):
                    content = display_logs[filename]
                    
                    if "train" in filename:
                        st.caption(f"🔥 training cluster live: {filename}")
                        st.code(content, language="text")
                    
                    elif "main" in filename:
                        st.caption(f"🖥️ backend activity: {filename}")
                        st.code(content, language="text")
            else:
                st.info("waiting for logs from backend...")

        except Exception as e:
            st.error(f"fragment error: {str(e)}")

sync_footer_fragment(stream_enabled)

