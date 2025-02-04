from fastapi import FastAPI, HTTPException
import os
import logging
from dotenv import load_dotenv
from dhanhq import dhanhq
from fastapi.responses import HTMLResponse

# Load environment variables
load_dotenv()
api_key = os.getenv("DHAN_API_KEY")
password = os.getenv("DHAN_PASSWORD")

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize FastAPI app
app = FastAPI()

# Initialize API connection
try:
    dhan = dhanhq(api_key, password)
    logging.info("Successfully connected to DhanHQ API")
except Exception as e:
    logging.error(f"Error initializing DhanHQ API: {str(e)}")
    dhan = None  # Prevents the app from crashing if API initialization fails

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

# 🏷️ Basic UI for placing orders and fetching them
@app.get("/", response_class=HTMLResponse)
async def homepage():
    return """
    <html>
        <head>
            <title>DhanHQ Trading Dashboard</title>
            <style>
                body { font-family: Arial, sans-serif; text-align: center; background-color: #f4f4f4; }
                h1 { color: #333; }
                button { padding: 10px 15px; font-size: 16px; margin: 10px; cursor: pointer; }
                table { width: 90%; margin: 20px auto; border-collapse: collapse; background: white; }
                th, td { padding: 10px; border: 1px solid #ddd; text-align: center; }
                th { background: #007bff; color: white; }
                tr:nth-child(even) { background: #f9f9f9; }
            </style>
            <script>
                async function placeOrder() {
                    try {
                        let response = await fetch('/place-order', { method: 'POST' });
                        let data = await response.json();
                        if (response.ok) {
                            alert("Order placed successfully: " + JSON.stringify(data, null, 2));
                        } else {
                            alert("Error: " + data.detail);
                        }
                    } catch (error) {
                        alert("Network error while placing order.");
                    }
                }

                async function fetchOrders() {
                    try {
                        let response = await fetch('/orders');
                        let data = await response.json();
                        let orders = data.orders;
                        let tableHTML = "<table><tr><th>Order ID</th><th>Status</th><th>Symbol</th><th>Quantity</th><th>Price</th><th>Time</th></tr>";

                        orders.forEach(order => {
                            tableHTML += `<tr>
                                <td>${order.orderId}</td>
                                <td>${order.orderStatus}</td>
                                <td>${order.tradingSymbol}</td>
                                <td>${order.quantity}</td>
                                <td>${order.price}</td>
                                <td>${order.createTime}</td>
                            </tr>`;
                        });

                        tableHTML += "</table>";
                        document.getElementById("orders").innerHTML = tableHTML;
                    } catch (error) {
                        alert("Error fetching orders.");
                    }
                }
            </script>
        </head>
        <body>
            <h1>DhanHQ Trading Dashboard</h1>
            <button onclick="placeOrder()">Place Order</button>
            <button onclick="fetchOrders()">Fetch Orders</button>
            <div id="orders"></div>
        </body>
    </html>
    """

