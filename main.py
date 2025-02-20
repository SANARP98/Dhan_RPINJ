from fastapi import FastAPI, HTTPException
import os
import logging
from dotenv import load_dotenv
from dhanhq import dhanhq
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel
from openai import OpenAI

import datetime
import sqlite3
import json
import re

# -----------------------------
# Load environment variables
# -----------------------------
load_dotenv()
api_key = os.getenv("DHAN_API_KEY")
password = os.getenv("DHAN_PASSWORD")
api_key2 = os.getenv("DHAN_API_KEY2")
password2 = os.getenv("DHAN_PASSWORD2")

DB_PATH = "options.db"

# Two separate security IDs for each account
SECURITY_ID_1 = None
SECURITY_ID_2 = None

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize FastAPI app
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialize Dhan API connection for both accounts
try:
    dhan = dhanhq(api_key, password)
    dhan2 = dhanhq(api_key2, password2)
    logging.info("Successfully connected to DhanHQ API for both accounts.")
except Exception as e:
    logging.error(f"Error initializing DhanHQ API: {str(e)}")
    dhan = None
    dhan2 = None

# -----------------------------
# Homepage (serves index.html)
# -----------------------------
@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# -----------------------------
# Models
# -----------------------------
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

# For closing positions
class ClosePositionRequest(BaseModel):
    account: int         # 1 or 2
    securityId: str      # e.g., "55568"
    netQty: int          # e.g., 75 (long) or -75 (short)
    orderType: str       # "MARKET" or "LIMIT"
    price: float = 0.0   # only used if orderType=LIMIT

# -----------------------------
# GPT Helper Logic (unchanged)
# -----------------------------
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def get_expiry_date():
    today = datetime.date.today()
    days_until_thursday = (3 - today.weekday()) % 7
    expiry_date = today + datetime.timedelta(days=days_until_thursday)
    return expiry_date.strftime("%d/%m/%Y")

def get_last_thursday_of_month():
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
    return re.sub(r"```json\s*|\s*```", "", response).strip()

def generate_prompt(text: str) -> str:
    today_date = datetime.datetime.today().strftime("%d/%m/%Y")
    weekly_expiry_date = get_expiry_date()
    monthly_expiry_date = get_last_thursday_of_month()

    return f"""
    Extract structured trading information from the given text. 
    datelogic = Year should always be 2025. If weekly is mentioned then print {weekly_expiry_date} in expiry, 
    if nothing is mentioned or monthly is mentioned then print {monthly_expiry_date}. 
    date should always be in dd/mm/yyyy format
    **Input Text:** "{text}"
    **Expected Output JSON:**
    {{
        "symbol": "<Extracted Symbol>",  
        "date": "{today_date}",
        "expiry": "datelogin",
        "Buy1": <Lowest Buy Price>,
        "Buy2": <Second Lowest Buy Price>,
        "SL1": <Highest SL>,
        "SL2": <Next Highest SL>,
        "Target1": <First Closest Target>,
        "Target2": <Second Closest Target>
    }}
    Ensure the values are correctly extracted and formatted from the input text. 
    Dont give any pre and post explanation. Just the output.
    """

