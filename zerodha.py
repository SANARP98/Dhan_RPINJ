import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
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

# Set up the Jinja2 templates (place your HTML files in a folder named "templates")
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
        positions_data = kite.positions()  # Returns a dict with keys like "net" and "day"
        # Render the positions.html template, passing in the positions data
        return templates.TemplateResponse("positions.html", {"request": request, "positions": positions_data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("zerodha:app", host="localhost", port=8000, reload=True)
