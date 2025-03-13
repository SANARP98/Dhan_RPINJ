import os
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
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

# Set up the Jinja2 templates (HTML files are in the "templates" folder)
templates = Jinja2Templates(directory="templates")

@app.get("/")
async def root():
    # Redirect the user to Zerodha's login page
    login_url = kite.login_url()
    return RedirectResponse(url=login_url)

@app.get("/login")
async def login(request: Request):
    # Zerodha will redirect back to this endpoint with a request token as a query parameter
    request_token = request.query_params.get("request_token")
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request token")
    try:
        # Generate a session and extract the access token using the request token and API secret
        session_data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = session_data["access_token"]

        # Set the access token in the KiteConnect instance for subsequent API calls
        kite.set_access_token(access_token)

        # Redirect to the /positions endpoint after login
        return RedirectResponse(url="/positions")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/positions", response_class=HTMLResponse)
async def positions(request: Request):
    try:
        # Retrieve positions data from the Kite Connect API
        positions_data = kite.positions()  # Returns a dict with keys "net" and "day"
        # Render the positions.html template, passing in the positions data
        return templates.TemplateResponse("positions.html", {"request": request, "positions": positions_data})
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
        return templates.TemplateResponse("orders.html", {"request": request, "orders": orders_data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sell")
async def sell_order(symbol: str, quantity: int):
    """
    Place a sell order for a given symbol and quantity.
    The symbol should be passed without the exchange prefix (e.g., "NIFTY2532023000CE").
    This endpoint first verifies the instrument using both market quotes and the instruments endpoint,
    checking for valid pricing and expiry.
    """
    try:
        # Construct the instrument identifier required for market quotes.
        instrument_identifier = f"NFO:{symbol}"
        
        # Fetch the latest market quote for the instrument.
        quote = kite.ltp([instrument_identifier])
        if instrument_identifier not in quote:
            return JSONResponse(
                content={"error": f"Instrument {instrument_identifier} not found in market quotes."},
                status_code=404
            )
        market_data = quote[instrument_identifier]
        if market_data.get("last_price", 0) <= 0:
            return JSONResponse(
                content={"error": f"Instrument {instrument_identifier} appears invalid or expired."},
                status_code=400
            )
        
        # Fetch instrument metadata from the instruments endpoint.
        instruments = kite.instruments("NFO")
        # Filter for the instrument with the matching trading symbol.
        instrument_details = next((inst for inst in instruments if inst["tradingsymbol"] == symbol), None)
        if not instrument_details:
            return JSONResponse(
                content={"error": f"Instrument details for {symbol} not found."},
                status_code=404
            )
        
        # If the instrument details include an expiry date, check if it has already expired.
        expiry_str = instrument_details.get("expiry")
        if expiry_str:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            if expiry_date < datetime.today().date():
                return JSONResponse(
                    content={"error": f"Instrument {symbol} has expired on {expiry_date}."},
                    status_code=400
                )
        
        # If all validations pass, place a sell order.
        order_response = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange="NFO",
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=quantity,
            product=kite.PRODUCT_NRML,
            order_type=kite.ORDER_TYPE_MARKET,
            validity=kite.VALIDITY_DAY
        )
        return JSONResponse(content={"message": "Sell order placed", "order_id": order_response})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("manage_positions2:app", host="localhost", port=8000, reload=True)
