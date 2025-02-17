from fastapi import FastAPI
from pydantic import BaseModel
import datetime
from openai import OpenAI

# client = OpenAI(api_key=OPENAI_API_KEY)
import os
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# Initialize FastAPI app
app = FastAPI()

# OpenAI API Key (Replace 'your-api-key' with an actual API key)
OPENAI_API_KEY = "your-api-key"

# Define request model
class TextInput(BaseModel):
    text: str

def get_expiry_date():
    """Find the nearest upcoming Thursday as expiry date."""
    today = datetime.date.today()
    days_until_thursday = (3 - today.weekday()) % 7  # 3 corresponds to Thursday
    expiry_date = today + datetime.timedelta(days=days_until_thursday)
    return expiry_date.strftime("%d/%m/%Y")

def generate_prompt(text):
    """Generate a structured prompt to extract the required trading details."""
    today_date = datetime.date.today().strftime("%d/%m/%Y")
    expiry_date = get_expiry_date()

    return f"""
    Extract structured trading information from the given text.

    **Input Text:** "{text}"

    **Expected Output JSON:**
    {{
        "symbol": "<Extracted Symbol>",  # Example: "NIFTY-Feb2025-23800-CE"
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

async def call_chatgpt(prompt):
    """Calls OpenAI API with the generated prompt."""
    response = client.chat.completions.create(model="gpt-4",
    messages=[{"role": "system", "content": "You are an expert in financial data extraction."},
              {"role": "user", "content": prompt}])
    return response.choices[0].message.content

@app.post("/submit-text")
async def submit_text(input_data: TextInput):
    """Processes the input text and returns structured JSON."""
    prompt = generate_prompt(input_data.text)
    processed_response = await call_chatgpt(prompt)

    return {"message": "Text processed successfully", "data": processed_response}
