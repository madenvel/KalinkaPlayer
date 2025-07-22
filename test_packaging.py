#!/usr/bin/env python3
"""
Test script to verify the Kalinka Player packaging improvements.
"""

import sys
import os

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_version_module():
    """Test the version module functionality."""
    print("Testing version module...")
    
    try:
        from src.version import get_version, get_api_version, get_version_from_git
        
        version = get_version()
        api_version = get_api_version()
        git_version = get_version_from_git()
        
        print(f"✓ Version from module: {version}")
        print(f"✓ API version: {api_version}")
        print(f"✓ Git fallback version: {git_version}")
        
        # Verify version is not the unknown fallback
        assert version != "0.0.0+unknown", "Version should not be unknown"
        assert api_version == "0.1", "API version should be 0.1"
        assert git_version != "0.0.0", "Git version should be available"
        
        print("✓ Version module tests passed!")
        return True
        
    except Exception as e:
        print(f"✗ Version module test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_service_discovery_properties():
    """Test that service discovery properties include the correct version."""
    print("\nTesting service discovery properties...")
    
    try:
        from src.version import get_version, get_api_version
        
        # Create mock service properties
        props = {
            "kalinka_api_version": get_api_version(),
            "server_version": get_version()
        }
        
        print(f"✓ Service properties: {props}")
        
        # Verify properties
        assert "kalinka_api_version" in props, "API version should be in properties"
        assert "server_version" in props, "Server version should be in properties"
        assert props["kalinka_api_version"] == "0.1", "API version should be 0.1"
        assert props["server_version"] != "0.0.0+unknown", "Server version should not be unknown"
        
        print("✓ Service discovery properties tests passed!")
        return True
        
    except Exception as e:
        print(f"✗ Service discovery test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_packaging_files():
    """Test that packaging files exist and are valid."""
    print("\nTesting packaging files...")
    
    try:
        # Check for pyproject.toml
        assert os.path.exists("pyproject.toml"), "pyproject.toml should exist"
        print("✓ pyproject.toml exists")
        
        # Check for setup.py
        assert os.path.exists("setup.py"), "setup.py should exist"
        print("✓ setup.py exists")
        
        # Check for MANIFEST.in
        assert os.path.exists("MANIFEST.in"), "MANIFEST.in should exist"
        print("✓ MANIFEST.in exists")
        
        # Check for requirements files
        assert os.path.exists("requirements.txt"), "requirements.txt should exist"
        assert os.path.exists("requirements-dev.txt"), "requirements-dev.txt should exist"
        print("✓ Requirements files exist")
        
        # Check for build script
        assert os.path.exists("build.sh"), "build.sh should exist"
        print("✓ Build script exists")
        
        # Check for installation guide
        assert os.path.exists("INSTALL.md"), "INSTALL.md should exist"
        print("✓ Installation guide exists")
        
        print("✓ Packaging files tests passed!")
        return True
        
    except Exception as e:
        print(f"✗ Packaging files test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("Running Kalinka Player packaging tests...\n")
    
    tests = [
        test_version_module,
        test_service_discovery_properties,
        test_packaging_files,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
    
    print(f"\n{'='*50}")
    print(f"Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The packaging improvements are working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the output above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
