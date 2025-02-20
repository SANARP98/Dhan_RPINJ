from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from dhanhq import dhanhq
from openai import OpenAI
import os
import logging
import sqlite3
from contextlib import contextmanager
import datetime
import json
import re
from typing import Dict, List

# -----------------------------
# Configuration
# -----------------------------
class Settings(BaseSettings):
    openai_api_key: str = ""
    db_path: str = "options.db"
    default_quantity: int = 75
    modified_order_price: float = 0.2

    class Config:
        env_file = ".env"

settings = Settings()
load_dotenv()

if not settings.openai_api_key:
    raise RuntimeError("Missing OpenAI API key")

OPEN_ORDER_STATUSES = {"OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"}
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# In-memory storage for accounts (could be replaced with a DB)
accounts: Dict[str, dhanhq] = {}

class Database:
    def __init__(self, db_path):
        self.pool = sqlite3.connect(db_path, check_same_thread=False)

    @contextmanager
    def get_cursor(self):
        conn = self.pool
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

db = Database(settings.db_path)
client = OpenAI(api_key=settings.openai_api_key)

# -----------------------------
# Models
# -----------------------------
class TextInput(BaseModel):
    text: str

class TradingResponse(BaseModel):
    symbol: str
    date: str
    expiry: str
    Buy1: float
    Buy2: float
    SL1: int
    SL2: int
    Target1: int
    Target2: int

class ClosePositionRequest(BaseModel):
    account_id: str
    securityId: str
    netQty: int
    orderType: str
    price: float = 0.0

class AddAccountRequest(BaseModel):
    api_key: str
    password: str

# -----------------------------
# Helper Functions
# -----------------------------
def handle_error(message: str, status_code: int = 500):
    return {"error": message}, status_code

