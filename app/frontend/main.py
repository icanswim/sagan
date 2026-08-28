from ctypes.wintypes import SIZE
import os
import requests
import time

import streamlit as st

import google.auth
from google.auth.transport.requests import AuthorizedSession


BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

gcp_project_id = os.getenv("GCP_PROJECT_ID")
gcp_region = os.getenv("GCP_REGION")
gcp_zone = os.getenv("GCP_ZONE")

st.set_page_config(page_title="Sagan", layout="wide")

if "local_logs" not in st.session_state:
    st.session_state.local_logs = {}

def get_spot_telemetry(project_id: str, 
                       region: str,
                       zone: str, 
                       machine_type: str = "e2-standard-2") -> dict:
    
    telemetry_data = {       
        "preemption_rate": 0.0, 
        "list_price": 0.0,
        "uptime": 0.0,
        "obtainability": 0.0                
    }

    region_zone = f"{region}-{zone}"

    try:
        credentials, _ = google.auth.default()
        authed_session = AuthorizedSession(credentials)
    except Exception as e:
        st.error(f"failed to authenticate with Google Cloud: {e}", icon="⚠️")
        return telemetry_data  # Exit early if we can't authenticate

    try:    
        url = f"https://compute.googleapis.com/compute/beta/projects/{project_id}/regions/{region}/advice/capacityHistory"

        payload = {
            "instanceProperties": {
                "machineType": f"{machine_type}",
                "scheduling": {
                    "provisioningModel": "SPOT"
                }
            },
            "locationPolicy": {
                "location": f"zones/{region_zone}"
            },
            "types": ["PREEMPTION", "PRICE"]
        }

        res = authed_session.post(url, json=payload, timeout=15)
        
        if res.status_code == 200:
            response_json = res.json()
            
            preemption_history = response_json.get("preemptionHistory", [])
            price_history = response_json.get("priceHistory", [])
            
            if preemption_history:
                telemetry_data['preemption_rate'] = preemption_history[0].get("preemptionRate", 0.0)
                
            if price_history:
                list_price_obj = price_history[0].get("listPrice", {})
                nanos = list_price_obj.get("nanos", 0)
                telemetry_data['list_price'] = nanos / 1e9
        else:
            raise Exception(f"HTTP {res.status_code}: {res.text}")
    except Exception as e:
        st.error(f"failed to fetch spot capacity metrics: {e}", icon="⚠️")

    try:
        url = f"https://compute.googleapis.com/compute/beta/projects/{project_id}/regions/{region}/advice/capacity"

        payload = {
            "instanceProperties": {
                "scheduling": {
                    "provisioningModel": "SPOT"
                }
            },
            "instanceFlexibilityPolicy": {
                "instanceSelections": {
                    "MACHINE_SELECTION_1": {
                        "machineTypes": [f"{machine_type}"]
                    }
                }
            },
            "distributionPolicy": {
                "targetShape": "ANY_SINGLE_ZONE",
                "zones": [
                    {
                        "zone": f"zones/{region_zone}"
                    }
                ]
            },
            "size": 1
        }

        res = authed_session.post(url, json=payload, timeout=15)

        if res.status_code == 200:
            response_json = res.json()

            recommendation = response_json.get("recommendations", [])
            
            if recommendation:
                scores = recommendation[0].get("scores", {})
                
                raw_uptime = scores.get("estimatedUptime", "0s")
                try:
                    uptime_seconds = float(raw_uptime.replace("s", "")) if isinstance(raw_uptime, str) else float(raw_uptime)
                except ValueError:
                    uptime_seconds = 0.0
                    
                telemetry_data['uptime'] = uptime_seconds
                telemetry_data['obtainability'] = scores.get("obtainability", 0.0)
        else:
            raise Exception(f"HTTP {res.status_code}: {res.text}")
    except Exception as e:
        st.error(f"failed to fetch spot capacity recommendations: {e}", icon="⚠️")
            
    return telemetry_data
       
