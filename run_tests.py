import os
import sys
import subprocess
import time
import signal
import threading
import argparse

def run_server():
    print("Starting server...")
    server_process = subprocess.Popen(
        [sys.executable, "mcp-ssms-server.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    
    # Print server output in real-time
    def print_server_output():
        for line in server_process.stdout:
            print(f"[SERVER] {line.strip()}")
    
    def print_server_errors():
        for line in server_process.stderr:
            print(f"[SERVER ERROR] {line.strip()}")
    
    threading.Thread(target=print_server_output, daemon=True).start()
    threading.Thread(target=print_server_errors, daemon=True).start()
    
    # Wait for server to initialize
    print("Waiting for server to initialize...")
    time.sleep(3)
    return server_process

def run_client():
    print("Starting client...")
    client_process = subprocess.Popen(
        [sys.executable, "mcp-ssms-client.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    
    # Print client output in real-time
    def print_client_output():
        for line in client_process.stdout:
            print(f"[CLIENT] {line.strip()}")
    
    def print_client_errors():
        for line in client_process.stderr:
            print(f"[CLIENT ERROR] {line.strip()}")
    
    threading.Thread(target=print_client_output, daemon=True).start()
    threading.Thread(target=print_client_errors, daemon=True).start()
    
    return client_process

def main():
    parser = argparse.ArgumentParser(description="Run SQL Server Table Assistant tests")
    parser.add_argument("--server-only", action="store_true", help="Run only the server")
    parser.add_argument("--client-only", action="store_true", help="Run only the client")
    args = parser.parse_args()
    
    server_process = None
    client_process = None
    
    try:
        if not args.client_only:
            server_process = run_server()
        
        if not args.server_only:
            client_process = run_client()
        
        # Wait for user to stop
        print("\nPress Ctrl+C to stop...")
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nStopping processes...")
    finally:
        if client_process:
            print("Terminating client...")
            client_process.terminate()
            client_process.wait(5)
        
        if server_process:
            print("Terminating server...")
            server_process.terminate()
            server_process.wait(5)
        
        print("All processes terminated.")

if __name__ == "__main__":
    main() 