def determine_expiry(text: str) -> str:
    today = datetime.date.today()
    weekly_expiry = today + datetime.timedelta(days=(3 - today.weekday()) % 7)
    last_day = datetime.date(today.year + (today.month // 12), (today.month % 12) + 1, 1) - datetime.timedelta(days=1)
    days_to_thursday = (last_day.weekday() - 3) % 7
    monthly_expiry = last_day - datetime.timedelta(days=days_to_thursday)
    return weekly_expiry.strftime("%d/%m/%Y") if "weekly" in text.lower() else monthly_expiry.strftime("%Y-%m-%d")

def clean_gpt_response(response: str) -> str:
    return re.sub(r"```json\s*|\s*```", "", response).strip()

def generate_prompt(text: str, today_date: str, expiry_date: str) -> str:
    return f"""
    Extract structured trading information from the given text.
    **Input Text:** "{text}"
    **Expected Output JSON:**
    {{
        "symbol": "<Extracted Symbol>",
        "date": "{today_date}",
        "expiry": "{expiry_date}",
        "Buy1": <Lowest Buy Price>,
        "Buy2": <Second Lowest Buy Price>,
        "SL1": <Highest SL>,
        "SL2": <Next Highest SL>,
        "Target1": <First Closest Target>,
        "Target2": <Second Closest Target>
    }}
    Ensure the values are correctly extracted and formatted from the input text.
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

def search_options_in_db(symbol: str, expiry: str) -> dict:
    if not os.path.exists(settings.db_path):
        return {"error": "Database file not found"}

    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(options)")
        columns = {col[1] for col in cursor.fetchall()}
        if not {"SYMBOL_NAME", "SM_EXPIRY_DATE"}.issubset(columns):
            return {"error": "Required columns not found in database"}

        query = """
            SELECT * FROM options
            WHERE LOWER(TRIM(REPLACE(SYMBOL_NAME, '-', ''))) = LOWER(TRIM(REPLACE(?, '-', '')))
            AND DATE(SM_EXPIRY_DATE) = ?
        """
        cursor.execute(query, (symbol, expiry))
        results = cursor.fetchall()

    return {"data": results} if results else {"message": "No matching symbol+expiry found", "symbol": symbol, "expiry": expiry}

async def place_or_modify_order(dhan_client, security_id: str, account_id: str, buy_price: float) -> dict:
    try:
        if not security_id:
            return {account_id: {"error": "Security ID not provided"}}

        existing_orders = dhan_client.get_order_list().get("data", [])
        open_orders = [o for o in existing_orders if o.get("securityId") == security_id and o.get("orderStatus") in OPEN_ORDER_STATUSES]
        
        if open_orders:
            order_id = open_orders[0].get("orderId")
            modified_order = dhan_client.modify_order(
                order_id=order_id,
                order_type=dhan_client.LIMIT,
                leg_name="ENTRY_LEG",
                quantity=settings.default_quantity,
                price=settings.modified_order_price,
                trigger_price=0,
                disclosed_quantity=0,
                validity=dhan_client.DAY
            )
            return {account_id: {"message": "Order modified", "order": modified_order}}
        else:
            new_order = dhan_client.place_order(
                security_id=security_id,
                exchange_segment=dhan_client.NSE_FNO,
                transaction_type=dhan_client.BUY,
                quantity=settings.default_quantity,
                order_type=dhan_client.LIMIT,
                product_type=dhan_client.MARGIN,
                price=buy_price
            )
            return {account_id: {"message": "New order placed", "order": new_order}}
    except Exception as e:
        return {account_id: {"error": f"Order operation failed: {str(e)}"}}

# -----------------------------
# Endpoints
# -----------------------------
@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "accounts": list(accounts.keys())})

@app.post("/add-account", response_model=dict)
async def add_account(req: AddAccountRequest):
    account_id = req.api_key  # Using API key as unique identifier
    if account_id in accounts:
        return handle_error("Account already exists", 400)
    
    try:
        dhan_client = dhanhq(req.api_key, req.password)
        accounts[account_id] = dhan_client
        logging.info(f"Account {account_id} added successfully.")
        return {"message": f"Account {account_id} added successfully"}
    except Exception as e:
        return handle_error(f"Failed to add account: {str(e)}", 500)

last_structured_data = None

@app.post("/submit-text", response_model=dict)
async def submit_text(input_data: TextInput):
    global last_structured_data
    if not accounts:
        return handle_error("No accounts added", 400)

    today_date = datetime.datetime.today().strftime("%d/%m/%Y")
    expiry_date = determine_expiry(input_data.text)
    prompt = generate_prompt(input_data.text, today_date, expiry_date)
    gpt_response = await call_chatgpt(prompt)
    cleaned_response = clean_gpt_response(gpt_response)

    try:
        structured_data = TradingResponse(**json.loads(cleaned_response))
    except json.JSONDecodeError as e:
        return handle_error(f"Failed to parse GPT response: {str(e)}", 400)

    matching_options = search_options_in_db(structured_data.symbol, structured_data.expiry)
    if "error" in matching_options:
        return handle_error(matching_options["error"], 404)
    if "data" not in matching_options or not matching_options["data"]:
        return {"message": "No matching symbol+expiry in DB", "structured_data": structured_data.dict()}

    security_id = matching_options["data"][0][0]
    buy_price = structured_data.Buy1
    results = {"structured_data": structured_data.dict()}
    for account_id, client in accounts.items():
        results.update(await place_or_modify_order(client, security_id, account_id, buy_price))
    last_structured_data = structured_data
    return results

@app.post("/place-order", response_model=dict)
async def place_order():
    if not accounts:
        return handle_error("No accounts added", 400)

    buy_price = last_structured_data.Buy1 if last_structured_data else 0.1
    results = {}
    for account_id, client in accounts.items():
        security_id = None
        try:
            orders = client.get_order_list().get("data", [])
            pending_orders = [o for o in orders if o.get("orderStatus") == "PENDING"]
            if pending_orders:
                security_id = pending_orders[0].get("securityId")
        except Exception as e:
            results[account_id] = {"error": f"Failed to fetch pending orders: {str(e)}"}
            continue
        results.update(await place_or_modify_order(client, security_id, account_id, buy_price))
    return results

@app.get("/orders", response_model=dict)
async def get_orders():
    if not accounts:
        return handle_error("No accounts added", 400)

    results = {}
    for account_id, client in accounts.items():
        try:
            orders = client.get_order_list().get("data", [])
            results[account_id] = {"orders": orders}
        except Exception as e:
            results[account_id] = {"error": f"Fetching orders failed: {str(e)}"}
    return results

@app.get("/holdings", response_model=dict)
async def get_holdings():
    if not accounts:
        return handle_error("No accounts added", 400)

    results = {}
    for account_id, client in accounts.items():
        try:
            holdings = client.get_holdings().get("data", [])
            results[account_id] = {"holdings": holdings}
        except Exception as e:
            results[account_id] = {"error": f"Fetching holdings failed: {str(e)}"}
    return results

@app.get("/positions", response_model=dict)
async def get_positions():
    if not accounts:
        return handle_error("No accounts added", 400)

    results = {}
    for account_id, client in accounts.items():
        try:
            positions = client.get_positions().get("data", [])
            results[account_id] = {"positions": positions}
        except Exception as e:
            results[account_id] = {"error": f"Fetching positions failed: {str(e)}"}
    return results

@app.post("/cancel-all-orders", response_model=dict)
async def cancel_all_orders():
    if not accounts:
        return handle_error("No accounts added", 400)

    results = {account_id: {"canceled": [], "errors": []} for account_id in accounts}
    for account_id, client in accounts.items():
        try:
            orders = client.get_order_list().get("data", [])
            open_orders = [o for o in orders if o.get("orderStatus") in OPEN_ORDER_STATUSES]
            for order in open_orders:
                try:
                    order_id = order.get("orderId")
                    cancel_resp = client.cancel_order(order_id=order_id)
                    results[account_id]["canceled"].append({"orderId": order_id, "response": cancel_resp})
                except Exception as e:
                    results[account_id]["errors"].append(f"Order {order.get('orderId', 'unknown')}: {str(e)}")
        except Exception as e:
            results[account_id]["errors"].append(f"Fetching orders failed: {str(e)}")
    return results

@app.post("/close-position", response_model=dict)
async def close_position(req: ClosePositionRequest):
    if req.account_id not in accounts:
        return handle_error(f"Account {req.account_id} not found", 404)

    current_dhan = accounts[req.account_id]
    try:
        if req.netQty == 0:
            return handle_error("netQty=0, nothing to close", 400)

        transaction_type = current_dhan.SELL if req.netQty > 0 else current_dhan.BUY
        qty_abs = abs(req.netQty)
        order_type = current_dhan.MARKET if req.orderType.upper() == "MARKET" else current_dhan.LIMIT
        price = 0 if order_type == current_dhan.MARKET else req.price

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
        return handle_error(f"Failed to close position: {str(e)}", 500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)