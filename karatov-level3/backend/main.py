from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"status": "LEVEL 3 READY"}
