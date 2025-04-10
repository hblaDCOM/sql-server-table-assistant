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
import logging

# Load environment variables
load_dotenv()

# Temp directory for IPC files
TEMP_DIR = os.path.join(tempfile.gettempdir(), "sql_assistant")
os.makedirs(TEMP_DIR, exist_ok=True)  # Create the directory if it doesn't exist

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
    uid = session['uid']
    print(f"Client connected: {uid}")
    
    # Start a new client process immediately
    if uid not in active_sessions:
        start_client_process(uid, request.sid)
        # Send initial greeting to client
        socketio.emit('response', {'text': 'Initializing the SQL Table Assistant...\n'}, room=request.sid)

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
    
    # Check if the client process is running
    if uid not in active_sessions:
        # Process not started yet (should not happen normally since we start on connect)
        socketio.emit('response', {'text': 'Initializing the SQL Table Assistant...\n'}, room=request.sid)
        start_client_process(uid, request.sid)
    elif not is_process_alive(uid):
        # Process died, restart it
        socketio.emit('response', {'text': 'Restarting the SQL Table Assistant...\n'}, room=request.sid)
        cleanup_session(uid)
        start_client_process(uid, request.sid)
    
    # Run the query
    status = run_client_query(uid, query, request.sid)
    if not status:
        socketio.emit('response', {'text': 'Error processing query. The assistant may need to be restarted.'}, room=request.sid)

def start_client_process(uid, sid):
    """Start a new client process with input/output files for IPC"""
    print(f"Starting new client process for session {uid}")
    
    # Create temporary files in our managed directory
    input_path = os.path.join(TEMP_DIR, f'input_{uid}.txt')
    output_path = os.path.join(TEMP_DIR, f'output_{uid}.txt')
    
    # Initialize the files
    try:
        with open(input_path, 'w') as f:
            f.write("")  # Empty the input file
            
        with open(output_path, 'w') as f:
            f.write("")  # Empty the output file
    except Exception as e:
        print(f"Error initializing IPC files: {e}")
        socketio.emit('response', {'text': f"Error starting SQL Assistant: {str(e)}"}, room=sid)
        return False
    
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
            
            # Notify user that process has started
            socketio.emit('response', {'text': 'SQL Table Assistant process started. Initializing...\n'}, room=sid)
            
            # Monitor process stdout for debugging
            for line in iter(process.stdout.readline, ''):
                line_text = line.strip()
                print(f"Process output [{uid}]: {line_text}")
                
                # Send progress updates for key initialization steps
                if "Fetching schema" in line_text:
                    socketio.emit('response', {'text': 'Retrieving your table schema...\n'}, room=sid)
                elif "Schema information fetched successfully" in line_text:
                    socketio.emit('response', {'text': 'Schema retrieved successfully!\n'}, room=sid)
                elif "Table Assistant is ready" in line_text:
                    socketio.emit('response', {'text': 'SQL Table Assistant is ready for your queries.\n'}, room=sid)
            
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
        last_check_time = time.time()
        start_time = time.time()
        initial_output_seen = False
        
        # Initialize the output file with empty content
        try:
            with open(output_path, 'w') as f:
                f.write("")
        except Exception as e:
            print(f"Error initializing output file: {e}")
        
        # Give the client process more time to start and print its initial output
        # This helps ensure we capture the schema fetching and welcome message
        time.sleep(4)
        
        while uid in active_sessions and active_sessions[uid]['active']:
            try:
                current_time = time.time()
                
                # Check if output file exists
                if not os.path.exists(output_path):
                    time.sleep(0.5)
                    continue
                
                # Read new content from output file
                try:
                    with open(output_path, 'r', encoding='utf-8') as f:
                        # Get file size
                        f.seek(0, os.SEEK_END)
                        file_size = f.tell()
                        
                        if file_size < last_position:
                            # File was truncated
                            last_position = 0
                        
                        # If this is initial startup and file has content, read from beginning
                        if not initial_output_seen and file_size > 0:
                            f.seek(0)
                        else:
                            # Go to last position
                            f.seek(last_position)
                            
                        new_content = f.read()
                        last_position = f.tell()
                except Exception as read_error:
                    print(f"Error reading output file: {read_error}")
                    time.sleep(1)
                    continue
                
                # Process only if we have new content
                if new_content:
                    # Mark that we've seen some output
                    initial_output_seen = True
                    
                    print(f"Read new content from output file: {len(new_content)} bytes")
                    # Add new content to buffer
                    buffer.append(new_content)
                    last_check_time = current_time
                    
                    # Flush buffer to client if it contains substantial content or after a delay
                    if len(''.join(buffer)) > 30 or current_time - last_flush_time > 0.3:
                        content = ''.join(buffer)
                        socketio.emit('response', {'text': content}, room=sid)
                        print(f"Sent {len(content)} characters to client")
                        buffer = []
                        last_flush_time = current_time
                else:
                    # Special handling for startup - if no content after 15 seconds, show a message
                    if not initial_output_seen and current_time - start_time > 15:
                        msg = "SQL Table Assistant is taking longer than expected to start. Please wait...\n"
                        socketio.emit('response', {'text': msg}, room=sid)
                        initial_output_seen = True  # Mark as seen to avoid repeat messages
                    
                    # No new content, check if we should flush buffer due to time
                    if buffer and current_time - last_flush_time > 1.0:
                        content = ''.join(buffer)
                        socketio.emit('response', {'text': content}, room=sid)
                        print(f"Timeout flush: sent {len(content)} characters to client")
                        buffer = []
                        last_flush_time = current_time
                    
                    # Check if we've had no content for a while - send ping message
                    if current_time - last_check_time > 10:
                        print(f"No content for {int(current_time - last_check_time)} seconds, checking if process is alive")
                        if not is_process_alive(uid):
                            print(f"Process for session {uid} is no longer alive")
                            socketio.emit('response', {'text': "The assistant process has stopped. Please refresh the page to restart."}, room=sid)
                            break
                        last_check_time = current_time
                
                # Small delay to avoid busy waiting
                time.sleep(0.2)
                
            except Exception as e:
                print(f"Error monitoring output: {e}")
                time.sleep(1)
        
        # Flush any remaining buffer content
        if buffer:
            content = ''.join(buffer)
            socketio.emit('response', {'text': content}, room=sid)
            print(f"Final flush: sent {len(content)} characters to client")
    
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
    
    # Start heartbeat thread
    heartbeat_thread = start_heartbeat(uid, input_path)
    
    # Wait for process to initialize
    print(f"Waiting for client process {uid} to initialize")
    time.sleep(3)  # Increased wait time
    
    # Log that the process started successfully
    if is_process_alive(uid):
        print(f"Client process {uid} started successfully")
    else:
        print(f"Warning: Client process {uid} may not have started properly")
    
    return True

