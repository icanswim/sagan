from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"hello": "world"}

@app.get("/healthz")
def health_check():
    return {"status": "ok"}


