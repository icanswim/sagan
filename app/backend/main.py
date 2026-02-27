from fastapi import FastAPI
import torch

app = FastAPI()

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "sagan-backend"}

@app.get("/data")
async def get_data():
    return {
        "message": "sagan backend...🚀",
        "version": "1.0.0",
        "torch.__version__": torch.__version__
    }