def run_client_query(uid, query, sid):
    """Send a query to the client process via the input file"""
    if uid not in active_sessions or not active_sessions[uid]['active']:
        print(f"Session {uid} is not active")
        return False
    
    session_data = active_sessions[uid]
    input_path = session_data['input_path']
    
    try:
        # Check if process is still alive
        if not is_process_alive(uid):
            print(f"Client process for session {uid} is not running")
            return False
            
        # Write query to input file with newline
        with open(input_path, 'w') as f:
            f.write(query + '\n')
            f.flush()  # Make sure it's written immediately
            os.fsync(f.fileno())  # Force write to disk
        
        # Verify file was written
        try:
            with open(input_path, 'r') as f:
                content = f.read().strip()
                if not content:
                    print(f"Warning: Input file appears empty after writing query for session {uid}")
        except Exception as verify_err:
            print(f"Error verifying input file write: {verify_err}")
        
        print(f"Wrote query to input file for session {uid}: {query}")
        
        # Notify user we're processing their query
        socketio.emit('response', {'text': f"\nProcessing: {query}\n"}, room=sid)
        
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

def is_process_alive(session_id):
    """Check if a process for a given session is still alive."""
    if session_id not in active_sessions or 'process' not in active_sessions[session_id]:
        return False
        
    process = active_sessions[session_id]['process']
    if process is None:
        return False
        
    # Check if process is still running
    return process.poll() is None

