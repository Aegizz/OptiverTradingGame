import asyncio
import websockets
import json

URL = "wss://vega-apac.optibook.net/ws/e65ed16e-1042-4aac-8327-e6f972d120d5"
PLAYER_ID = "50cc97f7-e061-519e-862d-25c882cab50b"

connection_message = {
    "event": "connection",
    "player_id": "",
    "data": {
        "alias": "Aegizz",
        "player_id": PLAYER_ID,
        "token": ""
    }
}

start_message = {
    "event": "start",
    "player_id": "",
    "data": {
        "player_id": PLAYER_ID
    }
}

skip_message = {
    "event": "skip",
    "player_id": "",
    "data": {}
}

# This function will handle the puzzle impact and adjust trading accordingly
def handle_puzzle_impact(puzzle_data):
    impact = puzzle_data.get("impact", 0)
    if impact > 0:
        print(f"The stock will increase by ${impact}.")
        return impact  # Indicating stock will increase (buy)
    elif impact < 0:
        print(f"The stock will decrease by ${abs(impact)}.")
        return impact  # Indicating stock will decrease (sell)
    return 0  # No trade if no clear impact

async def connect():
    async with websockets.connect(URL) as websocket:
        print("Connected to WebSocket")
        await websocket.send(json.dumps(connection_message))
        print("Sent connection message")

        while True:
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5)
                print(f"Received: {response}")
                response_data = json.loads(response)

                if response_data.get("event") == "connection" and "data" in response_data:
                    if response_data["data"].get("player_id") == PLAYER_ID:
                        print("Connection established, sending start event...")
                        await websocket.send(json.dumps(start_message))
                        print("Sent start message")

                elif response_data.get("event") == "state" and "data" in response_data:
                    forecast = response_data["data"].get("price_forecast", 0)
                    position = response_data["data"].get("position", 0)
                    position_limit = response_data["data"].get("position_limit", 3)

                    if forecast > 0 and position < position_limit:
                        trade_volume = 3
                    elif forecast < 0 and position > -position_limit:
                        trade_volume = -3
                    else:
                        trade_volume = 0

                    if trade_volume != 0:
                        trade_message = {
                            "event": "trade",
                            "player_id": PLAYER_ID,
                            "data": {
                                "volume": trade_volume
                            }
                        }
                        await websocket.send(json.dumps(trade_message))
                        print(f"Sent trade: {'BUY' if trade_volume > 0 else 'SELL'} {abs(trade_volume)}")
                elif response_data.get("event") == "end" and "data" in response_data:
                    print("Game over!")
                    break  # Exit the loop when the game ends

                # Handling the puzzle event
                elif response_data.get("event") == "puzzle" and "data" in response_data:
                    puzzle_data = response_data["data"]
                    puzzle_impact = handle_puzzle_impact(puzzle_data)

                    # If puzzle impact is positive (stock increases), buy more stock
                    if puzzle_impact > 0:
                        trade_message = {
                            "event": "trade",
                            "player_id": PLAYER_ID,
                            "data": {
                                "volume": 3  # Buy more stock
                            }
                        }
                        await websocket.send(json.dumps(trade_message))
                        print("Sent trade: BUY 3")

                    # If puzzle impact is negative (stock decreases), sell stock
                    elif puzzle_impact < 0:
                        trade_message = {
                            "event": "trade",
                            "player_id": PLAYER_ID,
                            "data": {
                                "volume": -3  # Sell stock
                            }
                        }
                        await websocket.send(json.dumps(trade_message))
                        print("Sent trade: SELL 3")

                    # After the trade, send the skip message to move to the next puzzle/event
                    await websocket.send(json.dumps(skip_message))
                    print("Sent skip message")

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error: {e}")
                break


def main():
    asyncio.run(connect())

if __name__ == "__main__":
    main()
