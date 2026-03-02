"""SQLite caching layer for API responses.

Stores Scryfall card data, EDHREC commander data, and Game Changers list
locally to minimize API calls. Supports TTL-based expiration.
"""
