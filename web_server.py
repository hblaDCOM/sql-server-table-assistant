import os
import sys
import json
import threading
import time
import queue
import tempfile
import subprocess
from pathlib import Path
from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create Flask app
app = Flask(__name__, 
    static_folder='static',
    template_folder='templates')

# Secret key for session
app.config['SECRET_KEY'] = os.urandom(24).hex()

# Setup CORS and SocketIO
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# IP allowlist - will be loaded from config
ALLOWED_IPS = os.getenv('ALLOWED_IPS', '127.0.0.1').split(',')

# Active client processes
active_sessions = {}

@app.before_request
def check_ip():
    """Check if client IP is in the allowlist"""
    client_ip = request.remote_addr
    if client_ip not in ALLOWED_IPS and not client_ip.startswith('127.0.0.'):
        return jsonify({'error': 'Access denied'}), 403

@app.route('/')
def index():
    """Serve the main chat interface"""
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    """Handle new client connection"""
    client_ip = request.remote_addr
    if client_ip not in ALLOWED_IPS and not client_ip.startswith('127.0.0.'):
        return False
    
    # Generate a unique session ID
    session['uid'] = os.urandom(16).hex()
    print(f"Client connected: {session['uid']}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    if 'uid' in session:
        uid = session['uid']
        cleanup_session(uid)
        print(f"Client disconnected: {uid}")

@socketio.on('query')
def handle_query(data):
    """Handle user query and run it through the MCP client"""
    if 'uid' not in session:
        socketio.emit('response', {'text': 'Session error. Please refresh the page.'}, room=request.sid)
        return
    
    uid = session['uid']
    query = data.get('query', '').strip()
    
    if not query:
        return
    
    # Start a new client process if not already running
    if uid not in active_sessions:
        start_client_process(uid, request.sid)
    
    # Run the query
    status = run_client_query(uid, query, request.sid)
    if not status:
        socketio.emit('response', {'text': 'Error processing query. The assistant may need to be restarted.'}, room=request.sid)

def start_client_process(uid, sid):
    """Start a new client process with input/output files for IPC"""
    print(f"Starting new client process for session {uid}")
    
    # Create temporary files for IPC
    input_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, prefix=f'mcp_input_{uid}_', suffix='.txt')
    output_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, prefix=f'mcp_output_{uid}_', suffix='.txt')
    input_path = input_file.name
    output_path = output_file.name
    
    # Close the files so the subprocess can use them
    input_file.close()
    output_file.close()
    
    # Create a background process runner
    def run_process():
        try:
            # Use a modified version of the mcp-ssms-client.py that uses file I/O instead of stdin/stdout
            cmd = [sys.executable, "mcp-ssms-client-file.py", input_path, output_path]
            
            # Start the process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1  # Line buffered
            )
            
            # Update session data with process info
            active_sessions[uid]['process'] = process
            
            # Monitor process stdout for debugging
            for line in iter(process.stdout.readline, ''):
                print(f"Process output [{uid}]: {line.strip()}")
            
            # Process completed
            print(f"Client process for session {uid} exited with code {process.returncode}")
            
            # Check if process terminated abnormally
            if process.returncode != 0 and uid in active_sessions:
                socketio.emit('response', {'text': f"The assistant process exited unexpectedly. Please refresh the page."}, room=sid)
        
        except Exception as e:
            print(f"Error in client process: {e}")
            if uid in active_sessions:
                socketio.emit('response', {'text': f"Error in assistant process: {str(e)}"}, room=sid)
    
    # Create a thread to monitor the output file
    def monitor_output():
        last_position = 0
        buffer = []
        last_flush_time = time.time()
        
        while uid in active_sessions and active_sessions[uid]['active']:
            try:
                # Check if output file exists
                if not os.path.exists(output_path):
                    time.sleep(0.5)
                    continue
                
                # Read new content from output file
                with open(output_path, 'r') as f:
                    f.seek(last_position)
                    new_content = f.read()
                    last_position = f.tell()
                
                if new_content:
                    # Add new content to buffer
                    buffer.append(new_content)
                    
                    # Flush buffer to client if it contains substantial content or after a delay
                    current_time = time.time()
                    if len(''.join(buffer)) > 100 or current_time - last_flush_time > 0.5:
                        content = ''.join(buffer)
                        socketio.emit('response', {'text': content}, room=sid)
                        buffer = []
                        last_flush_time = current_time
                
                # Small delay to avoid busy waiting
                time.sleep(0.2)
                
            except Exception as e:
                print(f"Error monitoring output: {e}")
                time.sleep(1)
        
        # Flush any remaining buffer content
        if buffer:
            content = ''.join(buffer)
            socketio.emit('response', {'text': content}, room=sid)
    
    # Store session data
    active_sessions[uid] = {
        'process': None,
        'input_path': input_path,
        'output_path': output_path,
        'active': True,
        'sid': sid
    }
    
    # Start process thread
    process_thread = threading.Thread(target=run_process)
    process_thread.daemon = True
    process_thread.start()
    
    # Start output monitor thread
    monitor_thread = threading.Thread(target=monitor_output)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    # Wait for process to initialize
    time.sleep(2)
    
    return True

