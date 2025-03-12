# Simple Optiver Trading Bot

This bot abuses the fact that the websocket connection to the game disclosure information about the state of the game to answer questions and determine the future of the market.

There are four variations of the code here, the original main.py which should include lots of logging information to understand what I can see.

The pnl.py and rust implementation are the most optimal which I used to smash the highscore by using asynchronous connections and playing the luck game. Since I knew all the answers, strategy wasn't very important it was just how lucky I could get.

To build and run in rust:

```bash
cargo build
```

```bash
cargo run
```

