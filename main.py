import aiosqlite
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import os
from typing import List, Dict, Optional, Any

from thefuzz import fuzz # For fuzzy matching scores
# from thefuzz import process # Alternative for extracting bests, we'll do manual iteration for more control

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent
DB_FILENAME = "cbse_results.sqlite3"
DB_PATH = BASE_DIR / DB_FILENAME
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# --- Fuzzy Search Configuration ---
SIMILARITY_THRESHOLD = 70  # Percentage (0-100). Adjust based on desired "fuzziness". 70-80 is often a good start.
MAX_FUZZY_RESULTS = 10     # Limit the number of displayed fuzzy search results

app = FastAPI(title="CBSE Result Viewer")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Constants for Percentage Calculation (from previous step) ---
PHYSICAL_EDUCATION_SUBJECT_CODES = ["048"]
PHYSICAL_EDUCATION_SUBJECT_KEYWORDS = ["PHYSICAL EDUCATION", "PHY.EDUCATION", "HEALTH & PHYSICAL EDUCATION"]
DEFAULT_MAX_MARKS_PER_SUBJECT = 100

# --- Helper Functions (get_db_connection, check_db_status, calculate_student_percentages - keep as is) ---
async def get_db_connection():
    try:
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        return conn
    except aiosqlite.OperationalError as e:
        print(f"OperationalError connecting to database at {DB_PATH}: {e}")
        return None
    except Exception as e:
        print(f"Generic error connecting to database: {e}")
        return None

def check_db_status():
    db_file_exists = DB_PATH.is_file()
    return {"exists": db_file_exists, "path": str(DB_PATH), "filename": DB_FILENAME}

