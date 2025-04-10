import os
import sys
import json
import asyncio
import subprocess
import threading
import io
import time
from threading import Thread, Lock
from queue import Queue
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

# Dictionary to store active assistant processes
active_sessions = {}

# Lock for thread-safe access to active_sessions
sessions_lock = Lock()

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
        with sessions_lock:
            if uid in active_sessions:
                # Terminate the assistant process
                session_data = active_sessions[uid]
                if session_data['process']:
                    try:
                        session_data['process'].terminate()
                        print(f"Process terminated for session {uid}")
                    except Exception as e:
                        print(f"Error terminating process: {e}")
                    
                # Mark this session for cleanup
                session_data['active'] = False
                
                print(f"Client disconnected: {uid}")

@socketio.on('query')
def handle_query(data):
    """Handle user query and route to SQL Table Assistant"""
    if 'uid' not in session:
        socketio.emit('response', {'text': 'Session error. Please refresh the page.'}, room=request.sid)
        return
    
    uid = session['uid']
    query = data.get('query', '').strip()
    
    if not query:
        return
    
    # Acquire lock for thread-safe access
    with sessions_lock:
        # Start assistant process if not already running
        if uid not in active_sessions or not is_process_alive(uid):
            # Start a new assistant process
            try:
                active_sessions[uid] = start_assistant_process(uid, request.sid)
                # Send initial debug message to client
                socketio.emit('response', {'text': 'Assistant process started. Processing query...'}, room=request.sid)
            except Exception as e:
                error_msg = f"Error starting assistant process: {str(e)}"
                print(error_msg)
                socketio.emit('response', {'text': error_msg}, room=request.sid)
                return
        
        # Get session data
        session_data = active_sessions[uid]
    
    # Check if process is alive
    if not is_process_alive(uid):
        socketio.emit('response', {'text': 'Assistant process is not running. Please refresh the page.'}, room=request.sid)
        return
    
    # Debug message
    print(f"Attempting to send query to assistant: {query}")
    
    # Send the query to the assistant process
    try:
        send_to_assistant(uid, query)
        # Send intermediate message to client
        socketio.emit('response', {'text': 'Query sent to assistant. Waiting for response...'}, room=request.sid)
    except Exception as e:
        error_msg = f"Error sending query to assistant: {str(e)}"
        print(error_msg)
        
        # Try to restart the process
        with sessions_lock:
            if uid in active_sessions:
                try:
                    session_data = active_sessions[uid]
                    if session_data['process']:
                        try:
                            session_data['process'].terminate()
                        except:
                            pass
                    
                    # Start a new process
                    active_sessions[uid] = start_assistant_process(uid, request.sid)
                    socketio.emit('response', {'text': 'Restarting assistant process. Please try your query again.'}, room=request.sid)
                except Exception as restart_error:
                    socketio.emit('response', {'text': f'Failed to restart assistant: {restart_error}. Please refresh the page.'}, room=request.sid)

def is_process_alive(uid):
    """Check if the process for a session is alive and healthy"""
    with sessions_lock:
        if uid not in active_sessions:
            return False
        
        session_data = active_sessions[uid]
        if not session_data['active']:
            return False
        
        process = session_data['process']
        if process is None:
            return False
        
        # Check if process is still running
        try:
            # poll() returns None if process is running, otherwise return code
            if process.poll() is None:
                # Process is still running
                return True
            else:
                # Process has exited
                print(f"Process for session {uid} has exited with code {process.poll()}")
                return False
        except Exception:
            return False

