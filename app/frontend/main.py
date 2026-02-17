import streamlit as st
import requests
import os

# GKE Service Discovery: Use the internal Kubernetes DNS name
# Default to localhost for local testing
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend-service:8000")

st.set_page_config(page_title="Sagan Analytics", page_icon="🚀")
st.title("🚀 Sagan Analytics")
st.write(f"Connected to backend at: `{BACKEND_URL}`")

# Use a columns layout for a cleaner UI
col1, col2 = st.columns(2)

with col1:
    if st.button("📡 Fetch Backend Data", use_container_width=True):
        try:
            # We assume your FastAPI has a /data or /health endpoint
            response = requests.get(f"{BACKEND_URL}/data", timeout=5)
            response.raise_for_status() 
            st.success("Connection Successful!")
            st.json(response.json())
        except Exception as e:
            st.error(f"Backend Unreachable")
            st.caption(f"Error details: {e}")

with col2:
    if st.button("🛡️ Check Health", use_container_width=True):
        try:
            response = requests.get(f"{BACKEND_URL}/health", timeout=2)
            if response.status_code == 200:
                st.balloons()
                st.info("Backend is Healthy!")
        except:
            st.warning("Health check failed.")
