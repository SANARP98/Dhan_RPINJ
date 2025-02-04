from fastapi import FastAPI, HTTPException
import os
import json
from dhanhq import dhanhq
from dotenv import load_dotenv
from fastapi.responses import HTMLResponse

# Load environment variables
load_dotenv()
api_key = os.getenv("DHAN_API_KEY")
password = os.getenv("DHAN_PASSWORD")

# Initialize API connection
dhan = dhanhq(api_key, password)

# Initialize FastAPI app
app = FastAPI()

# 🏷️ API to place an order
@app.post("/place-order")
async def place_order():
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
        return {"message": "Order placed successfully", "order": order}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 🏷️ API to fetch active orders (excluding cancelled)
@app.get("/orders")
async def get_orders():
    try:
        response = dhan.get_order_list()
        orders = [order for order in response.get("data", []) ]
        return {"orders": orders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
                    let response = await fetch('/place-order', { method: 'POST' });
                    let data = await response.json();
                    alert("Order placed: " + JSON.stringify(data, null, 2));
                }

                async function fetchOrders() {
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
