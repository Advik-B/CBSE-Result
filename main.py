from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import json # For loading JSON
from typing import List, Dict, Optional, Any, Tuple

from thefuzz import fuzz # For fuzzy matching scores

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent
JSON_FILENAME = "results.json" # Name of your JSON data file
JSON_DATA_PATH = BASE_DIR / JSON_FILENAME
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# --- Fuzzy Search Configuration ---
SIMILARITY_THRESHOLD = 70
MAX_FUZZY_RESULTS = 10

# --- Global variable to hold loaded JSON data ---
RESULTS_DATA_STORE: Dict[str, Any] = {}

app = FastAPI(title="CBSE Result Viewer")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Constants for Percentage Calculation ---
PHYSICAL_EDUCATION_SUBJECT_CODES = ["048", "843"] # Added 843 for AI if it's additional
PHYSICAL_EDUCATION_SUBJECT_KEYWORDS = ["PHYSICAL EDUCATION", "PHY.EDUCATION", "HEALTH & PHYSICAL EDUCATION", "ARTIFICIAL INTELLIGENCE"]
DEFAULT_MAX_MARKS_PER_SUBJECT = 100
# Consider if "additional" subjects like "ARTIFICIAL INTELLIGENCE" (843) should be treated like P.E.
# or if they are always part of the main 5/6 subjects for percentage.
# The current logic will exclude it if its code/keyword matches.

# --- Startup Event to Load JSON Data ---
@app.on_event("startup")
async def load_data():
    global RESULTS_DATA_STORE
    if JSON_DATA_PATH.is_file():
        try:
            with open(JSON_DATA_PATH, "r", encoding="utf-8") as f:
                RESULTS_DATA_STORE = json.load(f)
            print(f"Successfully loaded data from {JSON_FILENAME}. Found {len(RESULTS_DATA_STORE)} records.")
        except json.JSONDecodeError as e:
            print(f"ERROR: Could not decode JSON from {JSON_FILENAME}. Error: {e}")
            RESULTS_DATA_STORE = {} # Ensure it's an empty dict on error
        except Exception as e:
            print(f"ERROR: An unexpected error occurred while loading {JSON_FILENAME}. Error: {e}")
            RESULTS_DATA_STORE = {}
    else:
        print(f"WARNING: Data file {JSON_FILENAME} not found at {JSON_DATA_PATH}. Search will not work.")
        RESULTS_DATA_STORE = {}

def check_data_status() -> Dict[str, Any]:
    """Checks if the JSON data was loaded successfully."""
    return {
        "loaded": bool(RESULTS_DATA_STORE), # True if data store is not empty
        "path": str(JSON_DATA_PATH),
        "filename": JSON_FILENAME,
        "record_count": len(RESULTS_DATA_STORE)
    }

def get_student_data_from_json(roll_no: str) -> Optional[Dict[str, Any]]:
    """Helper to get a single student's full data by roll number."""
    return RESULTS_DATA_STORE.get(roll_no)