def start_assistant_process(uid, sid):
    """Start a new assistant process for this session"""
    print(f"Starting assistant process for session {uid}")
    
    # Set up command to run the MCP client with explicit arguments
    cmd = [sys.executable, "mcp-ssms-client.py"]
    
    # Launch the process with proper encoding and buffering
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # Unbuffered
            universal_newlines=True,  # Text mode with universal newlines
            shell=False,
            env=os.environ.copy()  # Pass current environment
        )
        
        # Check if process started correctly
        if process.poll() is not None:
            # Process failed to start or terminated immediately
            error_msg = f"Process exited immediately with code {process.poll()}"
            print(error_msg)
            raise Exception(error_msg)
    
    except Exception as e:
        print(f"Failed to start assistant process: {e}")
        raise
    
    # Create a queue for messages
    message_queue = Queue()
    
    # Session data
    session_data = {
        'process': process,
        'message_queue': message_queue,
        'active': True,
        'sid': sid,
        'last_response': '',
        'output_buffer': [],
        'expecting_input': False,
        'stdin_lock': Lock(),  # Add a lock for stdin access
        'initialization_complete': False
    }
    
    # Start the thread for reading assistant output
    stdout_thread = Thread(target=read_assistant_output, args=(uid, process.stdout, message_queue))
    stdout_thread.daemon = True
    stdout_thread.start()
    
    # Start the thread for processing messages
    process_thread = Thread(target=process_messages, args=(uid, sid))
    process_thread.daemon = True
    process_thread.start()
    
    # Wait for process to initialize and check if it's responding
    initialization_timeout = 5  # seconds
    start_time = time.time()
    initialization_success = False
    
    # Read initial output to confirm process is running properly
    while time.time() - start_time < initialization_timeout:
        time.sleep(0.5)
        
        with sessions_lock:
            if uid not in active_sessions:
                break
                
            # If we've collected any output or detected an input prompt, consider initialization successful
            if not message_queue.empty() or session_data.get('initial_output'):
                initialization_success = True
                session_data['initialization_complete'] = True
                break
                
            # Check if process is still running
            if process.poll() is not None:
                error_msg = f"Process terminated during initialization with code {process.poll()}"
                print(error_msg)
                raise Exception(error_msg)
    
    if not initialization_success:
        error_msg = "Timeout waiting for assistant process to initialize"
        print(error_msg)
        process.terminate()
        raise Exception(error_msg)
    
    # Process any initial output from the assistant
    process_initial_output(uid)
    
    # Send a test ping to verify stdin is working
    try:
        with session_data['stdin_lock']:
            # Just try to write a newline - harmless but verifies the pipe is working
            process.stdin.write("\n")
            process.stdin.flush()
    except Exception as e:
        print(f"Error testing stdin: {e}")
        process.terminate()
        raise Exception(f"Process started but stdin communication failed: {e}")
    
    print(f"Assistant process started successfully for session {uid}")
    return session_data

def process_initial_output(uid):
    """Process any initial output from the assistant process startup"""
    with sessions_lock:
        if uid not in active_sessions:
            return
            
        session_data = active_sessions[uid]
        message_queue = session_data['message_queue']
        
        # Process any messages already in the queue
        output_buffer = []
        
        # Non-blocking loop to collect initial messages
        while not message_queue.empty():
            try:
                message = message_queue.get_nowait()
                message_queue.task_done()
                
                # Skip special markers for initial output
                if message not in ["__EOF__", "__EXPECTING_INPUT__"]:
                    output_buffer.append(message)
            except:
                break
        
        # Store initial output in session data
        if output_buffer:
            session_data['initial_output'] = '\n'.join(output_buffer)
            print(f"Collected initial output ({len(output_buffer)} lines)")

def send_to_assistant(uid, query):
    """Send query to the assistant process"""
    # Prepare the query by ensuring it has no trailing whitespace and ends with a newline
    query = query.strip()
    if not query:
        return
        
    query_with_newline = query + "\n"
    
    with sessions_lock:
        if uid not in active_sessions:
            raise Exception("Session not found")
            
        session_data = active_sessions[uid]
        process = session_data['process']
        
        if process is None:
            raise Exception("Process is not initialized")
            
        if process.poll() is not None:
            raise Exception(f"Process has terminated with code {process.poll()}")
            
        if process.stdin is None or process.stdin.closed:
            raise Exception("Process stdin is closed")
        
        # Check if initialization was successful
        if not session_data.get('initialization_complete', False):
            raise Exception("Process initialization was not completed")
    
    # Use a lock to ensure only one thread writes to stdin at a time
    with session_data['stdin_lock']:
        try:
            # Write the query to the process stdin
            process.stdin.write(query_with_newline)
            process.stdin.flush()
            print(f"Successfully sent query to process: {query}")
            
            # Verify process is still running after write
            if process.poll() is not None:
                raise Exception(f"Process terminated after write with code {process.poll()}")
                
            return True
        except BrokenPipeError:
            print(f"Broken pipe when writing to stdin")
            with sessions_lock:
                if uid in active_sessions:
                    active_sessions[uid]['active'] = False
            raise Exception("Connection to assistant process was lost (broken pipe)")
        except Exception as e:
            print(f"Error writing to stdin: {e}")
            # Mark process as inactive to trigger restart on next query
            with sessions_lock:
                if uid in active_sessions:
                    active_sessions[uid]['active'] = False
            raise

