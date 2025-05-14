from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import json
from typing import List, Dict, Optional, Any, Tuple
import httpx # Import httpx

from thefuzz import fuzz

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent
# JSON_FILENAME = "results.json" # No longer reading from a local file directly
# JSON_DATA_PATH = BASE_DIR / JSON_FILENAME
JSON_DATA_URL = "https://6e69rsh8dqy6qfjr.public.blob.vercel-storage.com/results-QevAmcRFR28bF5A5vue9SEdZn51R2V.json" # Your Vercel Blob URL
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
PHYSICAL_EDUCATION_SUBJECT_CODES = ["048", "843"]
PHYSICAL_EDUCATION_SUBJECT_KEYWORDS = ["PHYSICAL EDUCATION", "PHY.EDUCATION", "HEALTH & PHYSICAL EDUCATION", "ARTIFICIAL INTELLIGENCE"]
DEFAULT_MAX_MARKS_PER_SUBJECT = 100

# --- Startup Event to Load JSON Data from URL ---
@app.on_event("startup")
async def load_data_from_url():
    global RESULTS_DATA_STORE
    print(f"Attempting to load data from URL: {JSON_DATA_URL}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client: # Increased timeout for potentially large files
            response = await client.get(JSON_DATA_URL)
            response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
            RESULTS_DATA_STORE = response.json() # Directly parse JSON from response
            print(f"Successfully loaded and parsed JSON data from URL. Found {len(RESULTS_DATA_STORE)} records.")
    except httpx.HTTPStatusError as e:
        print(f"ERROR: HTTP error occurred while fetching data from URL: {e.request.url} - {e.response.status_code}")
        RESULTS_DATA_STORE = {}
    except httpx.RequestError as e:
        print(f"ERROR: Request error occurred while fetching data from URL: {e.request.url} - {e}")
        RESULTS_DATA_STORE = {}
    except json.JSONDecodeError as e:
        print(f"ERROR: Could not decode JSON from URL response. Error: {e}")
        RESULTS_DATA_STORE = {}
    except Exception as e:
        print(f"ERROR: An unexpected error occurred while loading data from URL. Error: {e}")
        RESULTS_DATA_STORE = {}

    if not RESULTS_DATA_STORE:
        print("WARNING: RESULTS_DATA_STORE is empty after attempting to load from URL.")


def check_data_status() -> Dict[str, Any]:
    """Checks if the JSON data was loaded successfully."""
    return {
        "loaded": bool(RESULTS_DATA_STORE),
        "source_url": JSON_DATA_URL, # Indicate the source
        "record_count": len(RESULTS_DATA_STORE)
    }

# --- Helper Functions (get_student_data_from_json, calculate_student_percentages_from_json - keep as is) ---
def get_student_data_from_json(roll_no: str) -> Optional[Dict[str, Any]]:
    return RESULTS_DATA_STORE.get(roll_no)

def calculate_student_percentages_from_json(subjects_dict: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    total_obtained_all = 0
    num_scorable_subjects_all = 0
    total_obtained_no_pe = 0
    num_scorable_subjects_no_pe = 0
    found_pe_subject = False

    for sub_code, subject_data in subjects_dict.items():
        marks_str = subject_data.get("marks")
        subject_name_lower = str(subject_data.get("sub_name", "")).lower()
        if marks_str == "---" or not marks_str: continue
        try:
            marks_value = int(marks_str)
            total_obtained_all += marks_value
            num_scorable_subjects_all += 1
            is_pe_or_additional_type = False
            if sub_code in PHYSICAL_EDUCATION_SUBJECT_CODES: is_pe_or_additional_type = True
            else:
                for keyword in PHYSICAL_EDUCATION_SUBJECT_KEYWORDS:
                    if keyword.lower() in subject_name_lower: is_pe_or_additional_type = True; break
            if is_pe_or_additional_type: found_pe_subject = True
            else:
                total_obtained_no_pe += marks_value
                num_scorable_subjects_no_pe += 1
        except (ValueError, TypeError): continue

    percentage_overall = round((total_obtained_all / (num_scorable_subjects_all * DEFAULT_MAX_MARKS_PER_SUBJECT)) * 100, 2) if num_scorable_subjects_all > 0 and (num_scorable_subjects_all * DEFAULT_MAX_MARKS_PER_SUBJECT) > 0 else None
    percentage_excluding_pe = None
    if num_scorable_subjects_no_pe > 0 and (num_scorable_subjects_no_pe * DEFAULT_MAX_MARKS_PER_SUBJECT) > 0:
        percentage_excluding_pe = round((total_obtained_no_pe / (num_scorable_subjects_no_pe * DEFAULT_MAX_MARKS_PER_SUBJECT)) * 100, 2)
    elif not found_pe_subject and percentage_overall is not None:
        percentage_excluding_pe = percentage_overall
        num_scorable_subjects_no_pe = num_scorable_subjects_all
    return {
        "percentage_overall": percentage_overall, "num_subjects_overall": num_scorable_subjects_all,
        "percentage_excluding_pe": percentage_excluding_pe, "num_subjects_excluding_pe": num_scorable_subjects_no_pe,
        "found_pe_subject": found_pe_subject
    }

# --- Routes (read_root, search_results_page - modify slightly for data_status key name) ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    data_status = check_data_status()
    # Using data_status for the template to avoid confusion with old db_status
    return templates.TemplateResponse("index.html", {"request": request, "data_status": data_status})


@app.get("/search", response_class=HTMLResponse)
async def search_results_page(
    request: Request,
    candidate_name: str = Query(None, min_length=2, max_length=100, description="Search by Candidate Name (fuzzy match)")
):
    results_to_display = []
    search_performed = bool(candidate_name)
    error_message = None
    data_status = check_data_status() # Get current data status

    if not candidate_name:
        search_performed = False
        error_message = "Please enter a candidate name to search."
        return templates.TemplateResponse("results.html", {
            "request": request, "results_data": [], "search_performed": search_performed,
            "error_message": error_message, "candidate_name_query": candidate_name
        })

    if not data_status["loaded"]: # Check if data was successfully loaded from URL
        error_message = f"Data could not be loaded from the source ({data_status.get('source_url', 'configured URL')}). Search unavailable."
        return templates.TemplateResponse("results.html", {
            "request": request, "results_data": [], "search_performed": search_performed,
            "error_message": error_message, "candidate_name_query": candidate_name
        })

    try:
        potential_matches: List[Tuple[str, str, int]] = []
        search_name_lower = candidate_name.lower()

        for roll_no, student_info in RESULTS_DATA_STORE.items(): # Iterate over the global store
            db_name = student_info.get("candidate_name", "")
            db_name_lower = db_name.lower()
            score = fuzz.token_set_ratio(search_name_lower, db_name_lower)
            if score >= SIMILARITY_THRESHOLD:
                potential_matches.append((roll_no, db_name, score))
        
        potential_matches.sort(key=lambda x: x[2], reverse=True)
        
        distinct_roll_nos_processed = set()
        for roll_no_match, matched_name, match_score in potential_matches:
            if len(results_to_display) >= MAX_FUZZY_RESULTS: break
            if roll_no_match in distinct_roll_nos_processed: continue
            
            student_full_data = get_student_data_from_json(roll_no_match)
            if student_full_data:
                student_details = {
                    "roll_no": roll_no_match,
                    "candidate_name": student_full_data.get("candidate_name"),
                    "mother_name": student_full_data.get("mother_name"),
                    "father_name": student_full_data.get("father_name"),
                    "school_name": student_full_data.get("school_name"),
                    "result_status": student_full_data.get("Result")
                }
                subjects_list_for_template = []
                raw_subjects_data = student_full_data.get("marks", {})
                for sub_code, sub_details in raw_subjects_data.items():
                    subjects_list_for_template.append({
                        "sub_code": sub_code, "sub_name": sub_details.get("sub_name"),
                        "theory": sub_details.get("theory"), "practical": sub_details.get("practical"),
                        "marks": sub_details.get("marks"), "positional_grade": sub_details.get("positional_grade")
                    })
                
                pre_calc_overall = student_full_data.get("marks_percentage_with_add")
                pre_calc_excl_pe = student_full_data.get("marks_percentage_without_add")
                calculated_percentages = calculate_student_percentages_from_json(raw_subjects_data)
                final_percentages = {}
                if isinstance(pre_calc_overall, (float, int)): final_percentages["percentage_overall"] = pre_calc_overall
                else: final_percentages["percentage_overall"] = calculated_percentages["percentage_overall"]
                if isinstance(pre_calc_excl_pe, (float, int)): final_percentages["percentage_excluding_pe"] = pre_calc_excl_pe
                else: final_percentages["percentage_excluding_pe"] = calculated_percentages["percentage_excluding_pe"]
                final_percentages["num_subjects_overall"] = calculated_percentages["num_subjects_overall"]
                final_percentages["num_subjects_excluding_pe"] = calculated_percentages["num_subjects_excluding_pe"]
                final_percentages["found_pe_subject"] = calculated_percentages["found_pe_subject"]

                results_to_display.append({
                    "details": student_details, "subjects": subjects_list_for_template,
                    "percentages": final_percentages, "match_score": match_score
                })
                distinct_roll_nos_processed.add(roll_no_match)
    except Exception as e:
        error_message = f"An error occurred during the search: {str(e)}. Please check server logs."
        print(f"Search error: {e}")

    return templates.TemplateResponse("results.html", {
        "request": request, "results_data": results_to_display,
        "search_performed": search_performed, "error_message": error_message,
        "candidate_name_query": candidate_name
    })

# --- For local development ---
if __name__ == "__main__":
    import uvicorn
    print("Ensure Tailwind CSS is built. For dev: 'npm run watch:css'.")
    print("Starting Uvicorn server. Will load data from URL on startup.")
    print(f"Data URL: {JSON_DATA_URL}")
    print("Access at http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)