"""Pure shaping for the app's views (Market, Signals, Review, the Inspector).

Each function takes plain data already read from the engine's state and logs
and returns what one view shows -- derived values are computed here, never in
the page's JavaScript. No I/O: api_server reads, these shape, tests pin them.
"""
