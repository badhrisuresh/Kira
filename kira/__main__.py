"""Entry point: python -m kira"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("kira.server:app", host="0.0.0.0", port=8080, reload=True)
