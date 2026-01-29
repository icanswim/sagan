import streamlit as st
import requests
import os

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.title("Sagan Analytics")

if st.button("Fetch Backend Data"):
    try:
        response = requests.get(f"{BACKEND_URL}/data")
        response.raise_for_status() 
        st.success("Successfully connected!")
        st.json(response.json())
    except Exception as e:
        st.error(f"Failed to reach backend at {BACKEND_URL}/data. Error: {e}")

