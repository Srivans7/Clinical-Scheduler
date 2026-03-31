"""
WSGI entry point for PythonAnywhere deployment.
PythonAnywhere will call this to run the Flask application.
"""
import sys
import os

project_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_dir)

from app import app as application
