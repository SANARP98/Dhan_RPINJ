from fastapi import FastAPI, HTTPException
import os
import logging
from dotenv import load_dotenv
from dhanhq import dhanhq
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

# Load environment variables
load_dotenv()
api_key = os.getenv("DHAN_API_KEY")
password = os.getenv("DHAN_PASSWORD")

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize FastAPI app
app = FastAPI()

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
