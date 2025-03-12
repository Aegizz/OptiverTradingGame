use async_std::sync::{Arc, Mutex, RwLock};
use futures::stream::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use async_std::task;
use async_tungstenite::{async_std::connect_async, tungstenite::Message};
use futures::SinkExt;
use rand::Rng;
use statrs::statistics::Statistics;
use std::f64;

// Constants
const URL: &str = "wss://vega-apac.optibook.net/ws/e65ed16e-1042-4aac-8327-e6f972d120d5";
const PLAYER_ID: &str = "50cc97f7-e061-519e-862d-25c882cab50b";
const NUM_CONNECTIONS: usize = 5;
const HISTORY_SIZE: usize = 20;

// Message structures
#[derive(Serialize, Deserialize, Debug, Clone)]
struct ConnectionMessage {
    event: String,
    player_id: String,
    data: ConnectionData,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct ConnectionData {
    alias: String,
    player_id: String,
    token: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct StartMessage {
    event: String,
    player_id: String,
    data: StartData,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct StartData {
    player_id: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct SkipMessage {
    event: String,
    player_id: String,
    data: Value,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct TradeMessage {
    event: String,
    player_id: String,
    data: TradeData,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct TradeData {
    volume: i32,
}

// State structures
#[derive(Debug, Clone)]
struct SignalData {
    conn_id: usize,
    timestamp: f64,
    momentum: f64,
    forecast: f64,
    combined_signal: f64,
    trade_volume: i32,
    position: i32,
}

#[derive(Debug, Clone)]
struct PerformanceData {
    conn_id: usize,
    timestamp: f64,
    momentum: f64,
    forecast: f64,
    position: i32,
    trade_volume: i32,
    pnl_change: f64,
    price: f64,
    total_pnl: f64,
}

#[derive(Debug, Clone)]
struct ConnectionPerformance {
    last_pnl: f64,
    trades_made: usize,
    successful_trades: usize,
}

#[derive(Debug, Clone)]
struct StrategyParams {
    momentum_weight: f64,
    forecast_weight: f64,
    strong_momentum_threshold: f64,
    medium_momentum_threshold: f64,
    aggressive_factor: f64,
}

// Shared state
struct SharedState {
    strategy_params: RwLock<StrategyParams>,
    trade_history: Mutex<VecDeque<SignalData>>,
    performance_history: Mutex<VecDeque<PerformanceData>>,
    connection_performance: Mutex<std::collections::HashMap<usize, ConnectionPerformance>>,
    last_optimization: RwLock<f64>,
    optimization_interval: f64,
}

impl SharedState {
    fn new() -> Self {
        SharedState {
            strategy_params: RwLock::new(StrategyParams {
                momentum_weight: 0.6,
                forecast_weight: 0.4,
                strong_momentum_threshold: 10.0,
                medium_momentum_threshold: 5.0,
                aggressive_factor: 1.5,
            }),
            trade_history: Mutex::new(VecDeque::with_capacity(HISTORY_SIZE)),
            performance_history: Mutex::new(VecDeque::with_capacity(HISTORY_SIZE)),
            connection_performance: Mutex::new(std::collections::HashMap::new()),
            last_optimization: RwLock::new(timestamp()),
            optimization_interval: 30.0,
        }
    }
}

// Helper function for current time
fn timestamp() -> f64 {
    let start = SystemTime::now();
    let since_the_epoch = start
        .duration_since(UNIX_EPOCH)
        .expect("Time went backwards");
    since_the_epoch.as_secs_f64()
}

// Handle puzzle impact
fn handle_puzzle_impact(puzzle_data: &Value) -> i32 {
    let impact = puzzle_data["impact"].as_f64().unwrap_or(0.0);
    if impact > 0.0 {
        println!("The stock will increase by ${}", impact);
        return impact.abs() as i32; // Buy signal
    } else if impact < 0.0 {
        println!("The stock will decrease by ${}", impact.abs());
        return -(impact.abs() as i32); // Sell signal
    }
    0 // No trade
}

async fn determine_trade_volume(
    forecast: f64,
    momentum: f64,
    position: i32,
    position_limit: i32,
    conn_id: usize,
    shared_state: &Arc<SharedState>,
) -> i32 {
    // Get current strategy parameters (unused in volume calculation here,
    // but still used for signal weightings, if needed)
    let params = shared_state.strategy_params.read().await.clone();

    // Calculate signals with tanh smoothing (values in (-1, 1))
    let momentum_signal = f64::tanh(momentum / 10.0);
    let forecast_signal = f64::tanh(forecast * 2.0);

    // Weighted combination
    let combined_signal = (momentum_signal * params.momentum_weight)
        + (forecast_signal * params.forecast_weight);

    // In risky mode: if there is any signal, go all-in.
    // - If the signal is positive, buy the full available amount.
    // - If negative, sell the full available amount.
    let trade_volume = if combined_signal > 0.0 {
        // Maximum buy: position_limit minus current position.
        let max_buy = position_limit - position;
        max_buy
    } else if combined_signal < 0.0 {
        // Maximum sell: current position plus position_limit.
        let max_sell = position + position_limit;
        -max_sell
    } else {
        0
    };

    // Record for strategy optimization
    let signal_data = SignalData {
        conn_id,
        timestamp: timestamp(),
        momentum,
        forecast,
        combined_signal,
        trade_volume,
        position,
    };

    // Add to history with mutex protection
    let mut history = shared_state.trade_history.lock().await;
    if history.len() >= HISTORY_SIZE {
        history.pop_front();
    }
    history.push_back(signal_data);

    trade_volume
}

// Strategy optimization
async fn optimize_strategy(shared_state: &Arc<SharedState>) {
    // Check if it's time to optimize
    let current_time = timestamp();
    {
        let last_opt = *shared_state.last_optimization.read().await;
        if current_time - last_opt < shared_state.optimization_interval {
            return;
        }
        
        // Check if we have enough data
        let perf_history = shared_state.performance_history.lock().await;
        if perf_history.len() < 5 {
            return;
        }
    }
    
    // Update optimization timestamp
    *shared_state.last_optimization.write().await = current_time;
    
    // Extract performance data
    let performances: Vec<PerformanceData>;
    {
        let history = shared_state.performance_history.lock().await;
        performances = history.iter().cloned().collect();
    }
    
    if !performances.is_empty() {
        // Calculate average profit
        let pnl_changes: Vec<f64> = performances.iter()
            .map(|p| p.pnl_change)
            .collect();
        let avg_profit = pnl_changes.mean();
        
        // Update strategy based on performance
        let mut params = shared_state.strategy_params.write().await;
        
        if avg_profit > 5.0 {
            // Strategy is working well
            let mut momentum_correlations = Vec::new();
            let mut forecast_correlations = Vec::new();
            
            for p in &performances {
                if p.pnl_change > 0.0 && p.trade_volume != 0 {
                    // Profitable trade - analyze signals
                    if f64::abs(p.momentum) > f64::abs(p.forecast) {
                        momentum_correlations.push(1.0);
                        forecast_correlations.push(0.5);
                    } else {
                        momentum_correlations.push(0.5);
                        forecast_correlations.push(1.0);
                    }
                }
            }
            
            // Update weights if we have correlation data
            if !momentum_correlations.is_empty() && !forecast_correlations.is_empty() {
                let avg_momentum_corr = momentum_correlations.mean();
                let avg_forecast_corr = forecast_correlations.mean();
                let total = avg_momentum_corr + avg_forecast_corr;
                
                params.momentum_weight = avg_momentum_corr / total;
                params.forecast_weight = avg_forecast_corr / total;
                params.aggressive_factor = f64::min(2.0, params.aggressive_factor + 0.1);
            }
        } else if avg_profit < -5.0 {
            // Strategy is losing money
            params.momentum_weight = 0.5;
            params.forecast_weight = 0.5;
            params.aggressive_factor = f64::max(1.0, params.aggressive_factor - 0.2);
        }
        
        println!("Optimized strategy parameters: momentum_weight={}, forecast_weight={}, aggressive_factor={}",
                params.momentum_weight, params.forecast_weight, params.aggressive_factor);
    }
}

// Handle single connection
async fn handle_connection(conn_id: usize, shared_state: Arc<SharedState>) {
    println!("Starting connection {}", conn_id);
    
    // Initialize connection performance
    {
        let mut performances = shared_state.connection_performance.lock().await;
        if !performances.contains_key(&conn_id) {
            performances.insert(conn_id, ConnectionPerformance {
                last_pnl: 0.0,
                trades_made: 0,
                successful_trades: 0,
            });
        }
    }
    
    loop {
        println!("Connection {}: Connecting to WebSocket", conn_id);
        
        match connect_async(URL).await {
            Ok((mut ws_stream, _)) => {
                println!("Connection {}: Connected to WebSocket", conn_id);
                
                // Send connection message
                let conn_message = json!({
                    "event": "connection",
                    "player_id": "",
                    "data": {
                        "alias": format!("Aegizz-{}", conn_id),
                        "player_id": PLAYER_ID,
                        "token": ""
                    }
                });
                
                if let Err(e) = ws_stream.send(Message::Text(conn_message.to_string())).await {
                    println!("Connection {}: Error sending connection message: {}", conn_id, e);
                    task::sleep(Duration::from_secs(2)).await;
                    continue;
                }
                
                println!("Connection {}: Sent connection message", conn_id);
                
                // Message handling loop
                while let Some(msg_result) = ws_stream.next().await {
                    match msg_result {
                        Ok(msg) => {
                            if let Message::Text(text) = msg {
                                match serde_json::from_str::<Value>(&text) {
                                    Ok(response_data) => {
                                        let event = response_data["event"].as_str().unwrap_or("");
                                        
                                        // Handle connection establishment
                                        if event == "connection" && response_data.get("data").is_some() {
                                            if let Some(player_id) = response_data["data"]["player_id"].as_str() {
                                                if player_id == PLAYER_ID {
                                                    println!("Connection {}: Established, sending start event...", conn_id);
                                                    
                                                    let start_message = json!({
                                                        "event": "start",
                                                        "player_id": "",
                                                        "data": {
                                                            "player_id": PLAYER_ID
                                                        }
                                                    });
                                                    
                                                    if let Err(e) = ws_stream.send(Message::Text(start_message.to_string())).await {
                                                        println!("Connection {}: Error sending start message: {}", conn_id, e);
                                                        break;
                                                    }
                                                }
                                            }
                                        }
                                        // Handle state updates
                                        else if event == "state" && response_data.get("data").is_some() {
                                            let state_data = &response_data["data"];
                                            
                                            let forecast = state_data["price_forecast"].as_f64().unwrap_or(0.0);
                                            let momentum = state_data["momentum"].as_f64().unwrap_or(0.0);
                                            let position = state_data["position"].as_i64().unwrap_or(0) as i32;
                                            let position_limit = state_data["position_limit"].as_i64().unwrap_or(3) as i32;
                                            let current_price = state_data["price"].as_f64().unwrap_or(0.0);
                                            let current_pnl = state_data["pnl"].as_f64().unwrap_or(0.0);
                                            
                                            // Calculate trade volume
                                            let trade_volume = determine_trade_volume(
                                                forecast,
                                                momentum,
                                                position,
                                                position_limit,
                                                conn_id,
                                                &shared_state
                                            ).await;
                                            
                                            // Track PnL changes
                                            let pnl_change: f64;
                                            {
                                                let mut performances = shared_state.connection_performance.lock().await;
                                                let perf = performances.get_mut(&conn_id).unwrap();
                                                pnl_change = current_pnl - perf.last_pnl;
                                                perf.last_pnl = current_pnl;
                                                
                                                // Record performance data if we've made trades
                                                if perf.trades_made > 0 {
                                                    let perf_data = PerformanceData {
                                                        conn_id,
                                                        timestamp: timestamp(),
                                                        momentum,
                                                        forecast,
                                                        position,
                                                        trade_volume,
                                                        pnl_change,
                                                        price: current_price,
                                                        total_pnl: current_pnl,
                                                    };
                                                    
                                                    let mut history = shared_state.performance_history.lock().await;
                                                    if history.len() >= HISTORY_SIZE {
                                                        history.pop_front();
                                                    }
                                                    history.push_back(perf_data);
                                                }
                                            }
                                            
                                            println!(
                                                "Connection {}: Price=${}, Forecast={:.2}, Momentum={:.2}, Position={}/{}, PnL=${}",
                                                conn_id, current_price, forecast, momentum, position, position_limit, current_pnl
                                            );
                                            
                                            // Execute trade if needed
                                            if trade_volume != 0 {
                                                let trade_message = json!({
                                                    "event": "trade",
                                                    "player_id": PLAYER_ID,
                                                    "data": {
                                                        "volume": trade_volume
                                                    }
                                                });
                                                
                                                if let Err(e) = ws_stream.send(Message::Text(trade_message.to_string())).await {
                                                    println!("Connection {}: Error sending trade message: {}", conn_id, e);
                                                    break;
                                                }
                                                
                                                println!(
                                                    "Connection {}: Sent trade: {} {}",
                                                    conn_id,
                                                    if trade_volume > 0 { "BUY" } else { "SELL" },
                                                    trade_volume.abs()
                                                );
                                                
                                                // Update trade statistics
                                                {
                                                    let mut performances = shared_state.connection_performance.lock().await;
                                                    let perf = performances.get_mut(&conn_id).unwrap();
                                                    perf.trades_made += 1;
                                                }
                                            }
                                            
                                            // Optimize strategy periodically
                                            optimize_strategy(&shared_state).await;
                                        }
                                        // Handle game end
                                        else if event == "finish" && response_data.get("data").is_some() {
                                            let final_pnl = response_data["data"]["pnl"].as_f64().unwrap_or(0.0);
                                            println!("Connection {}: Game over! Final PnL: ${}", conn_id, final_pnl);
                                            println!("Connection {}: Will reconnect shortly...", conn_id);
                                            break;
                                        }
                                        // Handle puzzles
                                        else if event == "puzzle" && response_data.get("data").is_some() {
                                            let puzzle_data = &response_data["data"];
                                            let puzzle_impact = handle_puzzle_impact(puzzle_data);
                                            
                                            // Trade based on puzzle impact
                                            if puzzle_impact != 0 {
                                                let trade_message = json!({
                                                    "event": "trade",
                                                    "player_id": PLAYER_ID,
                                                    "data": {
                                                        "volume": if puzzle_impact > 0 { 3 } else { -3 }
                                                    }
                                                });
                                                
                                                if let Err(e) = ws_stream.send(Message::Text(trade_message.to_string())).await {
                                                    println!("Connection {}: Error sending puzzle trade: {}", conn_id, e);
                                                    break;
                                                }
                                                
                                                println!(
                                                    "Connection {}: Sent puzzle trade: {} 3",
                                                    conn_id,
                                                    if puzzle_impact > 0 { "BUY" } else { "SELL" }
                                                );
                                            }
                                            
                                            // Skip to next stage
                                            let skip_message = json!({
                                                "event": "skip",
                                                "player_id": "",
                                                "data": {}
                                            });
                                            
                                            if let Err(e) = ws_stream.send(Message::Text(skip_message.to_string())).await {
                                                println!("Connection {}: Error sending skip message: {}", conn_id, e);
                                                break;
                                            }
                                            
                                            println!("Connection {}: Sent skip message", conn_id);
                                        }
                                    },
                                    Err(e) => {
                                        println!("Connection {}: JSON decode error: {}", conn_id, e);
                                    }
                                }
                            }
                        },
                        Err(e) => {
                            println!("Connection {}: WebSocket error: {}", conn_id, e);
                            break;
                        }
                    }
                }
            },
            Err(e) => {
                println!("Connection {}: Failed to connect: {}", conn_id, e);
            }
        }
        
        println!("Connection {}: Closed, preparing to reconnect", conn_id);
        
        // Wait a few seconds before reconnecting
        let delay = rand::thread_rng().gen_range(1..4);
        task::sleep(Duration::from_secs(delay)).await;
    }
}

// Entry point
#[async_std::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("Starting trading bot with {} connections", NUM_CONNECTIONS);
    
    // Create shared state
    let shared_state = Arc::new(SharedState::new());
    
    // Start multiple connections in parallel
    let mut handles = Vec::new();
    for i in 0..NUM_CONNECTIONS {
        let state_clone = Arc::clone(&shared_state);
        let handle = task::spawn(async move {
            handle_connection(i, state_clone).await;
        });
        handles.push(handle);
    }
    
    // Wait for all connections (this will run indefinitely)
    futures::future::join_all(handles).await;
    
    Ok(())
}