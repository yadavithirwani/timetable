import json
import os
from backend.optimizer.engine import generate_timetable

def run_test():
    base_dir = os.path.dirname(__file__)
    data_path = os.path.join(base_dir, "data", "sample_school.json")
    
    with open(data_path, "r") as f:
        school_data = json.load(f)
        
    print("Running Timetable Optimizer Engine...")
    result = generate_timetable(school_data)
    
    print(f"Success: {result['success']}")
    print(f"Status: {result['status']}")
    print(f"Total Assignments: {len(result['assignments'])}")
    print(f"Metadata: {json.dumps(result['metadata'], indent=2)}")
    
    if result['success']:
        # Let's print out the timetable for 10-A on Monday as a sanity check
        print("\n--- Monday Schedule for Class 10-A ---")
        mon_10a = [a for a in result['assignments'] if a['class_id'] == 'c_10a' and a['day'] == 'Monday']
        mon_10a.sort(key=lambda x: x['period'])
        for a in mon_10a:
            print(f"Period {a['period']}: Subject {a['subject_id']} with Teacher {a['teacher_id']} in Room {a['room_id']}")
    else:
        print(f"Violations: {result.get('violations')}")

if __name__ == "__main__":
    run_test()
