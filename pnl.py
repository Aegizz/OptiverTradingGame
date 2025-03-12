import asyncio
import websockets
import json
import random
import numpy as np
from collections import deque
import time
import statistics

# Configuration
URL = "wss://vega-apac.optibook.net/ws/e65ed16e-1042-4aac-8327-e6f972d120d5"
PLAYER_ID = "50cc97f7-e061-519e-862d-25c882cab50b"
NUM_CONNECTIONS = 20  # Number of parallel connections to maintain
HISTORY_SIZE = 20  # Size of history window for strategy optimization

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

# Shared state for all connections
class SharedState:
    def __init__(self):
        self.strategy_params = {
            "momentum_weight": 0.6,
            "forecast_weight": 0.4,
            "strong_momentum_threshold": 10,
            "medium_momentum_threshold": 5,
            "aggressive_factor": 1.5
        }
        self.trade_history = deque(maxlen=HISTORY_SIZE)
        self.performance_history = deque(maxlen=HISTORY_SIZE)
        self.lock = asyncio.Lock()
        self.last_optimization = time.time()
        self.optimization_interval = 30  # Seconds between strategy optimizations
        
        # For each connection, track its performance
        self.connection_performance = {}

shared_state = SharedState()

# Handle puzzle impact
def handle_puzzle_impact(puzzle_data):
    impact = puzzle_data.get("impact", 0)
    if impact > 0:
        print(f"The stock will increase by ${impact}.")
        return impact  # Indicating stock will increase (buy)
    elif impact < 0:
        print(f"The stock will decrease by ${abs(impact)}.")
        return impact  # Indicating stock will decrease (sell)
    return 0  # No trade if no clear impact

# Dynamic trade volume determination with adaptive parameters
def determine_trade_volume(forecast, momentum, position, position_limit, conn_id, params):
    # Extract parameters
    momentum_weight = params["momentum_weight"]
    forecast_weight = params["forecast_weight"]
    strong_momentum_threshold = params["strong_momentum_threshold"]
    medium_momentum_threshold = params["medium_momentum_threshold"]
    aggressive_factor = params["aggressive_factor"]
    
    # Calculate weighted signal
    momentum_signal = np.tanh(momentum / 10)  # Normalize momentum
    forecast_signal = np.tanh(forecast * 2)    # Normalize forecast
    
    # Weighted combination of signals
    combined_signal = (momentum_signal * momentum_weight) + (forecast_signal * forecast_weight)
    
    # Scale signal to a trading volume between -3 and 3
    raw_volume = combined_signal * 3 * aggressive_factor
    
    # Apply position limits
    if raw_volume > 0:
        max_buy = position_limit - position
        trade_volume = min(max(0, round(raw_volume)), max_buy)
    elif raw_volume < 0:
        max_sell = position + position_limit
        trade_volume = max(-max_sell, min(0, round(raw_volume)))
    else:
        trade_volume = 0
    
    # Record for strategy optimization
    signal_data = {
        "conn_id": conn_id,
        "timestamp": time.time(),
        "momentum": momentum,
        "forecast": forecast,
        "combined_signal": combined_signal,
        "trade_volume": trade_volume,
        "position": position
    }
    shared_state.trade_history.append(signal_data)
    
    return trade_volume

# Adjust strategy parameters based on performance
async def optimize_strategy():
    async with shared_state.lock:
        # Only optimize if enough time has passed and we have data
        current_time = time.time()
        if (current_time - shared_state.last_optimization < shared_state.optimization_interval or
                len(shared_state.performance_history) < 5):
            return
        
        shared_state.last_optimization = current_time
        
        # If we have performance data, use it to optimize
        if shared_state.performance_history:
            # Extract the most recent PnL changes
            recent_performances = list(shared_state.performance_history)
            
            # Calculate average profit per trade
            avg_profit = statistics.mean([p.get("pnl_change", 0) for p in recent_performances])
            
            # If our strategy is working well, be more aggressive
            if avg_profit > 5:
                # Successful strategy - adjust weights to favor what's working
                momentum_correlations = []
                forecast_correlations = []
                
                for p in recent_performances:
                    if p.get("pnl_change", 0) > 0 and p.get("trade_volume", 0) != 0:
                        # This was a profitable trade - look at what signals were stronger
                        if abs(p.get("momentum", 0)) > abs(p.get("forecast", 0)):
                            momentum_correlations.append(1)
                            forecast_correlations.append(0.5)
                        else:
                            momentum_correlations.append(0.5)
                            forecast_correlations.append(1)
                
                # If we have correlation data, adjust weights
                if momentum_correlations and forecast_correlations:
                    avg_momentum_corr = statistics.mean(momentum_correlations)
                    avg_forecast_corr = statistics.mean(forecast_correlations)
                    total = avg_momentum_corr + avg_forecast_corr
                    
                    # Update weights based on correlation
                    shared_state.strategy_params["momentum_weight"] = avg_momentum_corr / total
                    shared_state.strategy_params["forecast_weight"] = avg_forecast_corr / total
                    shared_state.strategy_params["aggressive_factor"] = min(2.0, 
                                                                        shared_state.strategy_params["aggressive_factor"] + 0.1)
            elif avg_profit < -5:
                # Strategy is losing money - be more conservative and reset weights
                shared_state.strategy_params["momentum_weight"] = 0.5
                shared_state.strategy_params["forecast_weight"] = 0.5
                shared_state.strategy_params["aggressive_factor"] = max(1.0, 
                                                                    shared_state.strategy_params["aggressive_factor"] - 0.2)
            
            print(f"Optimized strategy parameters: {shared_state.strategy_params}")