def calculate_student_percentages_from_json(subjects_dict: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """
    Calculates percentages based on the 'marks' structure from the JSON.
    """
    total_obtained_all = 0
    num_scorable_subjects_all = 0
    total_obtained_no_pe = 0
    num_scorable_subjects_no_pe = 0
    found_pe_subject = False
    
    # Check for pre-calculated percentages in the JSON
    # If they exist and are not null, use them. Otherwise, calculate.
    # This part is new based on your JSON structure which has pre-calculated percentages
    # We'll make the function still calculate if these aren't present or null

    for sub_code, subject_data in subjects_dict.items():
        marks_str = subject_data.get("marks")
        subject_name_lower = str(subject_data.get("sub_name", "")).lower()
        
        # Skip non-scorable subjects like "WORK EXPERIENCE" based on marks "---"
        if marks_str == "---" or not marks_str: # also checking for empty string
            continue

        try:
            marks_value = int(marks_str)
            
            total_obtained_all += marks_value
            num_scorable_subjects_all += 1

            is_pe_or_additional_type = False
            if sub_code in PHYSICAL_EDUCATION_SUBJECT_CODES:
                is_pe_or_additional_type = True
            else:
                for keyword in PHYSICAL_EDUCATION_SUBJECT_KEYWORDS:
                    if keyword.lower() in subject_name_lower:
                        is_pe_or_additional_type = True
                        break
            
            if is_pe_or_additional_type:
                found_pe_subject = True # Mark that we found a PE-like subject
            else:
                total_obtained_no_pe += marks_value
                num_scorable_subjects_no_pe += 1
        
        except (ValueError, TypeError):
            # If marks cannot be converted to int (e.g., "AB", "NE", or it was empty after all)
            # print(f"Warning: Could not parse marks '{marks_str}' for subject {sub_code} - {subject_name_lower}")
            continue

    percentage_overall = None
    if num_scorable_subjects_all > 0:
        max_marks_all = num_scorable_subjects_all * DEFAULT_MAX_MARKS_PER_SUBJECT
        percentage_overall = round((total_obtained_all / max_marks_all) * 100, 2) if max_marks_all > 0 else 0

    percentage_excluding_pe = None
    if num_scorable_subjects_no_pe > 0:
        max_marks_no_pe = num_scorable_subjects_no_pe * DEFAULT_MAX_MARKS_PER_SUBJECT
        percentage_excluding_pe = round((total_obtained_no_pe / max_marks_no_pe) * 100, 2) if max_marks_no_pe > 0 else 0
    elif not found_pe_subject and percentage_overall is not None: # No PE found, so "excl. PE" is same as overall
        percentage_excluding_pe = percentage_overall
        num_scorable_subjects_no_pe = num_scorable_subjects_all

    return {
        "percentage_overall": percentage_overall,
        "num_subjects_overall": num_scorable_subjects_all,
        "percentage_excluding_pe": percentage_excluding_pe,
        "num_subjects_excluding_pe": num_scorable_subjects_no_pe,
        "found_pe_subject": found_pe_subject
    }


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    data_status = check_data_status()
    # Rename db_status to data_status for clarity in template if you prefer
    return templates.TemplateResponse("index.html", {"request": request, "db_status": data_status})


@app.get("/search", response_class=HTMLResponse)
async def search_results_page(
    request: Request,
    candidate_name: str = Query(None, min_length=2, max_length=100, description="Search by Candidate Name (fuzzy match)")
):
    results_to_display = [] # This will hold the final formatted data for the template
    search_performed = bool(candidate_name)
    error_message = None
    data_status = check_data_status()

    if not candidate_name:
        search_performed = False
        error_message = "Please enter a candidate name to search."
        return templates.TemplateResponse("results.html", {
            "request": request, "results_data": [], "search_performed": search_performed,
            "error_message": error_message, "candidate_name_query": candidate_name
        })

    if not data_status["loaded"]:
        error_message = f"Data file '{JSON_FILENAME}' not found or is empty. Search unavailable."
        return templates.TemplateResponse("results.html", {
            "request": request, "results_data": [], "search_performed": search_performed,
            "error_message": error_message, "candidate_name_query": candidate_name
        })

    try:
        potential_matches: List[Tuple[str, str, int]] = [] # (roll_no, name, score)
        search_name_lower = candidate_name.lower()

        for roll_no, student_info in RESULTS_DATA_STORE.items():
            db_name = student_info.get("candidate_name", "")
            db_name_lower = db_name.lower()
            
            # Using fuzz.token_set_ratio for better name matching
            score = fuzz.token_set_ratio(search_name_lower, db_name_lower)
            
            if score >= SIMILARITY_THRESHOLD:
                potential_matches.append((roll_no, db_name, score))
        
        # Sort matches by score in descending order
        potential_matches.sort(key=lambda x: x[2], reverse=True)
        
        # Prepare data for the top N distinct matches
        distinct_roll_nos_processed = set()
        for roll_no_match, matched_name, match_score in potential_matches:
            if len(results_to_display) >= MAX_FUZZY_RESULTS:
                break
            if roll_no_match in distinct_roll_nos_processed:
                continue
            
            student_full_data = get_student_data_from_json(roll_no_match)
            if student_full_data:
                # Extract student details
                student_details = {
                    "roll_no": roll_no_match,
                    "candidate_name": student_full_data.get("candidate_name"),
                    "mother_name": student_full_data.get("mother_name"),
                    "father_name": student_full_data.get("father_name"),
                    "school_name": student_full_data.get("school_name"),
                    "result_status": student_full_data.get("Result") # Get the overall PASS/FAIL/ABST
                }

                # Extract and format subject marks
                subjects_list_for_template = []
                raw_subjects_data = student_full_data.get("marks", {})
                for sub_code, sub_details in raw_subjects_data.items():
                    subjects_list_for_template.append({
                        "sub_code": sub_code,
                        "sub_name": sub_details.get("sub_name"),
                        "theory": sub_details.get("theory"),
                        "practical": sub_details.get("practical"),
                        "marks": sub_details.get("marks"),
                        "positional_grade": sub_details.get("positional_grade")
                    })
                
                # Use pre-calculated percentages if available and valid, otherwise calculate
                pre_calc_overall = student_full_data.get("marks_percentage_with_add") # Assuming this is overall
                pre_calc_excl_pe = student_full_data.get("marks_percentage_without_add")

                calculated_percentages = calculate_student_percentages_from_json(raw_subjects_data)

                final_percentages = {}
                if isinstance(pre_calc_overall, (float, int)):
                    final_percentages["percentage_overall"] = pre_calc_overall
                else:
                    final_percentages["percentage_overall"] = calculated_percentages["percentage_overall"]
                
                if isinstance(pre_calc_excl_pe, (float, int)):
                     final_percentages["percentage_excluding_pe"] = pre_calc_excl_pe
                else:
                    final_percentages["percentage_excluding_pe"] = calculated_percentages["percentage_excluding_pe"]

                # Keep calculated subject counts and found_pe_subject status
                final_percentages["num_subjects_overall"] = calculated_percentages["num_subjects_overall"]
                final_percentages["num_subjects_excluding_pe"] = calculated_percentages["num_subjects_excluding_pe"]
                final_percentages["found_pe_subject"] = calculated_percentages["found_pe_subject"]


                results_to_display.append({
                    "details": student_details,
                    "subjects": subjects_list_for_template,
                    "percentages": final_percentages,
                    "match_score": match_score # Optional: for display/debug
                })
                distinct_roll_nos_processed.add(roll_no_match)

    except Exception as e:
        error_message = f"An error occurred during the search: {str(e)}. Please check server logs."
        print(f"Search error: {e}") # Log for debugging

    return templates.TemplateResponse("results.html", {
        "request": request,
        "results_data": results_to_display, # Use the new list name
        "search_performed": search_performed,
        "error_message": error_message,
        "candidate_name_query": candidate_name
    })

# --- For local development ---
if __name__ == "__main__":
    import uvicorn
    # The load_data function will be called automatically on startup by FastAPI
    print("Ensure Tailwind CSS is built. For dev: 'npm run watch:css'.")
    print("Starting Uvicorn server. Access at http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False) # reload=True might cause issues with global data store