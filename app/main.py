from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import creator, editor, offers

app = FastAPI(title="Adrobot")
app.include_router(creator.router)
app.include_router(editor.router)
app.include_router(offers.router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/creator")


@app.get("/creator", include_in_schema=False)
def creator_page():
    return FileResponse(static_dir / "creator.html")


@app.get("/editor", include_in_schema=False)
def editor_page():
    return FileResponse(static_dir / "editor.html")
