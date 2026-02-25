from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
import json

app = FastAPI(title="Orpheus Music API")

# Load model on startup
model = None
tokenizer = None

class GenerateRequest(BaseModel):
    genre: str = "boom_bap"
    tempo: int = 90
    instruments: list[str] = ["drums", "bass"]
    bars: int = 4
    seed_midi: str | None = None

@app.on_event("startup")
async def load_model() -> None:
    global model, tokenizer
    print("Loading Orpheus Music Transformer...")
    model = AutoModelForCausalLM.from_pretrained(
        "asigalov61/Orpheus-Music-Transformer",
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True
    )
    print("Model loaded!")

@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "model_loaded": model is not None}

@app.post("/generate")
async def generate(request: GenerateRequest) -> dict[str, Any]:
    # For MVP, generate a simple pattern
    # TODO: Use actual model inference
    return {"status": "ok", "midi_data": [], "message": "MVP placeholder"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10002)