def read_assistant_output(uid, stdout, message_queue):
    """Read and process output from the assistant process"""
    ready_patterns = [
        "Enter your Query", 
        "Do you want to", 
        "Enter your feedback", 
        "Press Enter to exit",
        "Type your questions",
        "Table Assistant is ready",
        "special commands:"
    ]
    
    try:
        # Set initial line counter to detect startup
        line_count = 0
        detected_ready = False
        
        # Read continuous output from the process
        for line in iter(stdout.readline, ''):
            line_count += 1
            
            with sessions_lock:
                if uid not in active_sessions or not active_sessions[uid]['active']:
                    break
            
            # Strip the line and check if empty
            stripped_line = line.rstrip()
            if not stripped_line:
                continue
                
            # Debugging
            print(f"Output from assistant [{line_count}]: {stripped_line}")
            
            # Add the line to the message queue
            message_queue.put(stripped_line)
            
            # Check for patterns that indicate the assistant is ready for input
            lower_line = stripped_line.lower()
            if any(pattern.lower() in lower_line for pattern in ready_patterns):
                message_queue.put("__EXPECTING_INPUT__")
                detected_ready = True
                print(f"Detected input prompt: {stripped_line}")
                
                # Mark initialization as complete when we detect first prompt
                with sessions_lock:
                    if uid in active_sessions:
                        active_sessions[uid]['initialization_complete'] = True
            
            # Special handling for initial output - if we get a reasonable number of 
            # lines but no prompt detected, force an input signal
            if line_count > 10 and not detected_ready:
                with sessions_lock:
                    if uid in active_sessions:
                        # Check if initialization is already marked complete
                        if not active_sessions[uid].get('initialization_complete', False):
                            print("Reached line threshold without prompt, assuming ready for input")
                            active_sessions[uid]['initialization_complete'] = True
                            detected_ready = True
                            message_queue.put("__EXPECTING_INPUT__")
                
    except Exception as e:
        error_msg = f"Error reading assistant output for session {uid}: {str(e)}"
        print(error_msg)
        with sessions_lock:
            if uid in active_sessions and active_sessions[uid]['active']:
                message_queue.put(f"Error reading assistant output: {str(e)}")
    finally:
        # Mark end of output
        message_queue.put("__EOF__")
        print(f"Reached EOF for assistant output (read {line_count} lines)")
        
        # Cleanup
        with sessions_lock:
            if uid in active_sessions:
                active_sessions[uid]['active'] = False

def process_messages(uid, sid):
    """Process messages from the queue and send to client"""
    with sessions_lock:
        if uid not in active_sessions:
            return
            
        session_data = active_sessions[uid]
        message_queue = session_data['message_queue']
    
    output_buffer = []
    send_timer = None
    
    def send_buffered_output():
        """Helper to send the current buffer to the client"""
        nonlocal output_buffer
        if output_buffer:
            response_text = '\n'.join(output_buffer)
            
            with sessions_lock:
                if uid in active_sessions:
                    active_sessions[uid]['last_response'] = response_text
            
            socketio.emit('response', {'text': response_text}, room=sid)
            print(f"Sent {len(output_buffer)} lines to client")
            output_buffer = []
    
    while True:
        # Check if session is still active
        with sessions_lock:
            if uid not in active_sessions or not active_sessions[uid]['active']:
                break
        
        try:
            # Get message from queue (wait up to 0.5 seconds)
            try:
                message = message_queue.get(timeout=0.5)
                message_queue.task_done()
            except:
                # No new messages, but flush any existing messages in buffer after delay
                if output_buffer and len(output_buffer) > 0:
                    send_buffered_output()
                continue
                
            # Check for special markers
            if message == "__EOF__":
                # End of process output
                send_buffered_output()
                break
            elif message == "__EXPECTING_INPUT__":
                # Assistant is waiting for input, send buffer to client immediately
                send_buffered_output()
            else:
                # Normal message, add to buffer
                output_buffer.append(message)
                
                # Send immediately if buffer gets large enough
                if len(output_buffer) >= 5:
                    send_buffered_output()
                # Or schedule a send after a short delay for responsiveness
                elif len(output_buffer) == 1:
                    if send_timer:
                        send_timer.cancel()
                    def delayed_send():
                        if output_buffer:  # Check if still has content
                            send_buffered_output()
                    send_timer = threading.Timer(0.8, delayed_send)
                    send_timer.start()
        except Exception as e:
            print(f"Error processing messages for session {uid}: {str(e)}")
            
            with sessions_lock:
                if uid in active_sessions and active_sessions[uid]['active']:
                    socketio.emit('response', {'text': f"Error processing assistant response: {str(e)}"}, room=sid)
    
    # Final cleanup
    with sessions_lock:
        if uid in active_sessions:
            active_sessions[uid]['active'] = False
            print(f"Message processing ended for session {uid}")

if __name__ == '__main__':
    # Check if templates directory exists, if not create it
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    if not os.path.exists('static'):
        os.makedirs('static')
        os.makedirs('static/css', exist_ok=True)
        os.makedirs('static/js', exist_ok=True)
    
    # Get port from environment or use default
    port = int(os.getenv('WEB_PORT', 5000))
    
    print(f"Starting SQL Server Table Assistant Web Interface on port {port}")
    print(f"Allowed IPs: {', '.join(ALLOWED_IPS)}")
    print(f"Access the interface at http://localhost:{port} (or your server IP)")
    
    # Start the web server
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True) 