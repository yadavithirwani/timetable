import json
from typing import Dict, Any, List, Optional
from google import genai
from google.genai import types

def process_chat_message(
    user_message: str,
    history: List[Dict[str, str]],
    school_data: Dict[str, Any],
    current_timetable: Dict[str, Any],
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Processes ad-hoc timetable requests using Gemini AI neuro-symbolic translation."""
    
    # 1. Fallback / Graceful behavior if API key is missing
    if not api_key:
        # Check simple local string matching for fast offline testing/demoing
        msg_lower = user_message.lower()
        if "move" in msg_lower or "swap" in msg_lower:
            return {
                "response": "⚠️ **Gemini API Key Required**: To automatically parse and apply natural language modifications (like moving or swapping classes), please save your Gemini API Key in the **Setup / Config** drawer. \n\n*Alternatively, you can manually lock/swap classes directly using the web interface grid!*",
                "intent": "missing_key",
                "updated_timetable": None
            }
        elif "hello" in msg_lower or "hi" in msg_lower:
            return {
                "response": "Hello! I am your AI Timetable Assistant. Please enter your Gemini API Key in the setup drawer so I can analyze constraints and perform complex schedule adjustments for you automatically.",
                "intent": "greeting",
                "updated_timetable": None
            }
        else:
            return {
                "response": "I see your schedule request! To process ad-hoc natural language overrides, please supply your **Google Gemini API Key** in the settings drawer.",
                "intent": "missing_key",
                "updated_timetable": None
            }

    # 2. Invoke Gemini API for structured constraint parsing
    try:
        client = genai.Client(api_key=api_key)
        
        # Build strict context representation to keep token size optimal
        simplified_teachers = {t["id"]: t["name"] for t in school_data.get("teachers", [])}
        simplified_subjects = {s["id"]: s["name"] for s in school_data.get("subjects", [])}
        simplified_classes = {c["id"]: c["name"] for c in school_data.get("classes", [])}
        
        prompt = f"""
You are an expert neuro-symbolic AI timetabling assistant.
The user wants to make an ad-hoc modification or ask a question about the current timetable schedule.

Context metadata:
- Teachers: {json.dumps(simplified_teachers)}
- Subjects: {json.dumps(simplified_subjects)}
- Classes: {json.dumps(simplified_classes)}
- Days: {json.dumps(school_data.get("days"))}
- Periods per day: {school_data.get("periods_per_day")}

User Message: "{user_message}"

Analyze the request and return ONLY a valid JSON object matching this schema exactly:
{{
  "intent": "swap" | "query" | "general",
  "explanation_for_user": "Friendly response explaining what action is being taken or answering the query directly",
  "swap_params": {{
    "target_class_id": "c_10a", 
    "target_subject_id": "sub_phy",
    "new_day": "Wednesday",
    "new_period": 1
  }} // Only required if intent is 'swap', set fields to null if unknown
}}
"""
        # Request JSON output securely
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        
        ai_result = json.loads(response.text)
        intent = ai_result.get("intent", "general")
        explanation = ai_result.get("explanation_for_user", "Processing request...")
        
        # 3. If intent is 'swap', apply translation layer to re-solve with updated constraints
        if intent == "swap" and ai_result.get("swap_params"):
            params = ai_result["swap_params"]
            c_id = params.get("target_class_id")
            s_id = params.get("target_subject_id")
            new_d = params.get("new_day")
            new_p = params.get("new_period")
            
            if c_id and s_id and new_d and new_p is not None:
                # Let's find the current assignment to swap or override
                assignments = current_timetable.get("assignments", [])
                locked = []
                
                # Identify teacher for this subject in this class group
                t_id = None
                for t in school_data.get("teachers", []):
                    if s_id in t.get("qualified_subjects", []):
                        t_id = t["id"]
                        break
                        
                r_id = school_data["rooms"][0]["id"] # default room fallback
                for s in school_data.get("subjects", []):
                    if s["id"] == s_id and s.get("requires_lab"):
                        # Find lab room
                        for r in school_data.get("rooms", []):
                            if r.get("is_lab"):
                                r_id = r["id"]
                                break
                        break

                if t_id:
                    # Keep all existing assignments as soft references, but hard-lock the requested target override
                    locked.append({
                        "teacher_id": t_id,
                        "subject_id": s_id,
                        "class_id": c_id,
                        "room_id": r_id,
                        "day": new_d,
                        "period": int(new_p)
                    })
                    
                    from backend.optimizer.engine import generate_timetable
                    new_timetable = generate_timetable(school_data, locked_assignments=locked)
                    
                    if new_timetable.get("success"):
                        return {
                            "response": f"✨ **Ad-hoc Adjustment Applied Successfully!**\n\n{explanation}\n\n*The mathematical optimization engine has automatically adjusted surrounding slots to accommodate this constraint while ensuring zero clashes.*",
                            "intent": "swap_success",
                            "updated_timetable": new_timetable
                        }
                    else:
                        return {
                            "response": f"⚠️ **Constraint Conflict Detected**\n\nI attempted to apply your requested change ({explanation}), but the optimizer found that it results in an infeasible schedule (e.g., creating an unavoidable teacher clash or mandatory room overlap). \n\nPlease suggest an alternative open timeslot!",
                            "intent": "swap_conflict",
                            "updated_timetable": None
                        }

        return {
            "response": explanation,
            "intent": intent,
            "updated_timetable": None
        }

    except Exception as e:
        return {
            "response": f"⚠️ **AI Engine Error**: Could not reach Google Gemini API successfully. Details: `{str(e)}`\n\nPlease verify your API Key or connection settings.",
            "intent": "error",
            "updated_timetable": None
        }
