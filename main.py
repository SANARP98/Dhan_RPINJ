from fastapi import FastAPI, HTTPException
import os
import logging
from dotenv import load_dotenv
from dhanhq import dhanhq
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
import datetime
import sqlite3
import json
import re


# Load environment variables
load_dotenv()
api_key = os.getenv("DHAN_API_KEY")
password = os.getenv("DHAN_PASSWORD")

DB_PATH = "options.db"
SECURITY_ID = None  # or a default value, e.g. "44903"


# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize FastAPI app
app = FastAPI()

# Global variable to store submitted text
submitted_text = ""

# Templates
templates = Jinja2Templates(directory="templates")

# Initialize Dhan API connection
try:
    dhan = dhanhq(api_key, password)
    logging.info("Successfully connected to DhanHQ API")
except Exception as e:
    logging.error(f"Error initializing DhanHQ API: {str(e)}")
    dhan = None  # Prevents app crash if API initialization fails

# -------------------------------------------------------------------
# 1) Simple Homepage route
# -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# -------------------------------------------------------------------
# 2) Place Order (modified logic)
# -------------------------------------------------------------------
@app.post("/place-order")
async def place_order():
    global SECURITY_ID  # So we can reassign it

    if not dhan:
        raise HTTPException(status_code=500, detail="DhanHQ API not initialized")

    try:
        # 1) Fetch current orders
        existing_orders_response = dhan.get_order_list()
        all_orders = existing_orders_response.get("data", [])

        # --- A) Get the securityId from the FIRST pending order and store it in SECURITY_ID ---
        pending_orders = [o for o in all_orders if o.get("orderStatus") == "PENDING"]
        if pending_orders:
            SECURITY_ID = pending_orders[0].get("securityId")  # First PENDING order's security ID
            logging.info(f"Setting SECURITY_ID to the first pending order: {SECURITY_ID}")
        else:
            logging.info("No pending orders found to extract a securityId from.")

        # --- B) Now do your existing logic that checks if there's an open/active order for SECURITY_ID ---
        open_orders_for_security = [
            o for o in all_orders
            if o.get("securityId") == SECURITY_ID
            and o.get("orderStatus") in ["OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"]
        ]

        if open_orders_for_security:
            order_to_modify = open_orders_for_security[0]
            order_id = order_to_modify.get("orderId")

            modified_order = dhan.modify_order(
                order_id=order_id,
                order_type=dhan.LIMIT,
                leg_name="ENTRY_LEG", 
                quantity=75,
                price=0.2,
                trigger_price=0,
                disclosed_quantity=0,
                validity=dhan.DAY
            )
            logging.info(f"Order modified: {modified_order}")
            return {
                "message": "Order modified instead of placing a new one.",
                "modified_order": modified_order
            }
        else:
            order = dhan.place_order(
                security_id=SECURITY_ID,
                exchange_segment=dhan.NSE_FNO,
                transaction_type=dhan.BUY,
                quantity=75,
                order_type=dhan.LIMIT,
                product_type=dhan.MARGIN,
                price=0.1
            )
            logging.info(f"Order placed: {order}")
            return {
                "message": "No open order found; new order placed successfully.",
                "order": order
            }

    except Exception as e:
        logging.error(f"Order placement/modification failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Order placement/modification failed: {str(e)}")

# -------------------------------------------------------------------
# 3) Get Orders
# -------------------------------------------------------------------
@app.get("/orders")
async def get_orders():
    if not dhan:
        raise HTTPException(status_code=500, detail="DhanHQ API not initialized")

    try:
        response = dhan.get_order_list()
        orders = response.get("data", [])
        logging.info(f"Fetched {len(orders)} orders")
        return {"orders": orders}
    except Exception as e:
        logging.error(f"Fetching orders failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Fetching orders failed: {str(e)}")

# -------------------------------------------------------------------
# 4) Get Holdings
# -------------------------------------------------------------------
@app.get("/holdings")
async def get_holdings():
    if not dhan:
        raise HTTPException(status_code=500, detail="DhanHQ API not initialized")

    try:
        response = dhan.get_holdings()
        holdings = response.get("data", [])
        logging.info(f"Fetched {len(holdings)} holdings")
        return {"holdings": holdings}
    except Exception as e:
        logging.error(f"Fetching holdings failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Fetching holdings failed: {str(e)}")

# -------------------------------------------------------------------
# 5) Models
# -------------------------------------------------------------------
class TextInput(BaseModel):
    text: str

class TradingResponse(BaseModel):
    symbol: str
    date: str
    expiry: str
    Buy1: int
    Buy2: int
    SL1: int
    SL2: int
    Target1: int
    Target2: int

