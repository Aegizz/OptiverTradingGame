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

# New function to determine trade volume based on both forecast and momentum
def determine_trade_volume(forecast, momentum, position, position_limit):
    # Define thresholds for momentum and forecast
    STRONG_MOMENTUM_THRESHOLD = 10
    MEDIUM_MOMENTUM_THRESHOLD = 5
    
    # Calculate base trade signal from forecast
    forecast_signal = 1 if forecast > 0 else (-1 if forecast < 0 else 0)
    
    # Calculate momentum signal strength
    momentum_strength = 0
    if abs(momentum) > STRONG_MOMENTUM_THRESHOLD:
        momentum_strength = 2
    elif abs(momentum) > MEDIUM_MOMENTUM_THRESHOLD:
        momentum_strength = 1
    
    # Determine momentum direction
    momentum_signal = 1 if momentum > 0 else (-1 if momentum < 0 else 0)
    
    # If forecast and momentum agree, trade more aggressively
    if forecast_signal * momentum_signal > 0:
        # Signals agree, amplify the trade
        base_volume = forecast_signal * (1 + momentum_strength)
    elif momentum_strength > 1 and momentum_signal != 0:
        # Strong momentum disagrees with forecast, follow momentum
        base_volume = momentum_signal
    else:
        # Either weak disagreement or no strong signals, follow forecast but cautiously
        base_volume = forecast_signal
    
    # Scale to available trade size (1, 2, or 3)
    if base_volume > 0:
        # Buy case: check if we can buy more based on position limit
        max_buy = position_limit - position
        trade_volume = min(abs(base_volume), max_buy)
    elif base_volume < 0:
        # Sell case: check if we can sell more based on position limit
        max_sell = position + position_limit
        trade_volume = -min(abs(base_volume), max_sell)
    else:
        trade_volume = 0
    
    # Ensure we're always trading in whole numbers
    return int(trade_volume)

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
                    state_data = response_data["data"]
                    forecast = state_data.get("price_forecast", 0)
                    momentum = state_data.get("momentum", 0)
                    position = state_data.get("position", 0)
                    position_limit = state_data.get("position_limit", 3)
                    current_price = state_data.get("price", 0)
                    
                    print(f"Current state: Price=${current_price}, Forecast={forecast:.2f}, Momentum={momentum:.2f}, Position={position}/{position_limit}")
                    
                    # Use the new function to determine trade volume
                    trade_volume = determine_trade_volume(forecast, momentum, position, position_limit)

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
                    else:
                        print("No trade action taken")
                        
                elif response_data.get("event") == "end" and "data" in response_data:
                    print("Game over!")
                    print(f"Final PnL: ${response_data['data'].get('pnl', 0)}")
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