def read_assistant_output(uid, ws):
    """Read assistant output file and send contents to client."""
    if uid not in active_sessions:
        return
        
    output_path = active_sessions[uid]['output_path']
    
    # Initialize the file with empty content to avoid reading errors
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('')
            f.flush()
    except Exception as e:
        logging.error(f"Error initializing output file: {e}")
    
    # Give the client process a moment to start
    time.sleep(1)
    
    buffer = []
    last_flush_time = time.time()
    last_position = 0
    
    while uid in active_sessions and active_sessions[uid]['active']:
        if not is_process_alive(uid):
            # Process has ended, send any remaining output
            if buffer:
                ws.send(json.dumps({"type": "assistant", "content": ''.join(buffer)}))
            ws.send(json.dumps({"type": "status", "content": "Assistant process has ended"}))
            break
            
        try:
            if not os.path.exists(output_path):
                time.sleep(0.5)
                continue
                
            with open(output_path, 'r', encoding='utf-8') as f:
                # Check if the file has been truncated
                file_size = os.path.getsize(output_path)
                if file_size < last_position:
                    # File was truncated, reset position
                    last_position = 0
                    
                f.seek(last_position)
                new_content = f.read()
                
                if new_content:
                    buffer.append(new_content)
                    last_position = f.tell()
                    
                    # Flush if enough content or enough time has passed
                    current_time = time.time()
                    if len(''.join(buffer)) > 100 or (current_time - last_flush_time) > 0.5:
                        ws.send(json.dumps({"type": "assistant", "content": ''.join(buffer)}))
                        buffer = []
                        last_flush_time = current_time
                        
        except Exception as e:
            logging.error(f"Error reading output file: {e}")
                
        # Small delay to prevent high CPU usage
        time.sleep(0.1)
    
    # Final flush of any remaining content
    if buffer:
        ws.send(json.dumps({"type": "assistant", "content": ''.join(buffer)}))

def start_heartbeat(uid, input_path):
    """Start a heartbeat thread to keep the client process responsive"""
    
    def send_heartbeat():
        while uid in active_sessions and active_sessions[uid]['active']:
            try:
                if is_process_alive(uid):
                    # Check if the client is waiting for input
                    with open(input_path, 'r') as f:
                        content = f.read().strip()
                    
                    # Only send heartbeat if the input file is empty
                    if not content:
                        # Write a special heartbeat to the input file every 30 seconds
                        with open(input_path, 'w') as f:
                            f.write("__HEARTBEAT__\n")
                            f.flush()
                        print(f"Sent heartbeat to process {uid}")
            except Exception as e:
                print(f"Error in heartbeat thread: {e}")
            
            # Wait 30 seconds before next heartbeat
            time.sleep(30)
    
    # Start heartbeat thread
    heartbeat_thread = threading.Thread(target=send_heartbeat)
    heartbeat_thread.daemon = True
    heartbeat_thread.start()
    
    return heartbeat_thread

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
    sys.stdout.flush()  # Ensure the prompt is output immediately
    
    # Write a marker to the output file to indicate we're waiting for input
    try:
        with open(OUTPUT_FILE_PATH, 'a') as f:
            f.write(f"Waiting for your input: {prompt}\\n")
            f.flush()
    except Exception as e:
        original_print(f"Error writing prompt to output file: {e}")
    
    # Wait for input to appear in the file
    attempts = 0
    while True:
        try:
            # Check if the input file exists
            if not os.path.exists(INPUT_FILE_PATH):
                time.sleep(0.5)
                continue
                
            # Read content from the file
            with open(INPUT_FILE_PATH, 'r') as f:
                content = f.read().strip()
            
            # Process if we have content
            if content:
                # Handle heartbeat messages
                if content == "__HEARTBEAT__":
                    # Clear the input file
                    with open(INPUT_FILE_PATH, 'w') as f:
                        pass
                    # Skip this cycle
                    time.sleep(0.1)
                    continue
                
                print(f"Received input: {content}")
                
                # Clear the input file after reading
                try:
                    with open(INPUT_FILE_PATH, 'w') as f:
                        pass
                except Exception as clear_err:
                    original_print(f"Error clearing input file: {clear_err}")
                
                # Write acknowledgment to output
                try:
                    with open(OUTPUT_FILE_PATH, 'a') as f:
                        f.write(f"Processing your query: {content}\\n")
                        f.flush()
                except Exception as ack_err:
                    original_print(f"Error writing acknowledgment: {ack_err}")
                
                return content
        except Exception as e:
            print(f"Error reading input file (attempt {attempts}): {e}")
            attempts += 1
            if attempts >= 10:
                print("Too many errors reading input file, resetting...")
                attempts = 0
        
        # Check more frequently to be responsive
        time.sleep(0.2)

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
            f.flush()  # Make sure it's written immediately
    except Exception as e:
        original_print(f"Error writing to output file: {e}")

# Replace the print function
print = output_print

# Print a startup marker to indicate the process has started
print("==== SQL Table Assistant Initializing ====")
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