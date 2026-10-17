from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
app = FastAPI(title="Social LeagueCut", docs_url=None, redoc_url=None)

@app.get("/health")
def health():
    return {"status": "ok", "service": "social-leaguecut"}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"title": "Social LeagueCut"})
