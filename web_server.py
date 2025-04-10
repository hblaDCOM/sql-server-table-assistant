import os
import sys
import json
import asyncio
import subprocess
import threading
from threading import Thread
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
    if 'uid' in session and session['uid'] in active_sessions:
        # Terminate the assistant process
        session_data = active_sessions[session['uid']]
        if session_data['process']:
            try:
                session_data['process'].terminate()
                print(f"Process terminated for session {session['uid']}")
            except Exception as e:
                print(f"Error terminating process: {e}")
                
        # Mark this session for cleanup
        session_data['active'] = False
        
        print(f"Client disconnected: {session['uid']}")

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
    
    # Start assistant process if not already running
    if uid not in active_sessions:
        # Start a new assistant process
        try:
            active_sessions[uid] = start_assistant_process(uid, request.sid)
        except Exception as e:
            error_msg = f"Error starting assistant process: {str(e)}"
            print(error_msg)
            socketio.emit('response', {'text': error_msg}, room=request.sid)
            return
    
    # Send query to assistant process
    session_data = active_sessions[uid]
    
    if not session_data['process'] or not session_data['active']:
        socketio.emit('response', {'text': 'Assistant process is not running. Please refresh the page.'}, room=request.sid)
        return
    
    # Send the query to the assistant process
    try:
        send_to_assistant(uid, query)
    except Exception as e:
        error_msg = f"Error sending query to assistant: {str(e)}"
        print(error_msg)
        socketio.emit('response', {'text': error_msg}, room=request.sid)

def start_assistant_process(uid, sid):
    """Start a new assistant process for this session"""
    print(f"Starting assistant process for session {uid}")
    
    # Set up command to run the MCP client
    cmd = ["python", "mcp-ssms-client.py"]
    
    # Launch the process
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # Line buffered
        shell=False
    )
    
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
        'expecting_input': False
    }
    
    # Start the thread for reading assistant output
    stdout_thread = Thread(target=read_assistant_output, args=(uid, process.stdout, message_queue))
    stdout_thread.daemon = True
    stdout_thread.start()
    
    # Start the thread for processing messages
    process_thread = Thread(target=process_messages, args=(uid, sid))
    process_thread.daemon = True
    process_thread.start()
    
    return session_data

def send_to_assistant(uid, query):
    """Send query to the assistant process"""
    session_data = active_sessions[uid]
    process = session_data['process']
    
    # Write the query to the process stdin
    process.stdin.write(query + '\n')
    process.stdin.flush()
    print(f"Sent query to assistant process: {query}")

def read_assistant_output(uid, stdout, message_queue):
    """Read and process output from the assistant process"""
    try:
        # Read continuous output from the process
        for line in iter(stdout.readline, ''):
            if not active_sessions.get(uid, {}).get('active', False):
                break
                
            # Add the line to the message queue
            message_queue.put(line.rstrip())
            
            # Check for patterns that indicate we're expecting input
            if "Enter your Query" in line or "Do you want to" in line or "Enter your feedback" in line:
                message_queue.put("__EXPECTING_INPUT__")
    except Exception as e:
        if active_sessions.get(uid, {}).get('active', False):
            print(f"Error reading assistant output for session {uid}: {str(e)}")
            message_queue.put(f"Error reading assistant output: {str(e)}")
    finally:
        # Mark end of output
        message_queue.put("__EOF__")
        
        # Cleanup
        if uid in active_sessions:
            active_sessions[uid]['active'] = False

def process_messages(uid, sid):
    """Process messages from the queue and send to client"""
    if uid not in active_sessions:
        return
        
    session_data = active_sessions[uid]
    message_queue = session_data['message_queue']
    output_buffer = []
    
    while active_sessions.get(uid, {}).get('active', False):
        try:
            # Get message from queue (wait up to 0.1 seconds)
            try:
                message = message_queue.get(timeout=0.1)
            except:
                continue
                
            # Check for special markers
            if message == "__EOF__":
                # End of process output
                if output_buffer:
                    response_text = '\n'.join(output_buffer)
                    socketio.emit('response', {'text': response_text}, room=sid)
                break
            elif message == "__EXPECTING_INPUT__":
                # Assistant is waiting for input, send buffer to client
                if output_buffer:
                    response_text = '\n'.join(output_buffer)
                    session_data['last_response'] = response_text
                    socketio.emit('response', {'text': response_text}, room=sid)
                    output_buffer = []
            else:
                # Normal message, add to buffer
                output_buffer.append(message)
                
                # If buffer gets too large, send it immediately
                if len(output_buffer) > 20:
                    response_text = '\n'.join(output_buffer)
                    session_data['last_response'] = response_text
                    socketio.emit('response', {'text': response_text}, room=sid)
                    output_buffer = []
        except Exception as e:
            print(f"Error processing messages for session {uid}: {str(e)}")
            
            if active_sessions.get(uid, {}).get('active', False):
                socketio.emit('response', {'text': f"Error processing assistant response: {str(e)}"}, room=sid)
    
    # Final cleanup
    if uid in active_sessions:
        active_sessions[uid]['active'] = False

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
    print("Access the interface at http://localhost:{port} (or your server IP)")
    
    # Start the web server
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True) 