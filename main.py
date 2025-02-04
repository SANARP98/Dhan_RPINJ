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

# Load environment variables
load_dotenv()
api_key = os.getenv("DHAN_API_KEY")
password = os.getenv("DHAN_PASSWORD")
# OpenAI API Key (Replace 'your-api-key' with an actual API key)

openai.api_key = os.getenv("OPENAI_API_KEY")
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

# 🏷️ Endpoint to receive text from UI
def get_expiry_date():
    """Find the nearest upcoming Thursday as expiry date."""
    today = datetime.date.today()
    print(today)
    days_until_thursday = (3 - today.weekday()) % 7  # 3 corresponds to Thursday
    expiry_date = today + datetime.timedelta(days=days_until_thursday)
    print(expiry_date)
    return expiry_date.strftime("%d/%m/%Y")

def generate_prompt(text):
    """Generate a structured prompt to extract the required trading details."""
    today_date = datetime.date.today().strftime("%d/%m/%Y")
    expiry_date = get_expiry_date()

    return f"""
    Extract structured trading information from the given text. Year should always be 2025

    **Input Text:** "{text}"

    **Expected Output JSON:**
    {{
        "symbol": "<Extracted Symbol>",  # Should be in this format only: NIFTY followed by which month and yyyy followed by the 5 digit price followed by ce or pe "NIFTY-Feb2025-23800-CE"
        "date": "{today_date}", #should be in dd/mm/yy format only (also based on month decision above)
        "expiry": "{expiry_date}", #similarly change month based on date above
        "Buy1": <Lowest Buy Price>,
        "Buy2": <Second Lowest Buy Price>,
        "SL1": <Highest SL>,
        "SL2": <Next Highest SL>,
        "Target1": <First Closest Target>,
        "Target2": <Second Closest Target>
    }}

    Ensure the values are correctly extracted and formatted from the input text.
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

@app.post("/submit-text")
async def submit_text(input_data: TextInput):
    """Processes the input text and returns structured JSON."""
    prompt = generate_prompt(input_data.text)
    processed_response = await call_chatgpt(prompt)
    print(processed_response)

    return {"message": "Text processed successfully", "data": processed_response}