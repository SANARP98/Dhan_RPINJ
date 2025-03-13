import os
import time
import math
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from kiteconnect import KiteConnect

load_dotenv()

# Load Zerodha credentials and redirect URI from environment variables
API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")
REDIRECT_URI = os.environ.get("KITE_REDIRECT_URI", "http://localhost:8000/login")

# Initialize KiteConnect with your API key and set the redirect URI
kite = KiteConnect(api_key=API_KEY)
kite.redirect_uri = REDIRECT_URI

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/")
async def root():
    """Redirects the user to Zerodha's login page."""
    login_url = kite.login_url()
    return RedirectResponse(url=login_url)

@app.get("/login")
async def login(request: Request):
    """
    Handles Zerodha's redirect after login.
    Expects a 'request_token' in the query params and generates an access token.
    """
    request_token = request.query_params.get("request_token")
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request token")
    try:
        session_data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = session_data["access_token"]
        kite.set_access_token(access_token)
        # Optionally, save the access token for later use.
        return RedirectResponse(url="/positions")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/positions", response_class=HTMLResponse)
async def positions(request: Request):
    """
    Fetches the current net positions and renders them in an HTML template.
    The template also includes a form to select which position to monitor.
    """
    try:
        positions_data = kite.positions()  # Expected dict with keys "net" and "day"
        net_positions = positions_data.get("net", [])
        return templates.TemplateResponse("positions2.html", {"request": request, "positions": net_positions})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def place_sell_order(tradingsymbol, quantity):
    """
    Places a market sell order for the given tradingsymbol and quantity.
    Adjust the parameters as needed (e.g., exchange, product type).
    """
    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange="NFO",
            tradingsymbol=tradingsymbol,
            transaction_type="SELL",
            quantity=quantity,
            order_type="MARKET",
            product="NRML"  # Change to "MIS" if required
        )
        print(f"Sell order placed successfully. Order ID: {order_id}")
    except Exception as e:
        print(f"Error placing sell order: {e}")

def monitor_position(position):
    """
    Monitors the provided position until the price moves to the target profit or stop loss level.
    When the condition is met, a sell order is placed instantly.
    
    Prices are adjusted to two decimal digits and snapped to a valid 0.05 increment.
    For target profit, we use a ceiling so that the order is placed when the price is at or above the next valid level.
    For stop loss, we use a floor so that it falls to the next lower valid level.
    """
    try:
        buy_price = float(position.get("average_price", 0))
    except Exception:
        print("Invalid buy price value.")
        return

    tradingsymbol = position.get("tradingsymbol")
    instrument_token = position.get("instrument_token")
    quantity = position.get("quantity")
    
    if buy_price == 0 or not instrument_token:
        print("Insufficient data to monitor the position.")
        return

    # Calculate target profit and stop loss based on a 1.0 increment,
    # then adjust to the nearest valid price in 0.05 increments.
    target_profit = math.ceil((buy_price + 1.0) / 0.05) * 0.05
    stop_loss = math.floor((buy_price - 1.0) / 0.05) * 0.05

    print(f"Monitoring position for {tradingsymbol}:")
    print(f"Buy Price: {buy_price:.2f}")
    print(f"Target Profit Price: {target_profit:.2f}")
    print(f"Stop Loss Price: {stop_loss:.2f}")

    # Poll at a faster interval for an "instant" reaction.
    while True:
        try:
            # kite.ltp expects a list of instrument tokens as strings.
            ltp_data = kite.ltp([str(instrument_token)])
            current_price = ltp_data[str(instrument_token)]["last_price"]
        except Exception as e:
            print(f"Error fetching LTP: {e}")
            time.sleep(1)
            continue

        print(f"Current Price of {tradingsymbol}: {current_price:.2f}")

        # Place the sell order instantly if condition met
        if current_price >= target_profit:
            print("Target profit reached. Initiating sell order immediately...")
            place_sell_order(tradingsymbol, quantity)
            break
        elif current_price <= stop_loss:
            print("Stop loss triggered. Initiating sell order immediately...")
            place_sell_order(tradingsymbol, quantity)
            break

        time.sleep(1)  # Reduced polling interval for near-instant reaction

@app.post("/manage", response_class=HTMLResponse)
async def manage(request: Request, background_tasks: BackgroundTasks, position_index: int = Form(...)):
    """
    Receives the selected position index from the HTML form and starts monitoring that position
    as a background task.
    """
    try:
        positions_data = kite.positions()
        net_positions = positions_data.get("net", [])
        if position_index < 0 or position_index >= len(net_positions):
            return HTMLResponse("Invalid position index selected.")
        selected_position = net_positions[position_index]
        background_tasks.add_task(monitor_position, selected_position)
        return HTMLResponse(f"Started monitoring position: {selected_position.get('tradingsymbol')}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
# Additional endpoint in your FastAPI app
@app.get("/orders", response_class=HTMLResponse)
async def orders(request: Request):
    """
    Fetches all orders placed on Zerodha and renders them in an HTML template.
    """
    try:
        orders_data = kite.orders()
        print(orders_data)
        return templates.TemplateResponse("orders.html", {"request": request, "orders": orders_data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("manage_positions:app", host="localhost", port=8000, reload=True)
