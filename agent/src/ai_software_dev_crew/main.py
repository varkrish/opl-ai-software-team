#!/usr/bin/env python
import sys
import warnings

from datetime import datetime
from dotenv import load_dotenv

from .crew import AiSoftwareDevCrew
from .orchestrator import run_orchestrator, review_codebase

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# Load environment variables
load_dotenv()

# This main file is intended to be a way for you to run your
# crew locally, so refrain from adding unnecessary logic into this file.
# Replace with inputs you want to test with, it will automatically
# interpolate any tasks and agents information

def run():
    """
    Run the full software development orchestrator or code review.
    
    Usage:
        # Development mode (default)
        python main.py "Build a calculator API"
        
        # Code review mode (if first arg is a directory path)
        python main.py /path/to/codebase
        python main.py --review /path/to/codebase
    """
    import os
    from pathlib import Path
    
    # Check for review mode flag
    if len(sys.argv) > 1:
        if sys.argv[1] == "--review" or sys.argv[1] == "-r":
            # Review mode
            if len(sys.argv) > 2:
                codebase_path = " ".join(sys.argv[2:])
            else:
                codebase_path = None
            try:
                review_codebase(codebase_path)
            except Exception as e:
                raise Exception(f"An error occurred while reviewing codebase: {e}")
            return
        
        # Check if first argument is a directory path (code review mode)
        potential_path = Path(sys.argv[1]).expanduser().resolve()
        if potential_path.exists() and potential_path.is_dir():
            # It's a directory, treat as code review
            try:
                review_codebase(str(potential_path))
            except Exception as e:
                raise Exception(f"An error occurred while reviewing codebase: {e}")
            return
        
        # Check for design specs flags
        design_specs_path = None
        design_specs_urls = []
        
        # Check for --specs or --design flag (directory)
        if "--specs" in sys.argv or "--design" in sys.argv:
            spec_flag_idx = sys.argv.index("--specs") if "--specs" in sys.argv else sys.argv.index("--design")
            if len(sys.argv) > spec_flag_idx + 1:
                design_specs_path = sys.argv[spec_flag_idx + 1]
                # Remove the flag and path from args
                sys.argv = [sys.argv[0]] + [arg for i, arg in enumerate(sys.argv[1:], 1) if i not in [spec_flag_idx, spec_flag_idx + 1]]
        
        # Check for --urls or --references flag (URLs)
        if "--urls" in sys.argv or "--references" in sys.argv or "--refs" in sys.argv:
            url_flag = "--urls" if "--urls" in sys.argv else ("--references" if "--references" in sys.argv else "--refs")
            url_flag_idx = sys.argv.index(url_flag)
            if len(sys.argv) > url_flag_idx + 1:
                # Collect URLs until next flag or end
                urls = []
                i = url_flag_idx + 1
                while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                    urls.append(sys.argv[i])
                    i += 1
                design_specs_urls = urls
                # Remove the flag and URLs from args
                sys.argv = [sys.argv[0]] + [arg for i, arg in enumerate(sys.argv[1:], 1) 
                                          if i not in range(url_flag_idx, url_flag_idx + len(urls) + 1)]
        
        # Otherwise, treat as development vision
        vision = " ".join(sys.argv[1:])
    else:
        # Interactive mode - ask user what they want
        mode = input("\n🎯 What would you like to do?\n  1. Build new software\n  2. Review existing codebase\n  Choice (1/2): ").strip()
        
        if mode == "2":
            codebase_path = input("📁 Enter path to codebase: ").strip()
            try:
                review_codebase(codebase_path)
            except Exception as e:
                raise Exception(f"An error occurred while reviewing codebase: {e}")
            return
        else:
            vision = input("\n🎯 What would you like to build? ")
            specs_input = input("📋 Design specs directory (optional, press Enter to skip): ").strip()
            design_specs_path = specs_input if specs_input else None
            
            urls_input = input("🔗 Design spec URLs (comma-separated, optional, press Enter to skip): ").strip()
            if urls_input:
                design_specs_urls = [url.strip() for url in urls_input.split(",") if url.strip()]
            else:
                design_specs_urls = []
    
    try:
        run_orchestrator(vision, design_specs_path, design_specs_urls)
    except Exception as e:
        raise Exception(f"An error occurred while running the orchestrator: {e}")


def run_original():
    """
    Run the original demo crew (for testing).
    """
    inputs = {
        'topic': 'AI LLMs',
        'current_year': str(datetime.now().year)
    }

    try:
        AiSoftwareDevCrew().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"An error occurred while running the crew: {e}")


def train():
    """
    Train the crew for a given number of iterations.
    """
    inputs = {
        "topic": "AI LLMs",
        'current_year': str(datetime.now().year)
    }
    try:
        AiSoftwareDevCrew().crew().train(n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=inputs)

    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}")

def replay():
    """
    Replay the crew execution from a specific task.
    """
    try:
        AiSoftwareDevCrew().crew().replay(task_id=sys.argv[1])

    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}")

def test():
    """
    Test the crew execution and returns the results.
    """
    inputs = {
        "topic": "AI LLMs",
        "current_year": str(datetime.now().year)
    }

    try:
        AiSoftwareDevCrew().crew().test(n_iterations=int(sys.argv[1]), eval_llm=sys.argv[2], inputs=inputs)

    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}")

def run_with_trigger():
    """
    Run the crew with trigger payload.
    """
    import json

    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided. Please provide JSON payload as argument.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        raise Exception("Invalid JSON payload provided as argument")

    inputs = {
        "crewai_trigger_payload": trigger_payload,
        "topic": "",
        "current_year": ""
    }

    try:
        result = AiSoftwareDevCrew().crew().kickoff(inputs=inputs)
        return result
    except Exception as e:
        raise Exception(f"An error occurred while running the crew with trigger: {e}")


def web():
    """
    Run the web GUI application
    """
    from crew_studio.web_app import run_web_app
    import socket
    
    host = '0.0.0.0'
    port = 5000
    debug = False
    
    # Parse command line arguments
    if '--port' in sys.argv:
        idx = sys.argv.index('--port')
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    
    if '--host' in sys.argv:
        idx = sys.argv.index('--host')
        if idx + 1 < len(sys.argv):
            host = sys.argv[idx + 1]
    
    if '--debug' in sys.argv:
        debug = True

    # Function to find an available port if the requested one is taken
    def find_available_port(start_port, host):
        current_port = start_port
        while current_port < start_port + 100:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((host, current_port))
                    return current_port
                except socket.error:
                    current_port += 1
        return start_port

    # If port is 5000 (default) and we're not in a container, try to find an available one
    # or just always try to find one if the user didn't explicitly provide a port
    if '--port' not in sys.argv:
        port = find_available_port(port, host)
    
    run_web_app(host=host, port=port, debug=debug)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'web':
        web()
    else:
        run()
