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
import openai
import datetime
import sqlite3
import json
import re
import datetime 

# Load environment variables
load_dotenv()
api_key = os.getenv("DHAN_API_KEY")
password = os.getenv("DHAN_PASSWORD")
# OpenAI API Key (Replace 'your-api-key' with an actual API key)

openai.api_key = os.getenv("OPENAI_API_KEY")
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DB_PATH = "options.db"

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize FastAPI app
app = FastAPI()

# Global variable to store submitted text
submitted_text = ""
# Serve templates
templates = Jinja2Templates(directory="templates")

# Serve static files (if needed later for CSS, JS, images)
# app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize API connection
try:
    dhan = dhanhq(api_key, password)
    logging.info("Successfully connected to DhanHQ API")
except Exception as e:
    logging.error(f"Error initializing DhanHQ API: {str(e)}")
    dhan = None  # Prevents app crash if API initialization fails

# 🏷️ Serve HTML UI
@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# 🏷️ API to place an order
@app.post("/place-order")
async def place_order():
    if not dhan:
        raise HTTPException(status_code=500, detail="DhanHQ API not initialized")

    try:
        order = dhan.place_order(
            security_id="44903",
            exchange_segment=dhan.NSE_FNO,
            transaction_type=dhan.BUY,
            quantity=75,
            order_type=dhan.LIMIT,
            product_type=dhan.MARGIN,
            price=0.1
        )
        logging.info(f"Order placed: {order}")
        return {"message": "Order placed successfully", "order": order}
    except Exception as e:
        logging.error(f"Order placement failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Order placement failed: {str(e)}")

# 🏷️ API to fetch active orders (excluding cancelled)
@app.get("/orders")
async def get_orders():
    if not dhan:
        raise HTTPException(status_code=500, detail="DhanHQ API not initialized")

    try:
        response = dhan.get_order_list()
        orders = response.get("data", [])
        print(orders)
        logging.info(f"Fetched {len(orders)} orders")
        return {"orders": orders}
    except Exception as e:
        logging.error(f"Fetching orders failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Fetching orders failed: {str(e)}")

# 🏷️ API to fetch holdings
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

# Define a Pydantic model for text submission
class TextInput(BaseModel):
    text: str

# Define response model for processing
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

# 🏷️ Endpoint to receive text from UI
def get_expiry_date():
    """Find the nearest upcoming Thursday as expiry date."""
    today = datetime.date.today()
    print(today)
    days_until_thursday = (3 - today.weekday()) % 7  # 3 corresponds to Thursday
    expiry_date = today + datetime.timedelta(days=days_until_thursday)
    print(expiry_date)
    return expiry_date.strftime("%d/%m/%Y")

def get_last_thursday_of_month():
    """Find the last Thursday of the current month."""
    today = datetime.date.today()
    year = today.year
    month = today.month
    
    # Find the last day of the month
    next_month = month % 12 + 1
    year_adjusted = year + (1 if next_month == 1 else 0)
    first_day_next_month = datetime.date(year_adjusted, next_month, 1)
    last_day_this_month = first_day_next_month - datetime.timedelta(days=1)
    
    # Find the last Thursday
    days_to_thursday = (last_day_this_month.weekday() - 3) % 7
    last_thursday = last_day_this_month - datetime.timedelta(days=days_to_thursday)
    
    print(f"Last Thursday of the month: {last_thursday}")
    return last_thursday.strftime("%d/%m/%Y")
def clean_gpt_response(response):
    """Removes markdown formatting (```json ... ```) from GPT response."""
    return re.sub(r"```json\s*|\s*```", "", response).strip()  # Remove backticks and json keyword


