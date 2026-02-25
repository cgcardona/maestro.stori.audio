#!/usr/bin/env python3
"""
Access Code Generator CLI

Generate time-limited access codes for Stori Maestro.
Codes are cryptographically signed JWTs that expire after the specified duration.
All tokens must include a user ID (sub claim) for budget tracking.

Single-identifier (app flow): Use --user-id <device_uuid> where device_uuid is the
UUID the app sent at POST /api/v1/users/register. Then JWT sub = device UUID.

Usage:
    python scripts/generate_access_code.py --user-id <device-uuid> --days 7   # App user (after register)
    python scripts/generate_access_code.py --generate-user-id --days 7        # One-off / testing (then register)

Environment:
    STORI_ACCESS_TOKEN_SECRET must be set (generate with: openssl rand -hex 32)
"""
from __future__ import annotations

import argparse
import logging
import sys
import os
import uuid as uuid_module

# Add the app directory to the path so we can import from it
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.auth.tokens import generate_access_code, get_token_expiration, AccessCodeError

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate time-limited access codes for Stori Maestro",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --generate-user-id --hours 1        # New user, 1 hour (quick test)
    %(prog)s --generate-user-id --hours 24       # New user, 1 day
    %(prog)s --generate-user-id --days 7         # New user, 1 week
    %(prog)s --user-id UUID --days 30            # Existing user, 1 month
    %(prog)s --user-id UUID --minutes 30         # Existing user, 30 min (testing)

Environment:
    STORI_ACCESS_TOKEN_SECRET must be set before running this script.
    Generate one with: openssl rand -hex 32
        """
    )
    
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="User (device) UUID: same as app's device UUID from register (JWT sub = this UUID)",
    )
    parser.add_argument(
        "--generate-user-id",
        action="store_true",
        help="Generate a new UUID for this token (one-off/testing; then register that user_id)",
    )
    parser.add_argument(
        "--admin",
        action="store_true",
        help="Generate an admin token (can modify user budgets)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=0,
        help="Token validity in hours",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="Token validity in days",
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=0,
        help="Token validity in minutes (for testing)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only output the token (for scripting)",
    )
    
    args = parser.parse_args()
    
    # Validate at least one duration is specified
    if args.hours == 0 and args.days == 0 and args.minutes == 0:
        parser.error("At least one of --hours, --days, or --minutes must be specified")
    
    # REQUIRE user ID (either provided or generated)
    if not args.user_id and not args.generate_user_id:
        parser.error("Either --user-id or --generate-user-id is required. Legacy tokens without user IDs are no longer supported.")
    
    # Handle user ID
    user_id = args.user_id
    if args.generate_user_id:
        user_id = str(uuid_module.uuid4())
    
    # Validate user ID format
    try:
        uuid_module.UUID(user_id)
    except ValueError:
        parser.error(f"Invalid user ID format: {user_id}. Must be a valid UUID.")
    
    try:
        token = generate_access_code(
            user_id=user_id,
            duration_hours=args.hours if args.hours > 0 else None,
            duration_days=args.days if args.days > 0 else None,
            duration_minutes=args.minutes if args.minutes > 0 else None,
            is_admin=args.admin,
        )
        
        if args.quiet:
            print(token)
        else:
            expiration = get_token_expiration(token)

            parts = []
            if args.days > 0:
                parts.append(f"{args.days} day{'s' if args.days != 1 else ''}")
            if args.hours > 0:
                parts.append(f"{args.hours} hour{'s' if args.hours != 1 else ''}")
            if args.minutes > 0:
                parts.append(f"{args.minutes} minute{'s' if args.minutes != 1 else ''}")
            duration_str = ", ".join(parts)

            logger.info("\n" + "=" * 60)
            if args.admin:
                logger.info("STORI MAESTRO ADMIN ACCESS CODE")
            else:
                logger.info("STORI MAESTRO ACCESS CODE")
            logger.info("=" * 60)
            logger.info("\nDuration: %s", duration_str)
            logger.info("Expires:  %s", expiration.strftime("%Y-%m-%d %H:%M:%S UTC"))
            logger.info("User ID:  %s", user_id)
            if args.admin:
                logger.info("Role:     ADMIN (can modify user budgets)")
            logger.info("\nAccess Code:")
            logger.info("-" * 60)
            logger.info(token)
            logger.info("-" * 60)
            if args.admin:
                logger.warning("\nWARNING: This is an ADMIN token. Keep it secure!")
                logger.warning("Admin tokens can modify any user's budget.")
            else:
                if args.user_id:
                    logger.info("\nApp flow: This token's sub is the device UUID; app should use same UUID for X-Device-ID on assets.")
                else:
                    logger.info("\nOne-off: Register this user so they have a budget:")
                    logger.info('  curl -X POST https://<your-api>/api/v1/users/register -H "Content-Type: application/json" -d \'{"user_id": "%s"}\'', user_id)
                logger.info("\nThen share the access code with your user.")
            logger.info("=" * 60 + "\n")

    except AccessCodeError as e:
        logger.error("Error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
