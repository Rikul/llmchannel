"""Shared utility functions for the application."""

# Keywords used to detect OAuth-related errors
OAUTH_KEYWORDS = ['oauth', 'unauthorized', '401', '403', 'authentication']

# Keywords used to detect rate limiting errors
RATE_LIMIT_KEYWORDS = ['rate limit', 'rate_limit', 'ratelimit', '429', 'quota', 'too many requests']


def is_oauth_or_rate_limit_error(error_msg):
    """Check if error message is related to OAuth or rate limiting.
    
    Args:
        error_msg: The error message to check
        
    Returns:
        bool: True if the error appears to be OAuth or rate limit related
        
    Note:
        403 errors are included because OAuth providers commonly return 403
        for authentication issues (expired tokens, invalid credentials, etc.)
        in addition to authorization/permission errors.
    """
    error_lower = str(error_msg).lower()
    
    for keyword in OAUTH_KEYWORDS + RATE_LIMIT_KEYWORDS:
        if keyword in error_lower:
            return True
    return False
