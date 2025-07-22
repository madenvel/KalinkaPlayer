"""
Version module for Kalinka Player.

This module provides version information that is automatically generated
by setuptools-scm from git tags.
"""

import subprocess
import sys
from pathlib import Path

# This will be written by setuptools-scm
try:
    from ._version import version as __version__
except ImportError:
    # Fallback for development or when setuptools-scm hasn't run
    __version__ = None


def get_version() -> str:
    """
    Get the current version of the application.
    
    Returns:
        str: Version string in format X.Y.Z or X.Y.Z+commitish for dev versions
    """
    if __version__ and __version__ != "0.0.0+unknown":
        return __version__
    
    # Fall back to git-based version
    return get_version_from_git()


def get_version_from_git() -> str:
    """
    Get version directly from git tags as a fallback.
    
    Returns:
        str: Version string from git tags or "0.0.0" if not available
    """
    try:
        # Try to get the current directory's git repo
        repo_root = Path(__file__).parent.parent
        
        # Get the most recent release tag
        result = subprocess.run(
            ["git", "describe", "--tags", "--match", "release-*.*.*", "--abbrev=0"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0 and result.stdout.strip():
            tag = result.stdout.strip()
            # Remove 'release-' prefix
            version = tag.replace("release-", "")
            return version
        
        # If no release tag, try to get version with commit info
        result = subprocess.run(
            ["git", "describe", "--tags", "--match", "release-*.*.*"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode == 0 and result.stdout.strip():
            desc = result.stdout.strip()
            # Format: release-1.2.3-4-g1234567
            if desc.startswith("release-"):
                desc = desc.replace("release-", "")
            return desc
            
        return "0.0.0"
        
    except (subprocess.SubprocessError, FileNotFoundError):
        return "0.0.0"


def get_api_version() -> str:
    """
    Get the API version.
    
    Returns:
        str: API version string
    """
    return "0.1"


# Module level constants
API_VERSION = get_api_version()
