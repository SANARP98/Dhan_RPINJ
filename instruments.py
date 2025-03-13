import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from kiteconnect import KiteConnect
import requests

load_dotenv()

# Zerodha credentials from environment
API_KEY = os.environ.get("KITE_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET")
REDIRECT_URI = os.environ.get("KITE_REDIRECT_URI", "http://localhost:8000/login")

kite = KiteConnect(api_key=API_KEY)
kite.redirect_uri = REDIRECT_URI

app = FastAPI()

@app.get("/")
async def root():
    return RedirectResponse(url=kite.login_url())

@app.get("/login")
async def login(request: Request):
    request_token = request.query_params.get("request_token")
    if not request_token:
        raise HTTPException(status_code=400, detail="Missing request token")
    try:
        session_data = kite.generate_session(request_token, api_secret=API_SECRET)
        kite.set_access_token(session_data["access_token"])
        # Fetch instruments gzip for NSE
        instruments_url = kite.instruments(exchange="NSE")
        response = requests.get("https://api.kite.trade/instruments")
        
        if response.status_code == 200:
            with open("nse_instruments.csv.gz", "wb") as f:
                f.write(response.content)
            return {"status": "Instrument file downloaded successfully."}
        else:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch instruments")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("instruments:app", host="localhost", port=8000, reload=True)