# -------------------------------------------------------------------
# 6) Helpers for Expiry Date and GPT
# -------------------------------------------------------------------
def get_expiry_date():
    """Find the nearest upcoming Thursday as expiry date (dd/mm/yyyy)."""
    today = datetime.date.today()
    days_until_thursday = (3 - today.weekday()) % 7  # 3 = Thursday
    expiry_date = today + datetime.timedelta(days=days_until_thursday)
    return expiry_date.strftime("%d/%m/%Y")

def get_last_thursday_of_month():
    """Find the last Thursday of the current month (dd/mm/yyyy)."""
    today = datetime.date.today()
    year = today.year
    month = today.month

    next_month = (month % 12) + 1
    year_adjusted = year + (1 if next_month == 1 else 0)
    first_day_next_month = datetime.date(year_adjusted, next_month, 1)
    last_day_this_month = first_day_next_month - datetime.timedelta(days=1)

    days_to_thursday = (last_day_this_month.weekday() - 3) % 7
    last_thursday = last_day_this_month - datetime.timedelta(days=days_to_thursday)
    return last_thursday.strftime("%d/%m/%Y")

def clean_gpt_response(response: str) -> str:
    """Removes triple-backtick Markdown formatting from GPT response."""
    return re.sub(r"```json\s*|\s*```", "", response).strip()

def generate_prompt(text: str) -> str:
    """Generate a structured prompt for GPT to extract the required trading details."""
    today_date = datetime.datetime.today().strftime("%d/%m/%Y")
    weekly_expiry_date = get_expiry_date()
    monthly_expiry_date = get_last_thursday_of_month()

    return f"""
    Extract structured trading information from the given text. 
    datelogic = Year should always be 2025. If weekly is mentioned then print {weekly_expiry_date} in expiry, if nothing is mentioned or monthly is mentioned then print {monthly_expiry_date}. date should always be in dd/mm/yyyy format
    **Input Text:** "{text}"
    **Expected Output JSON:**
    {{
        "symbol": "<Extracted Symbol>",  # Should be in this format only: NIFTY followed by which month and yyyy followed by the 5 digit price followed by ce or pe "NIFTY-Feb2025-23800-CE"
        "date": "{today_date}",
        "expiry": "datelogin",
        "Buy1": <Lowest Buy Price>,
        "Buy2": <Second Lowest Buy Price>,
        "SL1": <Highest SL>,
        "SL2": <Next Highest SL>,
        "Target1": <First Closest Target>,
        "Target2": <Second Closest Target>
    }}
    Ensure the values are correctly extracted and formatted from the input text. Dont give any pre and post explanation. Just the output. 
    """

async def call_chatgpt(prompt: str) -> str:
    """Call OpenAI ChatCompletion API with the prompt and return the response text."""
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are an expert in financial data extraction."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content