def run_client_query(uid, query, sid):
    """Send a query to the client process via the input file"""
    if uid not in active_sessions or not active_sessions[uid]['active']:
        print(f"Session {uid} is not active")
        return False
    
    session_data = active_sessions[uid]
    input_path = session_data['input_path']
    
    try:
        # Write query to input file with newline
        with open(input_path, 'w') as f:
            f.write(query + '\n')
        
        print(f"Wrote query to input file for session {uid}: {query}")
        return True
    except Exception as e:
        print(f"Error writing query to input file: {e}")
        return False

def cleanup_session(uid):
    """Clean up session resources"""
    if uid in active_sessions:
        print(f"Cleaning up session {uid}")
        
        session_data = active_sessions[uid]
        session_data['active'] = False
        
        # Terminate process if running
        if 'process' in session_data and session_data['process']:
            try:
                session_data['process'].terminate()
                print(f"Process terminated for session {uid}")
            except:
                pass
        
        # Remove temporary files
        for file_path in ['input_path', 'output_path']:
            if file_path in session_data and session_data[file_path]:
                try:
                    os.remove(session_data[file_path])
                    print(f"Removed {file_path} for session {uid}")
                except:
                    pass
        
        # Remove session data
        del active_sessions[uid]

if __name__ == '__main__':
    # Check if we need to create the file-based client script
    client_file_script = Path("mcp-ssms-client-file.py")
    
    if not client_file_script.exists():
        print("Creating file-based client script...")
        
        # Create a modified version of the client script that uses file I/O
        with open("mcp-ssms-client.py", "r") as source_file, open(client_file_script, "w") as target_file:
            source_code = source_file.read()
            
            # Add file paths as command line arguments
            file_io_code = """
import sys

# Get file paths from command line arguments
INPUT_FILE_PATH = sys.argv[1] if len(sys.argv) > 1 else None
OUTPUT_FILE_PATH = sys.argv[2] if len(sys.argv) > 2 else None

# Override the get_input function to read from a file
def get_input(prompt: str) -> str:
    # Print to stdout for debugging
    print(f"Waiting for input: {prompt}")
    
    # Wait for input to appear in the file
    while True:
        try:
            with open(INPUT_FILE_PATH, 'r') as f:
                content = f.read().strip()
            
            if content:
                # Clear the input file after reading
                with open(INPUT_FILE_PATH, 'w') as f:
                    pass
                
                print(f"Received input: {content}")
                return content
        except Exception as e:
            print(f"Error reading input file: {e}")
        
        time.sleep(0.5)

# Override print function to write to the output file
original_print = print
def output_print(*args, **kwargs):
    # Call the original print function
    original_print(*args, **kwargs)
    
    # Write to the output file
    try:
        message = " ".join(str(arg) for arg in args)
        with open(OUTPUT_FILE_PATH, 'a') as f:
            f.write(message + "\\n")
    except Exception as e:
        original_print(f"Error writing to output file: {e}")

# Replace the print function
print = output_print
"""
            
            # Add the custom code right after the imports
            import_end_index = source_code.find("load_dotenv()")
            if import_end_index > 0:
                modified_code = source_code[:import_end_index + len("load_dotenv()")] + "\n\n" + file_io_code + source_code[import_end_index + len("load_dotenv()"):]
                target_file.write(modified_code)
            else:
                # Fallback if we can't find import section
                target_file.write(source_code)
                target_file.write("\n\n" + file_io_code)
    
    # Check if templates directory exists, if not create it
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    if not os.path.exists('static'):
        os.makedirs('static')
        os.makedirs('static/css', exist_ok=True)
        os.makedirs('static/js', exist_ok=True)
    
    # Get port from environment or use default
    port = int(os.getenv('WEB_PORT', 5000))
    
    # Display table we're working with
    table_schema = os.getenv("MSSQL_TABLE_SCHEMA", "dbo")
    table_name = os.getenv("MSSQL_TABLE_NAME", "your_table_name")
    fully_qualified_table_name = f"{table_schema}.{table_name}" if table_schema else table_name
    
    print(f"Starting SQL Server Table Assistant Web Interface (MCP Mode) on port {port}")
    print(f"Connected to table: {fully_qualified_table_name}")
    print(f"Allowed IPs: {', '.join(ALLOWED_IPS)}")
    print(f"Access the interface at http://localhost:{port} (or your server IP)")
    
    # Start the web server
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True) 