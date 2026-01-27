"""
URL fetcher for design specifications
Fetches content from URLs and makes it available to crews
"""
import os
import logging
from pathlib import Path
from typing import List, Dict, Optional
import requests
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def fetch_url_content(url: str, timeout: int = 30) -> Optional[str]:
    """
    Fetch content from a URL
    
    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        
    Returns:
        Content as string, or None if fetch failed
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; AI-Software-Dev-Crew/1.0)'
        }
        
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        
        # Try to detect encoding
        if response.encoding:
            return response.text
        else:
            return response.content.decode('utf-8', errors='ignore')
            
    except requests.exceptions.Timeout:
        logger.warning(f"Timeout fetching URL: {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning(f"Error fetching URL {url}: {str(e)}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error fetching URL {url}: {str(e)}")
        return None


def get_url_filename(url: str) -> str:
    """
    Extract a meaningful filename from URL
    
    Args:
        url: URL to extract filename from
        
    Returns:
        Filename string
    """
    parsed = urlparse(url)
    path = parsed.path.strip('/')
    
    if path:
        # Get last part of path
        filename = path.split('/')[-1]
        if filename:
            return filename
    
    # Fallback to domain + path
    domain = parsed.netloc.replace('www.', '')
    return f"{domain}_{path.replace('/', '_')}" if path else domain


def fetch_urls(urls: List[str], timeout: int = 30) -> Dict[str, str]:
    """
    Fetch content from multiple URLs
    
    Args:
        urls: List of URLs to fetch
        timeout: Request timeout per URL
        
    Returns:
        Dictionary mapping URL to content (or None if fetch failed)
    """
    results = {}
    
    for url in urls:
        logger.info(f"📥 Fetching: {url}")
        content = fetch_url_content(url, timeout)
        
        if content:
            # Use URL as key, or filename if available
            key = get_url_filename(url) or url
            results[key] = content
            logger.info(f"✅ Fetched {len(content)} characters from {url}")
        else:
            logger.warning(f"❌ Failed to fetch: {url}")
            results[url] = None
    
    return results


def is_valid_url(url: str) -> bool:
    """
    Check if string is a valid URL
    
    Args:
        url: String to check
        
    Returns:
        True if valid URL, False otherwise
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

