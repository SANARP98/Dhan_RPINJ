from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from dotenv import load_dotenv, set_key
import os
import logging
import sqlite3
from contextlib import contextmanager
import datetime
import json
import re
from typing import Dict
from dhanhq import dhanhq
from openai import AsyncOpenAI  # Changed to AsyncOpenAI for async support

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
        extra = "ignore"

settings = Settings()
load_dotenv()

if not settings.openai_api_key:
    raise RuntimeError("Missing OpenAI API key")

OPEN_ORDER_STATUSES = {"OPEN", "TRANSIT", "PARTIALLY_FILLED", "PENDING"}
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# In-memory storage for accounts
accounts: Dict[str, dhanhq] = {}

# Load existing accounts from .env on startup
def load_accounts_from_env():
    i = 1
    while True:
        api_key = os.getenv(f"DHAN_API_KEY{i}" if i > 1 else "DHAN_API_KEY")
        password = os.getenv(f"DHAN_PASSWORD{i}" if i > 1 else "DHAN_PASSWORD")
        if not api_key or not password:
            break
        try:
            client = dhanhq(api_key, password)
            accounts[api_key] = client
            logging.info(f"Loaded account {api_key} from .env")
        except Exception as e:
            logging.error(f"Failed to load account {api_key}: {str(e)}")
        i += 1

load_accounts_from_env()

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
client = AsyncOpenAI(api_key=settings.openai_api_key)  # Use AsyncOpenAI

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
def handle_error(message: str, status_code: int = 500) -> dict:
    return {"error": message, "status_code": status_code}

def determine_expiry(text: str) -> str:
    today = datetime.date.today()
    weekly_expiry = today + datetime.timedelta(days=(3 - today.weekday()) % 7)
    last_day = datetime.date(today.year + (today.month // 12), (today.month % 12) + 1, 1) - datetime.timedelta(days=1)
    days_to_thursday = (last_day.weekday() - 3) % 7
    monthly_expiry = last_day - datetime.timedelta(days=days_to_thursday)
    return weekly_expiry.strftime("%d/%m/%Y") if "weekly" in text.lower() else monthly_expiry.strftime("%Y-%m-%d")

def clean_gpt_response(response: str) -> str:
    cleaned = re.sub(r"```json\s*|\s*```", "", response).strip()
    logging.info(f"Cleaned GPT response: {cleaned}")
    return cleaned

def generate_prompt(text: str, today_date: str, expiry_date: str) -> str:
    return f"""
    Extract structured trading information from the given text. 
    datelogic = Year should always be 2025. If weekly is mentioned then print {expiry_date} in expiry, if nothing is mentioned or monthly is mentioned then print {expiry_date}. date should always be in dd/mm/yyyy format

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

async def call_chatgpt(prompt: str) -> str:
    try:
        response = await client.chat.completions.create(  # Properly awaited with AsyncOpenAI
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert in financial data extraction."},
                {"role": "user", "content": prompt}
            ]
        )
        raw_response = response.choices[0].message.content
        logging.info(f"Raw GPT response: {raw_response}")
        return raw_response
    except Exception as e:
        logging.error(f"Error calling GPT: {str(e)}")
        return ""

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

def update_env_file(api_key: str, password: str):
    env_file = ".env"
    existing_keys = {k for k in os.environ.keys() if k.startswith("DHAN_API_KEY")}
    max_index = max([int(k.replace("DHAN_API_KEY", "")) for k in existing_keys if k != "DHAN_API_KEY"] or [0]) + 1 if len(existing_keys) > 1 else 1
    
    key_name = f"DHAN_API_KEY{max_index}" if max_index > 1 or "DHAN_API_KEY" in os.environ else "DHAN_API_KEY"
    password_name = f"DHAN_PASSWORD{max_index}" if max_index > 1 or "DHAN_PASSWORD" in os.environ else "DHAN_PASSWORD"
    
    set_key(env_file, key_name, api_key)
    set_key(env_file, password_name, password)
    os.environ[key_name] = api_key
    os.environ[password_name] = password
    logging.info(f"Updated .env with {key_name} and {password_name}")

# -----------------------------
# Endpoints
# -----------------------------
@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "accounts": list(accounts.keys())})

@app.post("/add-account", response_model=dict)
async def add_account(req: AddAccountRequest):
    account_id = req.api_key
    if account_id in accounts:
        raise HTTPException(status_code=400, detail=handle_error("Account already exists"))
    
    try:
        dhan_client = dhanhq(req.api_key, req.password)
        accounts[account_id] = dhan_client
        update_env_file(req.api_key, req.password)
        logging.info(f"Account {account_id} added successfully.")
        return {"message": f"Account {account_id} added successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=handle_error(f"Failed to add account: {str(e)}"))

last_structured_data = None

@app.post("/submit-text", response_model=dict)
async def submit_text(input_data: TextInput):
    global last_structured_data
    if not accounts:
        raise HTTPException(status_code=400, detail=handle_error("No accounts added"))

    today_date = datetime.datetime.today().strftime("%d/%m/%Y")
    expiry_date = determine_expiry(input_data.text)
    prompt = generate_prompt(input_data.text, today_date, expiry_date)
    gpt_response = await call_chatgpt(prompt)
    cleaned_response = clean_gpt_response(gpt_response)

    if not cleaned_response:
        raise HTTPException(status_code=400, detail=handle_error("GPT returned an empty response"))

    try:
        structured_data = TradingResponse(**json.loads(cleaned_response))
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse GPT response: {cleaned_response}")
        raise HTTPException(status_code=400, detail=handle_error(f"Failed to parse GPT response: {str(e)}"))

    matching_options = search_options_in_db(structured_data.symbol, structured_data.expiry)
    if "error" in matching_options:
        raise HTTPException(status_code=404, detail=handle_error(matching_options["error"]))
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
        raise HTTPException(status_code=400, detail=handle_error("No accounts added"))

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
        raise HTTPException(status_code=400, detail=handle_error("No accounts added"))

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
        raise HTTPException(status_code=400, detail=handle_error("No accounts added"))

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
        raise HTTPException(status_code=400, detail=handle_error("No accounts added"))

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
        raise HTTPException(status_code=400, detail=handle_error("No accounts added"))

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
        raise HTTPException(status_code=404, detail=handle_error(f"Account {req.account_id} not found"))

    current_dhan = accounts[req.account_id]
    try:
        if req.netQty == 0:
            raise HTTPException(status_code=400, detail=handle_error("netQty=0, nothing to close"))

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
        raise HTTPException(status_code=500, detail=handle_error(f"Failed to close position: {str(e)}"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)