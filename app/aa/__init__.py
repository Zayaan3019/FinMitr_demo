"""
RBI Account Aggregator integration (PHASE 3).

FinGuru acts as an FIU under the DEPA consent framework: consent artifact ->
FI request -> notification -> FI fetch, encrypted end to end with X25519 ECDH.
No bank credential is ever seen, stored or transmitted by this application.

Sandbox-only. See ``client.py`` and the README for the stated FIU limitation.
"""

from app.aa.client import AAError, ConsentNotLive, FIUClient, get_fiu_client

__all__ = ["FIUClient", "get_fiu_client", "AAError", "ConsentNotLive"]
