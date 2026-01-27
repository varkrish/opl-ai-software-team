"""
Retry handler for API rate limits and timeouts
Handles rate limit errors by waiting for the specified retry delay
"""
import os
import time
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional
from functools import wraps

logger = logging.getLogger(__name__)


def extract_retry_delay(error: Exception) -> Optional[float]:
    """
    Extract retry delay from API error message
    
    Args:
        error: Exception from API call
        
    Returns:
        Retry delay in seconds, or None if not found
    """
    error_str = str(error)
    
    # Look for "Please retry in Xs" or "retryDelay" in error
    import re
    
    # Pattern 1: "Please retry in 43.414490109s"
    match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
    if match:
        return float(match.group(1))
    
    # Pattern 2: retryDelay in JSON error details
    match = re.search(r'"retryDelay":\s*"([\d.]+)s?"', error_str)
    if match:
        return float(match.group(1))
    
    # Pattern 3: retryDelay as number
    match = re.search(r'"retryDelay":\s*([\d.]+)', error_str)
    if match:
        return float(match.group(1))
    
    return None


def is_quota_exhausted(error: Exception) -> bool:
    """
    Check if error indicates quota exhaustion (daily limit reached)
    This is different from rate limits - quota exhaustion means no more requests today
    """
    error_str = str(error)
    
    # Check for quota exhaustion indicators
    quota_exhausted_indicators = [
        'exceeded your current quota',
        'quota exceeded for metric',
        'limit: 250',  # Daily limit reached
        'limit: 10',   # Per-minute limit (but check context)
        'GenerateRequestsPerDay',
        'free_tier_requests',
        'please check your plan and billing'
    ]
    
    # If it's a 429 but mentions quota exceeded with daily limits, it's quota exhaustion
    if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
        for indicator in quota_exhausted_indicators:
            if indicator.lower() in error_str.lower():
                # Additional check: if retry delay is very long (> 1 hour), likely quota exhaustion
                retry_delay = extract_retry_delay(error)
                if retry_delay and retry_delay > 3600:  # More than 1 hour
                    return True
                # If it mentions daily limits specifically
                if 'perday' in error_str.lower() or 'per day' in error_str.lower():
                    return True
    
    return False


def is_rate_limit_error(error: Exception) -> bool:
    """
    Check if error is a rate limit error (temporary, can retry)
    This is different from quota exhaustion
    """
    error_str = str(error)
    
    # If it's quota exhausted, it's not a rate limit
    if is_quota_exhausted(error):
        return False
    
    # Otherwise check for rate limit indicators
    return (
        '429' in error_str or
        'RESOURCE_EXHAUSTED' in error_str or
        'rate limit' in error_str.lower()
    )


def is_timeout_error(error: Exception) -> bool:
    """Check if error is a timeout error"""
    error_str = str(error)
    return (
        'timeout' in error_str.lower() or
        'timed out' in error_str.lower() or
        '504' in error_str or
        '408' in error_str
    )


