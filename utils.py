"""Shared utility functions for the application."""


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
    oauth_keywords = ['oauth', 'unauthorized', '401', '403', 'authentication']
    rate_limit_keywords = ['rate limit', 'rate_limit', 'ratelimit', '429', 'quota', 'too many requests']
    
    for keyword in oauth_keywords + rate_limit_keywords:
        if keyword in error_lower:
            return True
    return False