def format_expiry_date(expiry: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD for SQL date comparison."""
    try:
        return datetime.datetime.strptime(expiry, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return expiry

def search_options_in_db(symbol: str, expiry: str) -> dict:
    """
    1) Checks if symbol exists in DB
    2) Prints matching rows
    3) Checks if expiry matches
    """
    if not os.path.exists(DB_PATH):
        logging.error("Database file not found in the current directory!")
        return {"error": "Database file not found"}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get column names
    cursor.execute("PRAGMA table_info(options)")
    columns = [col[1] for col in cursor.fetchall()]
    logging.info(f"Database Columns: {columns}")

    # Basic validation
    if "SYMBOL_NAME" not in columns or "SM_EXPIRY_DATE" not in columns:
        conn.close()
        return {"error": "Required columns not found in database"}

    symbol_column = "SYMBOL_NAME"
    expiry_column = "SM_EXPIRY_DATE"

    # Format the expiry to match DB's YYYY-MM-DD
    formatted_expiry = format_expiry_date(expiry)
    logging.info(f"Checking Expiry: Given='{expiry}', Formatted='{formatted_expiry}'")

    # Step 1: Find all rows matching symbol (case-insensitive, ignoring hyphens)
    query_symbol = f"""
        SELECT * FROM options
        WHERE LOWER(TRIM(REPLACE({symbol_column}, '-', ''))) = LOWER(TRIM(REPLACE(?, '-', '')))
    """
    cursor.execute(query_symbol, (symbol,))
    symbol_results = cursor.fetchall()

    if not symbol_results:
        conn.close()
        return {"message": "Symbol not found", "symbol": symbol}

    logging.info(f"Found {len(symbol_results)} rows for symbol: {symbol}")

    # Step 2: Check if that symbol has matching expiry
    query_expiry = f"""
        SELECT * FROM options
        WHERE LOWER(TRIM(REPLACE({symbol_column}, '-', ''))) = LOWER(TRIM(REPLACE(?, '-', '')))
        AND DATE({expiry_column}) = ?
    """
    cursor.execute(query_expiry, (symbol, formatted_expiry))
    expiry_results = cursor.fetchall()
    conn.close()

    if expiry_results:
        return {
            "message": "Symbol and expiry found",
            "data": expiry_results  # List of tuples
        }
    else:
        return {
            "message": "Symbol found, but no matching expiry",
            "symbol": symbol,
            "expiry": formatted_expiry,
            "all_matching_rows": symbol_results
        }

# -------------------------------------------------------------------
# 7) Main Endpoint to submit text and place an order (modified logic)
# -------------------------------------------------------------------
@app.post("/submit-text")
async def submit_text(input_data: TextInput):
    """
    Updated logic:
    1) Generate GPT prompt and parse GPT response.
    2) Search DB for symbol/expiry.
    3) If found, BEFORE placing a new order, check if an open order for same securityId exists.
       - If open, modify that order instead of placing a new one.
       - Else, place a new order.
    """
    if not dhan:
        raise HTTPException(status_code=500, detail="DhanHQ API not initialized")

    # --- Step 1: Prompt GPT ---
    prompt = generate_prompt(input_data.text)
    gpt_raw_response = await call_chatgpt(prompt)
    logging.info(f"GPT Raw Response:\n{gpt_raw_response}")

    # Clean the GPT response
    cleaned_response = clean_gpt_response(gpt_raw_response)

    # --- Step 2: Parse the JSON from GPT ---
    try:
        structured_data = json.loads(cleaned_response)
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse GPT response: {str(e)}")
        return {"error": "Failed to parse GPT response", "details": str(e)}

    # Extract relevant fields
    symbol = structured_data.get("symbol", "").strip()
    expiry = structured_data.get("expiry", "").strip()
    logging.info(f"Parsed Symbol: {symbol}, Expiry: {expiry}")

    # --- Step 3: DB Search for the extracted symbol/expiry ---
    matching_options = search_options_in_db(symbol, expiry)
    logging.info(f"Matching Options: {matching_options}")

    # If there's an error or no data returned, do NOT place an order.
    if "error" in matching_options:
        raise HTTPException(
            status_code=404,
            detail=f"Database error: {matching_options['error']}"
        )

    if "data" not in matching_options or not matching_options["data"]:
        return {
            "message": "Cannot place order because no exact symbol+expiry match in DB.",
            "structured_data": structured_data,
            "db_search_result": matching_options
        }

    # We have valid data, so let's attempt to place/modify the order
    first_match = matching_options["data"][0]
    global SECURITY_ID
    SECURITY_ID = first_match[0]


    try:
        # -- Check for open orders for this security_id
        existing_orders_response = dhan.get_order_list()
        all_orders = existing_orders_response.get("data", [])

        open_orders_for_security = [
            o for o in all_orders
            if o.get("securityId") == SECURITY_ID
            and o.get("orderStatus") in ["OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"]
        ]

        if open_orders_for_security:
            # Modify the first open order
            order_to_modify = open_orders_for_security[0]
            order_id = order_to_modify.get("orderId")

            modified_order = dhan.modify_order(
                order_id=order_id,
                order_type=dhan.LIMIT,
                leg_name="ENTRY_LEG", 
                quantity=75,           # You can adjust quantity as needed
                price=0.2,            # Example new price
                trigger_price=0, 
                disclosed_quantity=0, 
                validity=dhan.DAY
            )
            logging.info(f"Order modified: {modified_order}")

            return {
                "message": "Found existing open order. Modified that order instead of placing new.",
                "symbol": symbol,
                "expiry": expiry,
                "order": modified_order,
                "structured_data": structured_data
            }
        else:
            # Place a new order
            order = dhan.place_order(
                security_id=SECURITY_ID,
                exchange_segment=dhan.NSE_FNO,
                transaction_type=dhan.BUY,
                quantity=75,          # Make sure this is correct lot size
                order_type=dhan.LIMIT,
                product_type=dhan.MARGIN,
                price=0.1             # Hard-coded example
            )
            logging.info(f"Order placed: {order}")

            return {
                "message": "No existing open order found; placed a new order.",
                "symbol": symbol,
                "expiry": expiry,
                "order": order,
                "structured_data": structured_data
            }

    except Exception as e:
        logging.error(f"Order placement/modification failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Order placement/modification failed: {str(e)}")
