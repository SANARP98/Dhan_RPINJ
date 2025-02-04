import sqlite3
import os
from datetime import datetime

# Database file path
DB_FILENAME = "options.db"
DB_PATH = os.path.join(os.getcwd(), DB_FILENAME)

# Search values
symbol = "NIFTY-Feb2025-23800-CE"
expiry = "06/02/2025"  # Ensure format matches DB format

def format_expiry_date(expiry: str):
    """Convert DD/MM/YYYY to YYYY-MM-DD for SQL comparison."""
    try:
        return datetime.strptime(expiry, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return expiry  # Return as-is if already correct

def search_symbol_and_expiry(symbol: str, expiry: str):
    """Checks if a symbol exists in DB, prints all rows, and then verifies expiry match."""
    if not os.path.exists(DB_PATH):
        print("Error: options.db not found in the current directory!")
        return {"error": "Database file not found"}

    # Connect to the SQLite database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get column names dynamically
    cursor.execute("PRAGMA table_info(options)")
    columns = [col[1] for col in cursor.fetchall()]
    print(f"Database Columns: {columns}")  # Debugging

    if "SYMBOL_NAME" not in columns or "SM_EXPIRY_DATE" not in columns:
        print("Error: Required columns not found in the database.")
        conn.close()
        return {"error": "Required columns not found in database"}

    # Define column names explicitly
    symbol_column = "SYMBOL_NAME"
    expiry_column = "SM_EXPIRY_DATE"

    # Ensure correct format for expiry
    formatted_expiry = format_expiry_date(expiry)
    print(f"Checking Expiry: Given='{expiry}', Formatted='{formatted_expiry}'\n")

    # ✅ Step 1: Find all rows with the SYMBOL
    query_symbol = f"""
        SELECT * FROM options
        WHERE LOWER(TRIM(REPLACE({symbol_column}, '-', ''))) = LOWER(TRIM(REPLACE(?, '-', '')))
    """
    cursor.execute(query_symbol, (symbol,))
    symbol_results = cursor.fetchall()

    if not symbol_results:
        conn.close()
        return {"message": "Symbol not found", "symbol": symbol}

    print("\n🔹 All Rows Matching Symbol:")
    for row in symbol_results:
        print(row)  # Print all rows where the symbol exists

    # ✅ Step 2: Check if EXPIRY exists for the given SYMBOL (Ignoring Time)
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
            "all_matching_rows": symbol_results  # Show all rows with the symbol
        }

# Run the search
result = search_symbol_and_expiry(symbol, expiry)

# Print results
print("\nSearch Result:")
print(result)
