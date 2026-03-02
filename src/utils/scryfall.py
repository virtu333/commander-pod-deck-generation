"""Scryfall API client with rate limiting and caching.

Handles bulk data downloads, individual card lookups by Scryfall ID,
and search queries (e.g., is:gamechanger). Respects 50-100ms rate limits.
Caches responses in SQLite.
"""