def generate_prompt(text):
    """Generate a structured prompt to extract the required trading details."""
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
        "date": "{today_date}", #should be in dd/mm/yy format only (also based on month decision above) (this is todays date as given in prompt input)
        "expiry": "datelogin", #similarly change month based on date above. This should be either weekly expiry date mentioned in prompt or monthly expiry date mentioned in prompt.
        "Buy1": <Lowest Buy Price>,
        "Buy2": <Second Lowest Buy Price>,
        "SL1": <Highest SL>,
        "SL2": <Next Highest SL>,
        "Target1": <First Closest Target>,
        "Target2": <Second Closest Target>
    }}

    Ensure the values are correctly extracted and formatted from the input text. Dont give any pre and post explanation. Just the output. 
    """

async def call_chatgpt(prompt):
    """Calls OpenAI API using the new OpenAI v1.0 API."""
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",  # Use "gpt-4" if needed
        messages=[
            {"role": "system", "content": "You are an expert in financial data extraction."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content

def search_options_in_db(symbol: str, expiry: str):

    """Checks if a symbol exists in DB, prints all rows, and then verifies expiry match."""
    if not os.path.exists(DB_PATH):
        print("Error: options.db not found in the current directory!")
        return {"error": "Database file not found"}

    # Connect to the SQLite database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get column names dynamically
    cursor.execute("PRAGMA table_info(options)")
    columns = [col[1] for col in cursor.fetchall()]
    print(f"Database Columns: {columns}")  # Debugging

    if "SYMBOL_NAME" not in columns or "SM_EXPIRY_DATE" not in columns:
        print("Error: Required columns not found in the database.")
        conn.close()
        return {"error": "Required columns not found in database"}

    # Define column names explicitly
    symbol_column = "SYMBOL_NAME"
    expiry_column = "SM_EXPIRY_DATE"

    # Ensure correct format for expiry
    formatted_expiry = format_expiry_date(expiry)
    print(f"Checking Expiry: Given='{expiry}', Formatted='{formatted_expiry}'\n")

    # ✅ Step 1: Find all rows with the SYMBOL
    query_symbol = f"""
        SELECT * FROM options
        WHERE LOWER(TRIM(REPLACE({symbol_column}, '-', ''))) = LOWER(TRIM(REPLACE(?, '-', '')))
    """
    cursor.execute(query_symbol, (symbol,))
    symbol_results = cursor.fetchall()

    if not symbol_results:
        conn.close()
        return {"message": "Symbol not found", "symbol": symbol}

    print("\n🔹 All Rows Matching Symbol:")
    for row in symbol_results:
        print(row)  # Print all rows where the symbol exists

    # ✅ Step 2: Check if EXPIRY exists for the given SYMBOL (Ignoring Time)
    query_expiry = f"""
        SELECT * FROM options
        WHERE LOWER(TRIM(REPLACE({symbol_column}, '-', ''))) = LOWER(TRIM(REPLACE(?, '-', '')))
        AND DATE({expiry_column}) = ?
    """
    cursor.execute(query_expiry, (symbol, formatted_expiry))
    expiry_results = cursor.fetchall()
    conn.close()

    if expiry_results:
        return {"message": "Symbol and expiry found", "data": expiry_results}
    else:
        return {
            "message": "Symbol found, but no matching expiry",
            "symbol": symbol,
            "expiry": formatted_expiry,
            "all_matching_rows": symbol_results  # Show all rows with the symbol
        }


def format_expiry_date(expiry: str):
    """Convert DD/MM/YYYY to YYYY-MM-DD for SQL comparison."""
    try:
        return datetime.datetime.strptime(expiry, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return expiry

@app.post("/submit-text")
async def submit_text(input_data: TextInput):
    """Processes input text, extracts trading data, and searches options database automatically."""
    prompt = generate_prompt(input_data.text)
    processed_response = await call_chatgpt(prompt)

    print(f"Raw GPT Response:\n{processed_response}")  # Debugging

    # ✅ Remove backticks before parsing
    cleaned_response = clean_gpt_response(processed_response)
    
    # Convert response into structured dictionary
    try:
        structured_data = json.loads(cleaned_response)  # ✅ Now safe to parse
    except json.JSONDecodeError as e:
        print("Failed parsing GPT response")
        return {"error": "Failed to parse GPT response", "details": str(e)}

    # Extract required fields
    symbol = structured_data.get("symbol", "").strip()
    expiry = structured_data.get("expiry", "").strip()

    print(f"Extracted Symbol: {symbol}")
    print(f"Extracted Expiry: {expiry}")

    # Search in database
    matching_options = search_options_in_db(symbol, expiry)
    print(matching_options)

    if not dhan:
        raise HTTPException(status_code=500, detail="DhanHQ API not initialized")

    try:
        order = dhan.place_order(
            security_id=matching_options["data"][0][0],
            exchange_segment=dhan.NSE_FNO,
            transaction_type=dhan.BUY,
            quantity=75,
            order_type=dhan.LIMIT,
            product_type=dhan.MARGIN,
            price=0.1
        )
        logging.info(f"Order placed: {order}")
        return {"message": "Order placed successfully", "order": order}
    except Exception as e:
        logging.error(f"Order placement failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Order placement failed: {str(e)}")


    return {
        "message": "Text processed and options searched successfully",
        "data": structured_data,
        "options": matching_options if matching_options else "No matching options found"
    }