#!/usr/bin/env python3
"""
Generate a valid JWT token for testing.
Usage: python3 generate_test_token.py <secret>
"""
from __future__ import annotations

import logging
import jwt
import time
import sys
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

if len(sys.argv) < 2:
    logger.error("Usage: python3 generate_test_token.py <secret>")
    logger.error("")
    logger.error("Example:")
    logger.error("  python3 generate_test_token.py 37cb24acdd29d432af320f645d1e27f1470705e413a15b11f0443d968569ec31")
    sys.exit(1)

SECRET = sys.argv[1]

# Create token that expires in 24 hours
payload = {
    'type': 'access',
    'iat': int(time.time()),
    'exp': int(time.time()) + (24 * 3600),  # 24 hours
    'sub': 'test-user-comprehensive'
}

token = jwt.encode(payload, SECRET, algorithm='HS256')
print(token)
