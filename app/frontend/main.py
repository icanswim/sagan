import streamlit as st
import requests
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

st.set_page_config(page_title="sagan", page_icon="🚀")
st.title("sagan frontend")
st.write(f"connected to backend at: `{BACKEND_URL}`")

col1, col2 = st.columns(3)

with col1:
    if st.button("📡 connect backend", use_container_width=True):
        try:
            response = requests.get(f"{BACKEND_URL}/data", timeout=5)
            response.raise_for_status() 
            st.success("connected...")
            st.json(response.json())
        except Exception as e:
            st.error(f"backend unreachable:`{BACKEND_URL}`")
            st.caption(f"exception: {e}")

with col2:
    if st.button("🛡️ health check", use_container_width=True):
        try:
            response = requests.get(f"{BACKEND_URL}/health", timeout=2)
            if response.status_code == 200:
                st.balloons()
                st.info("healthy...")
        except:
            st.warning("not healthy...")

with col3:
    if st.button("⏳ training status...", use_container_width=True):
        try:
            response = requests.get(f"{BACKEND_URL}/training-status", timeout=2)
            response.raise_for_status()
            st.json(response.json())
        except Exception as e:
            st.warning(f"not ready: {e}")

prompt = st.text_input("Hey Shakespeare...")
if st.button("🚀 send prompt", use_container_width=True):
    try:
        response = requests.post(f"{BACKEND_URL}/prompt", json={"content": prompt}, timeout=5)
        response.raise_for_status() 
        st.success("prompt sent...")
        st.json(response.json())
    except Exception as e:
        st.error(f"failed to send prompt to backend")
        st.caption(f"exception: {e}")
