import json
import os
import time
import inspect
from opentelemetry import trace

# Super simple in-memory cache
_rewards_cache = {}

def _initialize_cache():
    """Pre-populate cache with common demo users to avoid cache misses on new containers"""
    # Generate cache for common demo users (user-001 to user-020)
    common_users = [f"user-{i:03d}" for i in range(1, 21)]
    for user_id in common_users:
        _rewards_cache[user_id] = {
            'eligible': True,
            'cashback_percent': 2.0,
            'points_earned': 0,  # Will be updated per transaction amount
            'tier': 'GOLD',
            'cache_used': True
        }
    print(f"Pre-populated cache with {len(common_users)} users to avoid cold container cache misses")

# Initialize cache on container startup to prevent cache miss latency spikes
_initialize_cache()

def add_code_location_attributes():
    """
    Add code location attributes to the current span following OpenTelemetry semantic conventions.
    Adds code.file.path, code.line.number, and code.function.name attributes for observability.
    """
    span = trace.get_current_span()
    if span:
        # Get caller information from the stack
        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_frame = frame.f_back
            file_name = os.path.basename(caller_frame.f_code.co_filename)
            line_number = caller_frame.f_lineno
            function_name = f"{caller_frame.f_code.co_name}"
            
            # Add attributes using OpenTelemetry semantic conventions
            span.set_attribute("code.file.path", file_name)
            span.set_attribute("code.line.number", line_number)
            span.set_attribute("code.function.name", function_name)

def handler(event, context):
    """
    Super simple rewards eligibility service.
    Returns rewards data after a delay if cache is disabled.
    """
    # Add code location attributes to the auto-instrumented server span
    add_code_location_attributes()
    
    use_cache = os.environ.get('USE_CACHE', 'false').lower() == 'true'
    delay_ms = int(os.environ.get('REWARDS_QUERY_DELAY_MS', '1500'))  # Restored to 1500ms for demo scenarios
    
    # Get user ID from event (simple extraction)
    user_id = event.get('userId', 'unknown')
    amount = event.get('amount', 0)
    
    # Simple cache key - just userId for better hit rate
    cache_key = user_id
    
    # Check cache if enabled
    if use_cache and cache_key in _rewards_cache:
        print(f"Cache hit for user {user_id}")
        # Return cached response but update points for current amount
        cached = _rewards_cache[cache_key].copy()
        cached['points_earned'] = int(amount * 10)
        return cached
    
    # Simulate slow database query when cache is disabled or on cache miss
    if not use_cache or cache_key not in _rewards_cache:
        print(f"Cache {'miss' if use_cache else 'disabled'} - simulating slow database query")
        time.sleep(delay_ms / 1000.0)
    
    # Super simple rewards response - just return something
    rewards = {
        'eligible': True,
        'cashback_percent': 2.0,
        'points_earned': int(amount * 10),
        'tier': 'GOLD',
        'cache_used': use_cache
    }
    
    # Store in cache if enabled
    if use_cache:
        _rewards_cache[cache_key] = rewards
        # Keep cache small but preserve pre-populated users (simple LRU-ish behavior)
        if len(_rewards_cache) > 100:
            # Clear cache but immediately re-populate common users to avoid cache misses
            _rewards_cache.clear()
            _initialize_cache()
    
    return rewards