def retry_with_wait(
    max_attempts: int = 10,
    default_wait: float = 60.0,
    save_state: bool = True,
    state_file: Optional[str] = None
):
    """
    Decorator to retry function calls with wait on rate limit/timeout errors
    
    Args:
        max_attempts: Maximum number of retry attempts
        default_wait: Default wait time in seconds if retry delay not found
        save_state: Whether to save state for resumability
        state_file: Path to state file (auto-generated if None)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
            state_path = Path(workspace_path) / ".crew_state.json"
            
            # Load previous state if exists
            attempt = 0
            if save_state and state_path.exists():
                try:
                    with open(state_path, 'r') as f:
                        state = json.load(f)
                        attempt = state.get('attempt', 0)
                        logger.info(f"📂 Resuming from attempt {attempt}")
                except Exception as e:
                    logger.warning(f"Could not load state: {e}")
            
            last_error = None
            
            while attempt < max_attempts:
                try:
                    # Execute function
                    result = func(*args, **kwargs)
                    
                    # Clear state on success
                    if save_state and state_path.exists():
                        state_path.unlink()
                    
                    return result
                    
                except Exception as e:
                    last_error = e
                    attempt += 1
                    
                    # Check if it's a retryable error
                    is_retryable = is_rate_limit_error(e) or is_timeout_error(e)
                    
                    if not is_retryable or attempt >= max_attempts:
                        # Not retryable or max attempts reached
                        if save_state and state_path.exists():
                            state_path.unlink()
                        raise
                    
                    # Extract retry delay
                    retry_delay = extract_retry_delay(e)
                    if retry_delay is None:
                        retry_delay = default_wait
                    
                    # Add small buffer to retry delay
                    wait_time = retry_delay + 1.0
                    
                    logger.warning(
                        f"⚠️  Rate limit/timeout error (attempt {attempt}/{max_attempts}): {str(e)[:100]}"
                    )
                    logger.info(f"⏳ Waiting {wait_time:.1f} seconds before retry...")
                    
                    # Save state
                    if save_state:
                        state_data = {
                            'attempt': attempt,
                            'function': func.__name__,
                            'error': str(e)[:500],
                            'timestamp': time.time()
                        }
                        try:
                            with open(state_path, 'w') as f:
                                json.dump(state_data, f, indent=2)
                        except Exception as save_error:
                            logger.warning(f"Could not save state: {save_error}")
                    
                    # Wait before retry
                    time.sleep(wait_time)
                    
                    logger.info(f"🔄 Retrying (attempt {attempt + 1}/{max_attempts})...")
            
            # Max attempts reached
            if save_state and state_path.exists():
                state_path.unlink()
            raise last_error
        
        return wrapper
    return decorator


def safe_execute_with_retry(
    func: Callable,
    *args,
    max_attempts: int = 10,
    default_wait: float = 60.0,
    save_state: bool = True,
    **kwargs
) -> Any:
    """
    Execute a function with retry logic for rate limits/timeouts
    
    Args:
        func: Function to execute
        *args: Positional arguments
        max_attempts: Maximum retry attempts
        default_wait: Default wait time in seconds
        save_state: Save state for resumability
        **kwargs: Keyword arguments
        
    Returns:
        Function result
    """
    workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
    state_path = Path(workspace_path) / ".crew_state.json"
    
    # Load previous state
    attempt = 0
    if save_state and state_path.exists():
        try:
            with open(state_path, 'r') as f:
                state = json.load(f)
                attempt = state.get('attempt', 0)
                logger.info(f"📂 Resuming from attempt {attempt}")
        except Exception as e:
            logger.warning(f"Could not load state: {e}")
    
    last_error = None
    
    while attempt < max_attempts:
        try:
            result = func(*args, **kwargs)
            
            # Clear state on success
            if save_state and state_path.exists():
                state_path.unlink()
            
            return result
            
        except Exception as e:
            last_error = e
            attempt += 1
            
            # Check if quota is exhausted (stop immediately, don't retry)
            if is_quota_exhausted(e):
                logger.error("❌ QUOTA EXHAUSTED - Daily limit reached. Stopping job.")
                logger.error(f"   Error: {str(e)[:200]}")
                if save_state and state_path.exists():
                    state_path.unlink()
                # Raise a specific exception for quota exhaustion
                quota_error = Exception(
                    f"QUOTA_EXHAUSTED: Daily API quota limit reached. "
                    f"Please check your plan and billing details. "
                    f"Original error: {str(e)[:500]}"
                )
                quota_error.quota_exhausted = True
                raise quota_error
            
            # Check if retryable (rate limit or timeout)
            is_retryable = is_rate_limit_error(e) or is_timeout_error(e)
            
            if not is_retryable or attempt >= max_attempts:
                if save_state and state_path.exists():
                    state_path.unlink()
                raise
            
            # Extract retry delay
            retry_delay = extract_retry_delay(e)
            if retry_delay is None:
                retry_delay = default_wait
            
            wait_time = retry_delay + 1.0
            
            logger.warning(
                f"⚠️  Rate limit/timeout (attempt {attempt}/{max_attempts}): {str(e)[:100]}"
            )
            logger.info(f"⏳ Waiting {wait_time:.1f} seconds...")
            
            # Save state
            if save_state:
                state_data = {
                    'attempt': attempt,
                    'function': func.__name__,
                    'error': str(e)[:500],
                    'timestamp': time.time()
                }
                try:
                    with open(state_path, 'w') as f:
                        json.dump(state_data, f, indent=2)
                except Exception as save_error:
                    logger.warning(f"Could not save state: {save_error}")
            
            # Wait
            time.sleep(wait_time)
            
            logger.info(f"🔄 Retrying (attempt {attempt + 1}/{max_attempts})...")
    
    # Max attempts reached
    if save_state and state_path.exists():
        state_path.unlink()
    raise last_error