st.title("🚀 Sagan")
st.caption("a utility for serving data science applications")

t1, t2, t3 = st.tabs(["💬 inference", "🛠️ training control", "📜 history"])

with t1:
    st.subheader("💬 Shakespeare GPT Inference")
    prompt = st.text_area("Ask Shakespeare...", placeholder="To be or not to be...", height=150)
    if st.button("generate", type="primary"):
        with st.spinner("thinking..."):
            try:
                res = requests.post(f"{BACKEND_URL}/prompt", json={"content": prompt}, timeout=120)
                if res.status_code == 200:
                    st.write(res.json().get("response"))
                else:
                    if "application/json" in res.headers.get("content-type", ""):
                        error_msg = res.json().get("detail", f"backend error: {res.status_code}")
                    else:
                        error_msg = f"backend error: {res.status_code}"
                    st.error(error_msg, icon="❌")
            except Exception as e:
                st.error(f"failed to connect to backend: {e}", icon="🔌")
with t2:
    st.subheader("⚙️ Google Kubernetes Engine Training Control")
    st.caption("adjust the parameters and train the model...")

    if "last_click" not in st.session_state:
        st.session_state.last_click = 0.0

    with st.form("get_spot_telemetry"):
        st.markdown(f"☁️ Spot Compute Instance Availability")
        run_check = st.form_submit_button("🔍 Live Check", use_container_width=True)

    if run_check:
        elapsed = time.time() - st.session_state.last_click
        
        if elapsed < 10:
            st.warning(f"⚠️ 10 second cooldown...")
            data = {}
        else:
            st.session_state.last_click = time.time()
            with st.spinner("checking..."):
                data = get_spot_telemetry(
                    project_id=gcp_project_id, 
                    region=gcp_region, 
                    zone=gcp_zone,
                    machine_type="e2-standard-2", 
                )
            
        if data:
            preempt_rate = data.get("preemption_rate", 0.0)
            list_price = data.get("list_price", 0.0)
            uptime = data.get("uptime", 0.0)
            obtainability = data.get("obtainability", 0.0)

            if preempt_rate <= 0.20 and obtainability >= 0.8 and uptime >= 3600:
                st.success(f"🟢 **spot availability**")
            elif preempt_rate <= 0.30 or obtainability >= 0.5 or uptime >= 1800:
                st.warning(f"🟡 **spot availability**")
            else:
                st.error(f"🔴 **spot availability**")
            
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("preemption rate", f"{float(preempt_rate) * 100:.0f}%")
            mc2.metric("price per hour", f"${float(list_price):.4f}")
            mc3.metric("uptime", f"{float(uptime):.0f}s")
            mc4.metric("obtainability", f"{float(obtainability) * 100:.0f}%")

    with st.form("training_params"):
        st.markdown("🏋️‍♂️ Training Parameters")
        batch_size = st.number_input("batch size", value=64, min_value=8, max_value=168, step=8, help="8 <= bs <= 168")
        epoch = st.number_input("epochs", value=1, min_value=1, max_value=10, step=1, help="1 <= epochs <= 10")
        n_samples = st.number_input("samples (n)", value=2000, min_value=1000, max_value=300000, step=1000, help="1000 <= n <= 300k")

        submitted = st.form_submit_button("🔥 start training", type="primary", use_container_width=True)

    if submitted:
        payload = {"batch_size": batch_size, "epoch": epoch, "n": n_samples}
        try:
            res = requests.post(f"{BACKEND_URL}/train", json=payload, timeout=10)
            if res.status_code == 200:
                msg = res.json().get("response")
                if msg:
                    st.write(msg)
            else:
                if "application/json" in res.headers.get("content-type", ""):
                    error_msg = res.json().get("detail", f"backend error: {res.status_code}")
                else:
                    error_msg = f"backend error: {res.status_code}"
                st.error(error_msg, icon="❌")
        except Exception as e:
            st.error(f"failed to connect to backend: {e}", icon="🔌")

    sc1, sc2 = st.columns(2)
    with sc1:
        if st.button("🛑 stop training", use_container_width=True, type="secondary"):
            try:
                requests.delete(f"{BACKEND_URL}/stop_train", timeout=5)
                st.info("stop signal sent...")
            except:
                st.error("could not reach backend...")
    with sc2:
        if st.button("🔄 sync weights", use_container_width=True):
            try:
                res = requests.post(f"{BACKEND_URL}/reload_model", timeout=5)
                st.toast(res.json().get("status", "syncing..."))
            except:
                st.error("sync failed...")

    st.subheader("📋 Live Training Monitor")
    @st.fragment(run_every="5s")
    def live_container_status():
        try:
            res = requests.get(f"{BACKEND_URL}/job_status", timeout=5)
            if res.status_code == 200:
                data = res.json()
            else:
                if "application/json" in res.headers.get("content-type", ""):
                    error_msg = res.json().get("detail", f"backend error: {res.status_code}")
                else:
                    error_msg = f"backend error: {res.status_code}"
                    data = {"status": error_msg, "color": "red", "job_name": "n/a"}
                st.error(error_msg, icon="❌")
        except Exception as e:
            st.error(f"failed to connect to backend: {e}", icon="🔌")

        current_status = data.get("status", "unknown")
        color = data.get("color", "green")
        job_name = data.get("job_name", "n/a")
        st.markdown(f"🏷️ Job Name: {job_name}")
        if color == "green":
            st.success(f"🟢 **Status:** {current_status}")
        elif color == "blue":
            st.info(f"🔵 **Status:** {current_status}")
        elif color == "yellow":
            st.warning(f"🟡 **Status:** {current_status}")
        else:
            st.error(f"🔴 **Status:** {current_status}")

    live_container_status()

    st.subheader("📋 Live Training Logs")
    @st.fragment(run_every="5s")
    def live_container_logs():
        try:
            res = requests.get(f"{BACKEND_URL}/log", timeout=5)
            if res.status_code == 200:
                data = res.json()
            else:
                if "application/json" in res.headers.get("content-type", ""):
                    error_msg = res.json().get("detail", f"backend error: {res.status_code}")
                else:
                    error_msg = f"backend error: {res.status_code}"
                    data = {"status": error_msg, "color": "red", "job_name": "n/a"}
                st.error(error_msg, icon="❌")
        except Exception as e:
            st.error(f"failed to connect to backend: {e}", icon="🔌")

        log_keys = [k for k in data.keys() if k.endswith("log")]
        
        if log_keys:
            for log_key in log_keys:
                with st.expander(f"📋 log stream: {log_key}", expanded=True):
                    st.code(data.get(log_key, "no data..."), language="bash")
        else:
            st.caption("no active container logs found...")

    live_container_logs()
    
with t3:
    st.subheader("📜 Training Runs")
    @st.fragment(run_every="10s")
    def refresh_history():
        try:
            res = requests.get(f"{BACKEND_URL}/history", timeout=5)
            if res.status_code == 200:
                data = res.json().get("history", ['no data...'])
                if data:
                    st.dataframe(
                        data,
                        column_config={
                            "batch_size": st.column_config.NumberColumn("batch size", format="%d"),
                            "epoch": st.column_config.NumberColumn("epochs", format="%d"),
                            "n": st.column_config.NumberColumn("n", format="%d"),
                            "test_loss": st.column_config.NumberColumn("test loss", format="%.4f"),
                            "status": "status",
                            "created_at": "created at",
                            "finished_at": "finished at"
                        },
                        hide_index=True,
                        use_container_width=True
                    )
                else:
                    st.info("no training runs found in history...")
            else:
                st.warning(f"status code: {res.status_code}")
        except Exception as e:
            st.error(f"backend error: {str(e)}")

    refresh_history()
