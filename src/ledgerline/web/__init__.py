"""Web-facing adapters for the engine.

Nothing in here is part of the trading core. These modules turn the immutable
results the engine already produces into JSON-safe structures a browser (or any
HTTP client) can consume. They add no behaviour and read no state the engine did
not already expose — a serializer, not a second source of truth.
"""