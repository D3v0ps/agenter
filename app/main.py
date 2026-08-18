"""Minimal FastAPI-app.

Endpoints för webhooks (t.ex. inkommande SMS-svar) tillkommer i senare steg.
Kärnan exponerar än så länge endast en hälsokontroll — ingen affärslogik får
ligga här; allt går via serviceskiktet (app/services/)."""
from fastapi import FastAPI

app = FastAPI(title="Miljonbemanning — deterministisk kärna", version="0.1.0")


@app.get("/halsa")
def halsa() -> dict[str, str]:
    return {"status": "ok"}