# Handle a single connection
async def handle_connection(conn_id):
    print(f"Starting connection {conn_id}")
    
    # Initialize this connection's performance tracking
    if conn_id not in shared_state.connection_performance:
        shared_state.connection_performance[conn_id] = {
            "last_pnl": 0,
            "trades_made": 0,
            "successful_trades": 0
        }
    
    try:
        async with websockets.connect(URL) as websocket:
            print(f"Connection {conn_id}: Connected to WebSocket")
            
            # Customize the connection message for this connection
            conn_message = json.loads(json.dumps(connection_message))
            conn_message["data"]["alias"] = f"Aegizz-{conn_id}"
            
            await websocket.send(json.dumps(conn_message))
            print(f"Connection {conn_id}: Sent connection message")

            while True:
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=5)
                    response_data = json.loads(response)
                    
                    # Handle connection establishment
                    if response_data.get("event") == "connection" and "data" in response_data:
                        if response_data["data"].get("player_id") == PLAYER_ID:
                            print(f"Connection {conn_id}: Established, sending start event...")
                            await websocket.send(json.dumps(start_message))
                    
                    # Handle state updates
                    elif response_data.get("event") == "state" and "data" in response_data:
                        state_data = response_data["data"]
                        forecast = state_data.get("price_forecast", 0)
                        momentum = state_data.get("momentum", 0)
                        position = state_data.get("position", 0)
                        position_limit = state_data.get("position_limit", 3)
                        current_price = state_data.get("price", 0)
                        current_pnl = state_data.get("pnl", 0)
                        
                        # Get current strategy parameters
                        async with shared_state.lock:
                            params = dict(shared_state.strategy_params)
                        
                        # Calculate trade volume
                        trade_volume = determine_trade_volume(
                            forecast, momentum, position, position_limit, conn_id, params
                        )
                        
                        # Track PnL changes
                        last_pnl = shared_state.connection_performance[conn_id]["last_pnl"]
                        pnl_change = current_pnl - last_pnl
                        shared_state.connection_performance[conn_id]["last_pnl"] = current_pnl
                        
                        # Record performance data for optimization
                        if shared_state.connection_performance[conn_id]["trades_made"] > 0:
                            perf_data = {
                                "conn_id": conn_id,
                                "timestamp": time.time(),
                                "momentum": momentum,
                                "forecast": forecast,
                                "position": position,
                                "trade_volume": trade_volume,
                                "pnl_change": pnl_change,
                                "price": current_price,
                                "total_pnl": current_pnl
                            }
                            shared_state.performance_history.append(perf_data)
                        
                        print(f"Connection {conn_id}: Price=${current_price}, Forecast={forecast:.2f}, "
                              f"Momentum={momentum:.2f}, Position={position}/{position_limit}, PnL=${current_pnl}")
                        
                        # Execute trade if needed
                        if trade_volume != 0:
                            trade_message = {
                                "event": "trade",
                                "player_id": PLAYER_ID,
                                "data": {
                                    "volume": trade_volume
                                }
                            }
                            await websocket.send(json.dumps(trade_message))
                            print(f"Connection {conn_id}: Sent trade: "
                                  f"{'BUY' if trade_volume > 0 else 'SELL'} {abs(trade_volume)}")
                            
                            # Update trade statistics
                            shared_state.connection_performance[conn_id]["trades_made"] += 1
                        
                        # Update strategy periodically
                        await optimize_strategy()
                    
                    # Handle game end
                    elif response_data.get("event") == "finish" and "data" in response_data:
                        final_pnl = response_data["data"].get("pnl", 0)
                        print(f"Connection {conn_id}: Game over! Final PnL: ${final_pnl}")
                        print(f"Connection {conn_id}: Will reconnect shortly...")
                        # Instead of break, we'll raise an exception to ensure we hit the reconnection code
                        raise asyncio.CancelledError("Game finished, reconnecting...")
                    
                    # Handle puzzles
                    elif response_data.get("event") == "puzzle" and "data" in response_data:
                        puzzle_data = response_data["data"]
                        puzzle_impact = handle_puzzle_impact(puzzle_data)
                        
                        # Trade based on puzzle impact
                        if puzzle_impact != 0:
                            trade_message = {
                                "event": "trade",
                                "player_id": PLAYER_ID,
                                "data": {
                                    "volume": 3 if puzzle_impact > 0 else -3
                                }
                            }
                            await websocket.send(json.dumps(trade_message))
                            print(f"Connection {conn_id}: Sent puzzle trade: "
                                  f"{'BUY' if puzzle_impact > 0 else 'SELL'} 3")
                        
                        # Skip to next stage
                        await websocket.send(json.dumps(skip_message))
                        print(f"Connection {conn_id}: Sent skip message")
                
                except asyncio.TimeoutError:
                    continue
                except json.JSONDecodeError as e:
                    print(f"Connection {conn_id}: JSON decode error: {e}")
                    continue
                except Exception as e:
                    print(f"Connection {conn_id}: Error in message handling: {e}")
                    # Let the outer exception handler deal with reconnection
                    raise
    
    except Exception as e:
        print(f"Connection {conn_id}: Connection error: {e}")
    
    print(f"Connection {conn_id}: Closed, preparing to reconnect")
    
    # Wait a few seconds before reconnecting
    await asyncio.sleep(random.uniform(1, 3))
    
    # Reconnect by creating a new task
    asyncio.create_task(handle_connection(conn_id))

async def main():
    # Start multiple connections in parallel
    connection_tasks = []
    for i in range(NUM_CONNECTIONS):
        task = asyncio.create_task(handle_connection(i))
        connection_tasks.append(task)
    
    # Wait for all connections to complete (this will run indefinitely)
    await asyncio.gather(*connection_tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception as e:
        print(f"Critical error in main loop: {e}")