from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class TimeSlot(BaseModel):
    day: str  # e.g., "Monday", "Tuesday"
    period: int  # e.g., 1, 2, 3...

class Subject(BaseModel):
    id: str
    name: str
    periods_per_week: int
    requires_lab: bool = False

class Teacher(BaseModel):
    id: str
    name: str
    qualified_subjects: List[str]  # List of Subject IDs
    max_periods_per_day: int = 6
    unavailable_slots: List[TimeSlot] = []

class ClassGroup(BaseModel):
    id: str
    name: str  # e.g., "10-A"
    grade: int
    section: str
    subject_ids: List[str]  # List of Subject IDs required for this class

class Room(BaseModel):
    id: str
    name: str
    capacity: int = 40
    is_lab: bool = False

class Assignment(BaseModel):
    teacher_id: str
    subject_id: str
    class_id: str
    room_id: str
    day: str
    period: int

class TimetableOutput(BaseModel):
    assignments: List[Assignment]
    score: float = 0.0
    violations: List[str] = []
    metadata: Dict[str, Any] = {}