async def call_chatgpt(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are an expert in financial data extraction."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content

def format_expiry_date(expiry: str) -> str:
    try:
        return datetime.datetime.strptime(expiry, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return expiry

def search_options_in_db(symbol: str, expiry: str) -> dict:
    if not os.path.exists(DB_PATH):
        logging.error("Database file not found!")
        return {"error": "Database file not found"}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(options)")
    columns = [col[1] for col in cursor.fetchall()]

    if "SYMBOL_NAME" not in columns or "SM_EXPIRY_DATE" not in columns:
        conn.close()
        return {"error": "Required columns not found in database"}

    symbol_column = "SYMBOL_NAME"
    expiry_column = "SM_EXPIRY_DATE"

    formatted_expiry = format_expiry_date(expiry)
    query_symbol = f"""
        SELECT * FROM options
        WHERE LOWER(TRIM(REPLACE({symbol_column}, '-', ''))) = LOWER(TRIM(REPLACE(?, '-', '')))
    """
    cursor.execute(query_symbol, (symbol,))
    symbol_results = cursor.fetchall()

    if not symbol_results:
        conn.close()
        return {"message": "Symbol not found", "symbol": symbol}

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
            "all_matching_rows": symbol_results
        }

# -----------------------------
# 1) /submit-text
# -----------------------------
@app.post("/submit-text")
async def submit_text(input_data: TextInput):
    global SECURITY_ID_1, SECURITY_ID_2

    if not dhan or not dhan2:
        raise HTTPException(status_code=500, detail="One or both DhanHQ APIs not initialized.")

    prompt = generate_prompt(input_data.text)
    gpt_raw_response = await call_chatgpt(prompt)
    cleaned_response = clean_gpt_response(gpt_raw_response)

    try:
        structured_data = json.loads(cleaned_response)
    except json.JSONDecodeError as e:
        return {"error": "Failed to parse GPT response", "details": str(e)}

    symbol = structured_data.get("symbol", "").strip()
    expiry = structured_data.get("expiry", "").strip()

    matching_options = search_options_in_db(symbol, expiry)
    if "error" in matching_options:
        raise HTTPException(status_code=404, detail=f"Database error: {matching_options['error']}")

    if "data" not in matching_options or not matching_options["data"]:
        return {
            "message": "Cannot place order because no exact symbol+expiry match in DB.",
            "structured_data": structured_data,
            "db_search_result": matching_options
        }

    first_match = matching_options["data"][0]
    SECURITY_ID_1 = first_match[0]
    SECURITY_ID_2 = first_match[0]

    results = {
        "account1": None,
        "account2": None,
        "structured_data": structured_data
    }

    # Account 1 place/modify logic
    try:
        existing_orders_response_1 = dhan.get_order_list()
        all_orders_1 = existing_orders_response_1.get("data", [])
        open_orders_for_security_1 = [
            o for o in all_orders_1
            if o.get("securityId") == SECURITY_ID_1
            and o.get("orderStatus") in ["OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"]
        ]
        if open_orders_for_security_1:
            order_to_modify_1 = open_orders_for_security_1[0]
            order_id_1 = order_to_modify_1.get("orderId")
            modified_order_1 = dhan.modify_order(
                order_id=order_id_1,
                order_type=dhan.LIMIT,
                leg_name="ENTRY_LEG",
                quantity=75,
                price=0.2,
                trigger_price=0,
                disclosed_quantity=0,
                validity=dhan.DAY
            )
            results["account1"] = {
                "message": "Found existing open order. Modified it instead of placing new.",
                "order": modified_order_1
            }
        else:
            order_1 = dhan.place_order(
                security_id=SECURITY_ID_1,
                exchange_segment=dhan.NSE_FNO,
                transaction_type=dhan.BUY,
                quantity=75,
                order_type=dhan.LIMIT,
                product_type=dhan.MARGIN,
                price=0.1
            )
            results["account1"] = {
                "message": "No existing open order found; placed a new order.",
                "order": order_1
            }
    except Exception as e:
        results["account1"] = {"error": f"Order placement/modification failed: {str(e)}"}

    # Account 2 place/modify logic
    try:
        existing_orders_response_2 = dhan2.get_order_list()
        all_orders_2 = existing_orders_response_2.get("data", [])
        open_orders_for_security_2 = [
            o for o in all_orders_2
            if o.get("securityId") == SECURITY_ID_2
            and o.get("orderStatus") in ["OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"]
        ]
        if open_orders_for_security_2:
            order_to_modify_2 = open_orders_for_security_2[0]
            order_id_2 = order_to_modify_2.get("orderId")
            modified_order_2 = dhan2.modify_order(
                order_id=order_id_2,
                order_type=dhan2.LIMIT,
                leg_name="ENTRY_LEG",
                quantity=75,
                price=0.2,
                trigger_price=0,
                disclosed_quantity=0,
                validity=dhan2.DAY
            )
            results["account2"] = {
                "message": "Found existing open order. Modified it instead of placing new.",
                "order": modified_order_2
            }
        else:
            order_2 = dhan2.place_order(
                security_id=SECURITY_ID_2,
                exchange_segment=dhan2.NSE_FNO,
                transaction_type=dhan2.BUY,
                quantity=75,
                order_type=dhan2.LIMIT,
                product_type=dhan2.MARGIN,
                price=0.1
            )
            results["account2"] = {
                "message": "No existing open order found; placed a new order.",
                "order": order_2
            }
    except Exception as e:
        results["account2"] = {"error": f"Order placement/modification failed: {str(e)}"}

    return results

# -----------------------------
# 2) /place-order
# -----------------------------
@app.post("/place-order")
async def place_order():
    global SECURITY_ID_1, SECURITY_ID_2

    if not dhan or not dhan2:
        raise HTTPException(status_code=500, detail="One or both DhanHQ APIs not initialized.")

    results = {"account1": None, "account2": None}

    # Account 1
    try:
        existing_orders_response_1 = dhan.get_order_list()
        all_orders_1 = existing_orders_response_1.get("data", [])

        pending_orders_1 = [o for o in all_orders_1 if o.get("orderStatus") == "PENDING"]
        if pending_orders_1:
            SECURITY_ID_1 = pending_orders_1[0].get("securityId")

        open_orders_for_security_1 = [
            o for o in all_orders_1
            if o.get("securityId") == SECURITY_ID_1
            and o.get("orderStatus") in ["OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"]
        ]
        if open_orders_for_security_1:
            order_to_modify_1 = open_orders_for_security_1[0]
            order_id_1 = order_to_modify_1.get("orderId")
            modified_order_1 = dhan.modify_order(
                order_id=order_id_1,
                order_type=dhan.LIMIT,
                leg_name="ENTRY_LEG",
                quantity=75,
                price=0.2,
                trigger_price=0,
                disclosed_quantity=0,
                validity=dhan.DAY
            )
            results["account1"] = {
                "message": "Order modified instead of placing a new one.",
                "modified_order": modified_order_1
            }
        else:
            order_1 = dhan.place_order(
                security_id=SECURITY_ID_1,
                exchange_segment=dhan.NSE_FNO,
                transaction_type=dhan.BUY,
                quantity=75,
                order_type=dhan.LIMIT,
                product_type=dhan.MARGIN,
                price=0.1
            )
            results["account1"] = {
                "message": "No open order found; new order placed successfully.",
                "order": order_1
            }
    except Exception as e:
        results["account1"] = {"error": f"[Account1] Order placement/modification failed: {str(e)}"}

    # Account 2
    try:
        existing_orders_response_2 = dhan2.get_order_list()
        all_orders_2 = existing_orders_response_2.get("data", [])

        pending_orders_2 = [o for o in all_orders_2 if o.get("orderStatus") == "PENDING"]
        if pending_orders_2:
            SECURITY_ID_2 = pending_orders_2[0].get("securityId")

        open_orders_for_security_2 = [
            o for o in all_orders_2
            if o.get("securityId") == SECURITY_ID_2
            and o.get("orderStatus") in ["OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"]
        ]
        if open_orders_for_security_2:
            order_to_modify_2 = open_orders_for_security_2[0]
            order_id_2 = order_to_modify_2.get("orderId")
            modified_order_2 = dhan2.modify_order(
                order_id=order_id_2,
                order_type=dhan2.LIMIT,
                leg_name="ENTRY_LEG",
                quantity=75,
                price=0.2,
                trigger_price=0,
                disclosed_quantity=0,
                validity=dhan2.DAY
            )
            results["account2"] = {
                "message": "Order modified instead of placing a new one.",
                "modified_order": modified_order_2
            }
        else:
            order_2 = dhan2.place_order(
                security_id=SECURITY_ID_2,
                exchange_segment=dhan2.NSE_FNO,
                transaction_type=dhan2.BUY,
                quantity=75,
                order_type=dhan2.LIMIT,
                product_type=dhan2.MARGIN,
                price=0.1
            )
            results["account2"] = {
                "message": "No open order found; new order placed successfully.",
                "order": order_2
            }
    except Exception as e:
        results["account2"] = {"error": f"[Account2] Order placement/modification failed: {str(e)}"}

    return results

# -----------------------------
# 3) /orders (fetch for both)
# -----------------------------
@app.get("/orders")
async def get_orders():
    if not dhan or not dhan2:
        raise HTTPException(status_code=500, detail="One or both DhanHQ APIs not initialized.")

    results = {"account1": None, "account2": None}

    # Account 1
    try:
        response_1 = dhan.get_order_list()
        orders_1 = response_1.get("data", [])
        results["account1"] = {"orders": orders_1}
    except Exception as e:
        results["account1"] = {"error": f"Fetching orders failed: {str(e)}"}

    # Account 2
    try:
        response_2 = dhan2.get_order_list()
        orders_2 = response_2.get("data", [])
        results["account2"] = {"orders": orders_2}
    except Exception as e:
        results["account2"] = {"error": f"Fetching orders failed: {str(e)}"}

    return results

# -----------------------------
# 4) /holdings (fetch for both)
# -----------------------------
@app.get("/holdings")
async def get_holdings():
    if not dhan or not dhan2:
        raise HTTPException(status_code=500, detail="One or both DhanHQ APIs not initialized.")

    results = {"account1": None, "account2": None}

    # Account 1
    try:
        response_1 = dhan.get_holdings()
        holdings_1 = response_1.get("data", [])
        results["account1"] = {"holdings": holdings_1}
    except Exception as e:
        results["account1"] = {"error": f"Fetching holdings failed: {str(e)}"}

    # Account 2
    try:
        response_2 = dhan2.get_holdings()
        holdings_2 = response_2.get("data", [])
        results["account2"] = {"holdings": holdings_2}
    except Exception as e:
        results["account2"] = {"error": f"Fetching holdings failed: {str(e)}"}

    return results

# -----------------------------
# 5) /positions (fetch for both)
# -----------------------------
@app.get("/positions")
async def get_positions():
    if not dhan or not dhan2:
        raise HTTPException(status_code=500, detail="One or both DhanHQ APIs not initialized.")

    results = {"account1": None, "account2": None}

    # Account 1
    try:
        response_1 = dhan.get_positions()
        logging.info(response_1)
        positions_1 = response_1.get("data", [])
        results["account1"] = {"positions": positions_1}
    except Exception as e:
        results["account1"] = {"error": f"Fetching positions failed: {str(e)}"}

    # Account 2
    try:
        response_2 = dhan2.get_positions()
        logging.info(response_2)
        positions_2 = response_2.get("data", [])
        results["account2"] = {"positions": positions_2}
    except Exception as e:
        results["account2"] = {"error": f"Fetching positions failed: {str(e)}"}

    return results

# -----------------------------
# 6) /cancel-all-orders
# -----------------------------
@app.post("/cancel-all-orders")
async def cancel_all_orders():
    if not dhan or not dhan2:
        raise HTTPException(status_code=500, detail="One or both DhanHQ APIs not initialized.")

    results = {
        "account1": {"canceled": [], "errors": []},
        "account2": {"canceled": [], "errors": []}
    }

    # Account 1
    try:
        response_1 = dhan.get_order_list()
        all_orders_1 = response_1.get("data", [])
        open_orders_1 = [
            o for o in all_orders_1
            if o.get("orderStatus") in ["OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"]
        ]
        for order in open_orders_1:
            try:
                order_id = order.get("orderId")
                if order_id:
                    cancel_resp = dhan.cancel_order(order_id=order_id)
                    results["account1"]["canceled"].append({"orderId": order_id, "response": cancel_resp})
            except Exception as e:
                results["account1"]["errors"].append(str(e))
    except Exception as e:
        results["account1"]["errors"].append(f"Error fetching or canceling orders: {str(e)}")

    # Account 2
    try:
        response_2 = dhan2.get_order_list()
        all_orders_2 = response_2.get("data", [])
        open_orders_2 = [
            o for o in all_orders_2
            if o.get("orderStatus") in ["OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"]
        ]
        for order in open_orders_2:
            try:
                order_id = order.get("orderId")
                if order_id:
                    cancel_resp = dhan2.cancel_order(order_id=order_id)
                    results["account2"]["canceled"].append({"orderId": order_id, "response": cancel_resp})
            except Exception as e:
                results["account2"]["errors"].append(str(e))
    except Exception as e:
        results["account2"]["errors"].append(f"Error fetching or canceling orders: {str(e)}")

    return results

# -----------------------------
# 7) /close-position
# -----------------------------
@app.post("/close-position")
async def close_position(req: ClosePositionRequest):
    """
    Closes the position for the specified securityId and netQty on either account 1 or 2.
    If netQty > 0 => SELL
    If netQty < 0 => BUY
    Uses MARKET or LIMIT as requested.
    """
    if not dhan or not dhan2:
        raise HTTPException(status_code=500, detail="One or both DhanHQ APIs not initialized.")

    # Determine which Dhan account to use
    if req.account == 1:
        current_dhan = dhan
    elif req.account == 2:
        current_dhan = dhan2
    else:
        raise HTTPException(status_code=400, detail="Invalid account number (must be 1 or 2)")

    try:
        # netQty > 0 => we SELL to close
        # netQty < 0 => we BUY to close
        if req.netQty == 0:
            raise ValueError("netQty=0, nothing to close")

        transaction_type = current_dhan.SELL if req.netQty > 0 else current_dhan.BUY
        qty_abs = abs(req.netQty)

        # Decide order type
        if req.orderType.upper() == "MARKET":
            order_type = current_dhan.MARKET
            price = 0
        else:
            order_type = current_dhan.LIMIT
            price = req.price

        # Place the order
        result = current_dhan.place_order(
            security_id=req.securityId,
            exchange_segment=current_dhan.NSE_FNO,
            transaction_type=transaction_type,
            quantity=qty_abs,
            order_type=order_type,
            product_type=current_dhan.MARGIN,
            price=price
        )

        return {"message": "Position close order placed", "orderResponse": result}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
