from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json
import os

from backend.optimizer.engine import generate_timetable

app = FastAPI(title="Timetable Manager API", description="AI-Powered Timetable Optimization & Scheduling API")

# Optional shared password for deployments: set APP_PASSWORD (and optionally APP_USER).
# The browser shows its standard sign-in prompt; /api/v2/health stays open for uptime checks.
_APP_PASSWORD = os.environ.get("APP_PASSWORD")
if _APP_PASSWORD:
    import base64
    import secrets

    from fastapi import Request
    from fastapi.responses import Response

    _APP_USER = os.environ.get("APP_USER", "school")

    @app.middleware("http")
    async def _basic_auth(request: Request, call_next):
        if request.url.path == "/api/v2/health":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                if secrets.compare_digest(user, _APP_USER) and secrets.compare_digest(pw, _APP_PASSWORD):
                    return await call_next(request)
            except Exception:
                pass
        return Response("Sign in required", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Timetable Studio"'})

# Allow CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "config", "my_school_config.json"))
TIMETABLE_SAVE_PATH = os.path.join(BASE_DIR, "data", "saved_timetable.json")

# Helper functions for JSON persistence
def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_json(path: str, data: Dict[str, Any]):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

@app.get("/api/school-data")
def get_school_data():
    """Retrieves current school entities/configuration."""
    data = load_json(DATA_PATH)
    if not data:
        raise HTTPException(status_code=404, detail="School data configuration not found.")
    return data

@app.post("/api/school-data")
def update_school_data(data: Dict[str, Any] = Body(...)):
    """Saves updated school configuration entities."""
    save_json(DATA_PATH, data)
    return {"success": True, "message": "School configuration saved successfully."}

class GenerateRequest(BaseModel):
    locked_assignments: Optional[List[Dict[str, Any]]] = None

@app.post("/api/generate")
def trigger_generation(req: GenerateRequest = Body(default=GenerateRequest())):
    """Triggers the CP-SAT optimization engine to solve the timetable."""
    school_data = load_json(DATA_PATH)
    if not school_data:
        raise HTTPException(status_code=400, detail="Cannot generate timetable without school configuration data.")
        
    result = generate_timetable(school_data, locked_assignments=req.locked_assignments)
    
    # Save successfully generated timetable automatically
    if result.get("success"):
        save_json(TIMETABLE_SAVE_PATH, result)
        
    return result

@app.post("/api/import-excel")
def import_excel():
    """Rebuild the school configuration from config/SHIKHA_CLASSWISE.xlsx + config/period_quotas.json."""
    from backend.ingest.excel_loader import load_school_from_excel
    try:
        school = load_school_from_excel()
    except Exception as e:  # surface the reason to the UI instead of a 500
        raise HTTPException(status_code=400, detail=f"Could not read the Excel file: {e}")
    save_json(DATA_PATH, school)
    return {"success": True, "classes": len(school["classes"]), "teachers": len(school["teachers"]),
            "lessons": len(school["lessons"]), "warnings": school["import_warnings"]}


@app.get("/api/timetable")
def get_saved_timetable():
    """Retrieves the latest successfully generated timetable."""
    data = load_json(TIMETABLE_SAVE_PATH)
    if not data:
        return {"success": False, "assignments": [], "message": "No saved timetable found."}
    return data

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []
    api_key: Optional[str] = None

@app.post("/api/ai/chat")
def ai_assistant_chat(req: ChatRequest):
    """Communicates with the Gemini AI engine to propose natural language modifications."""
    from backend.ai.assistant import process_chat_message
    
    school_data = load_json(DATA_PATH)
    current_timetable = load_json(TIMETABLE_SAVE_PATH)
    
    response = process_chat_message(
        user_message=req.message,
        history=req.history,
        school_data=school_data,
        current_timetable=current_timetable,
        api_key=req.api_key
    )
    
    # If the AI proposed constraint modifications that trigger a successful update, persist it
    if response.get("updated_timetable") and response["updated_timetable"].get("success"):
        save_json(TIMETABLE_SAVE_PATH, response["updated_timetable"])
        
    return response


# Upload-first API used by the new web app
from backend.api_v2 import router as v2_router  # noqa: E402

app.include_router(v2_router)


# Serve the web UI from the same server so it opens at http://localhost:8000
# (mounted last so the /api routes above take precedence).
from fastapi.staticfiles import StaticFiles  # noqa: E402

_FRONTEND = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))
if os.path.isdir(_FRONTEND):
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