def calculate_student_percentages(subjects_list: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    total_obtained_all = 0
    num_scorable_subjects_all = 0
    total_obtained_no_pe = 0
    num_scorable_subjects_no_pe = 0
    found_pe_subject = False

    for subject_data in subjects_list:
        marks_str = subject_data.get("marks")
        subject_name_lower = str(subject_data.get("sub_name", "")).lower()
        subject_code = str(subject_data.get("sub_code", ""))
        try:
            marks_value = int(marks_str)
            total_obtained_all += marks_value
            num_scorable_subjects_all += 1
            is_pe = False
            if subject_code in PHYSICAL_EDUCATION_SUBJECT_CODES: is_pe = True
            else:
                for keyword in PHYSICAL_EDUCATION_SUBJECT_KEYWORDS:
                    if keyword.lower() in subject_name_lower: is_pe = True; break
            if is_pe: found_pe_subject = True
            else:
                total_obtained_no_pe += marks_value
                num_scorable_subjects_no_pe += 1
        except (ValueError, TypeError): continue

    percentage_overall = round((total_obtained_all / (num_scorable_subjects_all * DEFAULT_MAX_MARKS_PER_SUBJECT)) * 100, 2) if num_scorable_subjects_all > 0 else None
    percentage_excluding_pe = None
    if num_scorable_subjects_no_pe > 0:
        percentage_excluding_pe = round((total_obtained_no_pe / (num_scorable_subjects_no_pe * DEFAULT_MAX_MARKS_PER_SUBJECT)) * 100, 2)
    elif not found_pe_subject and percentage_overall is not None:
        percentage_excluding_pe = percentage_overall
        num_scorable_subjects_no_pe = num_scorable_subjects_all
    return {
        "percentage_overall": percentage_overall, "num_subjects_overall": num_scorable_subjects_all,
        "percentage_excluding_pe": percentage_excluding_pe, "num_subjects_excluding_pe": num_scorable_subjects_no_pe,
        "found_pe_subject": found_pe_subject
    }

# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    db_status = check_db_status()
    return templates.TemplateResponse("index.html", {"request": request, "db_status": db_status})

@app.get("/search", response_class=HTMLResponse)
async def search_results_page(
    request: Request,
    candidate_name: str = Query(None, min_length=2, max_length=100, description="Search by Candidate Name (fuzzy match)")
):
    results_data = []
    search_performed = bool(candidate_name)
    error_message = None
    db_status = check_db_status()

    if not candidate_name: # If no search term is provided on the search page itself
        search_performed = False
        return templates.TemplateResponse("results.html", {
            "request": request, "results_data": [], "search_performed": False,
            "error_message": "Please enter a candidate name to search.",
            "candidate_name_query": candidate_name
        })

    if not db_status["exists"]:
        error_message = f"Database file '{DB_FILENAME}' not found. Please ensure it is present in the application directory."
        return templates.TemplateResponse("results.html", {
            "request": request, "results_data": [], "search_performed": search_performed,
            "error_message": error_message, "candidate_name_query": candidate_name
        })

    conn = await get_db_connection()
    if not conn:
        error_message = "Could not connect to the database. The database file might be corrupted or inaccessible."
        return templates.TemplateResponse("results.html", {
            "request": request, "results_data": [], "search_performed": search_performed,
            "error_message": error_message, "candidate_name_query": candidate_name
        })

    try:
        cursor = await conn.cursor()
        roll_nos_of_matched_students = []

        if candidate_name: # This will always be true due to the check above, but good for structure
            # Step 1: Fetch all candidate names and roll numbers from DB
            await cursor.execute("SELECT roll_no, candidate_name FROM students")
            all_students_from_db = await cursor.fetchall() # List of Row objects

            if not all_students_from_db:
                error_message = "No student data found in the database to search against."
            else:
                potential_matches = []
                search_name_lower = candidate_name.lower()

                for student_row in all_students_from_db:
                    db_name_lower = student_row["candidate_name"].lower()
                    # fuzz.token_set_ratio is good for names: handles word order and common words.
                    # fuzz.partial_ratio could also be considered if substring matches are highly desired.
                    score = fuzz.token_set_ratio(search_name_lower, db_name_lower)
                    
                    if score >= SIMILARITY_THRESHOLD:
                        potential_matches.append({
                            "roll_no": student_row["roll_no"],
                            "name": student_row["candidate_name"], # Original name for display/debugging
                            "score": score
                        })
                
                # Sort matches by score in descending order
                potential_matches.sort(key=lambda x: x["score"], reverse=True)
                
                # Get the roll numbers of the top N distinct matches
                distinct_roll_nos_added = set()
                for match_info in potential_matches:
                    if len(roll_nos_of_matched_students) < MAX_FUZZY_RESULTS:
                        if match_info["roll_no"] not in distinct_roll_nos_added:
                             roll_nos_of_matched_students.append(match_info["roll_no"])
                             distinct_roll_nos_added.add(match_info["roll_no"])
                    else:
                        break # Reached max results limit
        
        # Step 2: Fetch full details for the matched student roll numbers
        for roll_no_to_fetch in roll_nos_of_matched_students:
            await cursor.execute(
                "SELECT roll_no, candidate_name, mother_name, father_name, school_name FROM students WHERE roll_no = ?",
                (roll_no_to_fetch,)
            )
            student_detail_row = await cursor.fetchone()
            if student_detail_row: # Should always be true if roll_no came from students table
                student_detail_dict = dict(student_detail_row)
                await cursor.execute(
                    "SELECT sub_code, sub_name, theory, practical, marks, positional_grade FROM results WHERE student_roll_no = ? ORDER BY sub_code",
                    (student_detail_dict["roll_no"],)
                )
                subject_rows = await cursor.fetchall()
                subjects_as_dicts = [dict(row) for row in subject_rows]
                
                percentages = calculate_student_percentages(subjects_as_dicts)
                
                results_data.append({
                    "details": student_detail_dict,
                    "subjects": subjects_as_dicts,
                    "percentages": percentages
                })
        # Note: The template will show "No students found matching your criteria" 
        # if results_data is empty after this process.

    except Exception as e:
        error_message = f"An error occurred during the search: {str(e)}. Please check server logs."
        print(f"Search error: {e}")
    finally:
        if conn:
            await conn.close()

    return templates.TemplateResponse("results.html", {
        "request": request,
        "results_data": results_data,
        "search_performed": search_performed,
        "error_message": error_message,
        "candidate_name_query": candidate_name # Pass the original query back for display
    })

# --- For local development ---
if __name__ == "__main__":
    import uvicorn
    if not DB_PATH.is_file():
        print(f"ERROR: Database file '{DB_FILENAME}' not found at '{DB_PATH}'.")
    else:
        print(f"Database file '{DB_FILENAME}' found at '{DB_PATH}'.")
        print("Ensure Tailwind CSS is built. For dev: 'npm run watch:css'.")
        print("Starting Uvicorn server. Access at http://127.0.0.1:8000")
        uvicorn.run(app, host="127.0.0.1", port=8000)