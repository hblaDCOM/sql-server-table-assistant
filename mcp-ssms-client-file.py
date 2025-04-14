import os
import sys
import json
import openai
import re
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from tabulate import tabulate
import threading
from dotenv import load_dotenv
import math
from typing import Dict, List, Optional, Any, Tuple
from mcp import ClientSession
from openai import AzureOpenAI
import time
import signal
import pyodbc
from math import isnan, isinf

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("client_debug.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("mcp-ssms-client")

# Load environment variables from .env file
load_dotenv()

# Constants for file-based operation
INPUT_FILE = os.environ.get("MCP_INPUT_FILE", "./mcp_input.txt")
OUTPUT_FILE = os.environ.get("MCP_OUTPUT_FILE", "./mcp_output.txt")
FLAG_FILE = os.environ.get("MCP_FLAG_FILE", "./mcp_input.txt.waiting")
LOG_FILE = os.environ.get("MCP_LOG_FILE", "./mcp_client.log")

# Global variables
running = True

# Signal handler for graceful shutdown
def signal_handler(sig, frame):
    global running
    print(f"Received signal {sig}, shutting down gracefully...")
    with open(OUTPUT_FILE, "a") as f:
        f.write("\nService is shutting down. Please restart if needed.\n")
        f.flush()
    running = False
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
if hasattr(signal, 'SIGBREAK'):  # Windows
    signal.signal(signal.SIGBREAK, signal_handler)

# Initialize OpenAI client
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2023-05-15"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

# Get the table name from environment variables or use a default
# Also check command line arguments for table name (position 3)
print(f"Received arguments: {sys.argv}")
print(f"Environment MSSQL_TABLE_SCHEMA: {os.getenv('MSSQL_TABLE_SCHEMA', 'Not set')}")
print(f"Environment MSSQL_TABLE_NAME: {os.getenv('MSSQL_TABLE_NAME', 'Not set')}")

if len(sys.argv) >= 4 and sys.argv[3] and sys.argv[3] != "None":
    FULLY_QUALIFIED_TABLE_NAME = sys.argv[3]
    print(f"Using table name from command line arg: {FULLY_QUALIFIED_TABLE_NAME}")
else:
    # Construct table name from environment variables
    table_schema = os.getenv("MSSQL_TABLE_SCHEMA", "dbo")
    table_name = os.getenv("MSSQL_TABLE_NAME", "YourTableName")
    FULLY_QUALIFIED_TABLE_NAME = f"{table_schema}.{table_name}" if table_schema else table_name
    print(f"Using table name from environment variables: {FULLY_QUALIFIED_TABLE_NAME}")

print(f"Final table name: {FULLY_QUALIFIED_TABLE_NAME}")

# Parse command line arguments for input/output files
if len(sys.argv) >= 3:
    INPUT_FILE = sys.argv[1]
    OUTPUT_FILE = sys.argv[2]
else:
    # Default files if not provided
    INPUT_FILE = os.getenv("MCP_INPUT_FILE", "input.txt")
    OUTPUT_FILE = os.getenv("MCP_OUTPUT_FILE", "output.txt")

print(f"Using input file: {INPUT_FILE}")
print(f"Using output file: {OUTPUT_FILE}")

# More robust environment variable loading
def get_env_var(name, default=None, required=False):
    """Get environment variable with validation"""
    value = os.getenv(name, default)
    if required and (value is None or value.strip() == ''):
        logger.error(f"Required environment variable {name} is not set")
        print(f"ERROR: Required environment variable {name} is not set or empty")
        # Write to output file if it exists and is writable
        try:
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\nCRITICAL ERROR: Required environment variable {name} is not set\n")
                f.write("This environment variable is required for SQL Server connection.\n")
                f.write("Please set it in your .env file or environment and restart the application.\n")
        except:
            pass
    return value

# Later in your code where you load environment variables:
MSSQL_SERVER = get_env_var("MSSQL_SERVER", "localhost", required=True)
MSSQL_DATABASE = get_env_var("MSSQL_DATABASE", required=True)
MSSQL_USERNAME = get_env_var("MSSQL_USERNAME", required=True)
MSSQL_PASSWORD = get_env_var("MSSQL_PASSWORD", required=True)
MSSQL_DRIVER = get_env_var("MSSQL_DRIVER", "{ODBC Driver 18 for SQL Server}")
MSSQL_TABLE_SCHEMA = get_env_var("MSSQL_TABLE_SCHEMA", "dbo")
MSSQL_TABLE_NAME = get_env_var("MSSQL_TABLE_NAME", required=True)
FULLY_QUALIFIED_TABLE_NAME = f"{MSSQL_TABLE_SCHEMA}.{MSSQL_TABLE_NAME}" if MSSQL_TABLE_SCHEMA else MSSQL_TABLE_NAME

# Validate SQL connection parameters
def validate_sql_connection_params():
    """Validate that all required connection parameters are present"""
    missing_params = []
    
    if not MSSQL_SERVER or MSSQL_SERVER.strip() == '':
        missing_params.append("MSSQL_SERVER")
    if not MSSQL_DATABASE or MSSQL_DATABASE.strip() == '':
        missing_params.append("MSSQL_DATABASE")
    if not MSSQL_USERNAME or MSSQL_USERNAME.strip() == '':
        missing_params.append("MSSQL_USERNAME")
    if not MSSQL_PASSWORD:
        missing_params.append("MSSQL_PASSWORD")
    if not MSSQL_DRIVER or MSSQL_DRIVER.strip() == '':
        missing_params.append("MSSQL_DRIVER")
    
    if missing_params:
        error_msg = f"Missing required SQL connection parameters: {', '.join(missing_params)}"
        logger.error(error_msg)
        print(f"ERROR: {error_msg}")
        
        # Write to output file
        try:
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\nCRITICAL ERROR: {error_msg}\n")
                f.write("These parameters are required for SQL Server connection.\n")
                f.write("Please check your .env file and ensure all required variables are set.\n")
        except:
            pass
        
        return False
    return True

# Function to read input from file - for file-based operation
def get_input(prompt=None):
    """
    Wait for input from a file instead of standard input.
    """
    if prompt:
        with open(OUTPUT_FILE, "a") as f:
            f.write(f"\n{prompt}\n")
            f.flush()
    
    global running
    # Create a flag file to indicate we're waiting for input
    try:
        with open(FLAG_FILE, "w") as f:
            f.write("1")
    except Exception as e:
        logger.error(f"Error creating flag file: {e}")
    
    # Clear the input file before waiting
    try:
        open(INPUT_FILE, "w").close()
    except Exception as e:
        logger.error(f"Error clearing input file: {e}")
    
    # Wait for input to appear in the file (max 30 minutes)
    max_attempts = 1800  # 30 minutes with 1 second sleep
    for attempt in range(max_attempts):
        if not running:
            return "exit"  # Exit gracefully if shutdown signal received
            
        try:
            with open(INPUT_FILE, "r") as f:
                content = f.read().strip()
            
            if content:
                # Check if this is a probe message
                if content.startswith("__PROBE__"):
                    logger.info(f"Received probe: {content}")
                    # Acknowledge the probe
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(f"\n__PROBE_ACK__: {content[9:]}\n")
                        f.flush()
                    
                    # Clear the input and flag files
                    try:
                        open(INPUT_FILE, "w").close()
                        if os.path.exists(FLAG_FILE):
                            os.remove(FLAG_FILE)
                    except Exception as e:
                        logger.error(f"Error clearing files after probe: {e}")
                    
                    # Continue waiting for real input
                    continue
                
                # Clear the flag file
                try:
                    if os.path.exists(FLAG_FILE):
                        os.remove(FLAG_FILE)
                except Exception as e:
                    logger.error(f"Error removing flag file: {e}")
                
                return content
        except Exception as e:
            logger.error(f"Error reading input file: {e}")
        
        time.sleep(1)
    
    # If we reach here, we timed out
    try:
        if os.path.exists(FLAG_FILE):
            os.remove(FLAG_FILE)
    except Exception as e:
        logger.error(f"Error removing flag file after timeout: {e}")
    
    return "Timed out waiting for input"

# Simple mock ClientSession for file-based operation
class FileBasedClientSession:
    """A simple mock for ClientSession that works with files."""
    
    async def initialize(self):
        """Initialize the session."""
        print("File-based session initialized.")
        # Write to output file
        with open(OUTPUT_FILE, "a") as f:
            f.write("File-based session initialized.\n")
        
    async def call_tool(self, tool_name, args):
        """Call a tool by writing to output file and waiting for response."""
        # Create a mock response class
        class MockResponse:
            def __init__(self, text):
                self.content = [type('obj', (object,), {'text': text})]
        
        # Debug output to console
        if tool_name == "query_table" and "sql" in args:
            print(f"Executing SQL query: {args['sql']}")
            
            # Write tool call to output with better formatting
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\n--- Executing SQL Query ---\n")
                f.write(f"{args['sql']}\n")
                f.write("---------------------------\n")
                f.write("WAITING_FOR_RESULT\n")
                f.flush()
        elif tool_name == "test_connection":
            print(f"Testing SQL Server connection")
            
            # Write tool call to output
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\n--- Testing SQL Server Connection ---\n")
                f.write("Attempting to connect to SQL Server to verify connectivity...\n")
                f.write("WAITING_FOR_RESULT\n")
                f.flush()
        else:
            # Write generic tool call to output
            print(f"Calling tool: {tool_name}")
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\nTOOL_CALL: {tool_name}\n")
                f.write(f"ARGS: {json.dumps(args)}\n")
                f.write("WAITING_FOR_TOOL_RESULT\n")
                f.flush()
        
        # Flag file approach - write a flag file that the web server will check
        flag_file = f"{INPUT_FILE}.waiting_tool"
        try:
            with open(flag_file, "w") as f:
                f.write(f"waiting for tool result: {tool_name}")
                f.flush()
        except Exception as e:
            print(f"Error creating tool flag file: {e}")
        
        # Wait for tool result in input file
        print(f"Waiting for result of tool call: {tool_name}")
        
        # Set up timeout handling - use a longer timeout for test_connection
        timeout_seconds = 90 if tool_name == "test_connection" else 45  # Increased timeout for connection testing
        start_time = time.time()
        last_progress_time = time.time()
        last_check_time = time.time()
        
        # For timeout tracking
        last_progress_msg = ""
        
        # First check - does input file already have a response?
        try:
            if os.path.exists(INPUT_FILE):
                with open(INPUT_FILE, "r") as f:
                    result = f.read().strip()
                
                if result and result != "__HEARTBEAT__":
                    print(f"Found immediate result for {tool_name}")
                    
                    # Clear the input file
                    with open(INPUT_FILE, "w") as f:
                        f.write("")
                        f.flush()
                    
                    # Clear the flag file
                    if os.path.exists(flag_file):
                        try:
                            os.remove(flag_file)
                        except:
                            pass
                    
                    # Process the result
                    return MockResponse(result)
        except Exception as e:
            print(f"Error checking for immediate result: {e}")
        
        # Main waiting loop
        while True:
            # Check for timeout
            current_time = time.time()
            if current_time - start_time > timeout_seconds:
                error_msg = f"Timeout waiting for tool result after {timeout_seconds} seconds"
                print(error_msg)
                
                # Clear the flag file
                if os.path.exists(flag_file):
                    try:
                        os.remove(flag_file)
                    except:
                        pass
                
                # Write timeout to output file
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\n{error_msg}\n")
                    f.write("The operation may still be processing on the server.\n")
                    f.flush()
                
                # Special handling for different tools
                if tool_name == "test_connection":
                    conn_error = "Connection failed: Timeout while waiting for SQL Server response. This typically indicates:\n"
                    conn_error += "1. Network connectivity issues or firewall blocking the connection\n"
                    conn_error += "2. SQL Server is unreachable or not running\n"
                    conn_error += "3. SQL credentials are incorrect\n"
                    conn_error += "4. The ODBC driver is incompatible or missing\n\n"
                    conn_error += "Please check SQL Server settings in .env file and verify server connectivity."
                    
                    # Write detailed error to output
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(f"\n===== SQL CONNECTION ERROR =====\n")
                        f.write(conn_error + "\n")
                        f.write("You can use the /diagnose command for more information.\n")
                        f.write("==================================\n")
                        f.flush()
                    
                    return MockResponse(conn_error)
                elif tool_name == "get_table_schema":
                    return MockResponse("Error retrieving schema: Connection timed out after 45 seconds. Please check SQL Server connectivity and that the table exists.")
                elif tool_name == "query_table":
                    return MockResponse(f"Error: SQL query execution timed out after 45 seconds. The query may be too complex or the server may be unresponsive.\nQuery: {args.get('sql', 'Unknown')}")
                else:
                    return MockResponse(f"Error: Timeout waiting for {tool_name} result")
            
            try:
                # Only check at reasonable intervals to reduce file operations
                if current_time - last_check_time < 0.3:  # Faster checks for more responsiveness
                    await asyncio.sleep(0.1)
                    continue
                
                last_check_time = current_time
                
                # Print a progress message every 5 seconds to show we're still alive
                if current_time - last_progress_time > 5:
                    elapsed = int(current_time - start_time)
                    progress_msg = f"Still waiting for {tool_name} after {elapsed} seconds..."
                    print(progress_msg)
                    
                    # Only write progress to file if different from last message
                    if progress_msg != last_progress_msg:
                        with open(OUTPUT_FILE, "a") as f:
                            f.write(f"\n{progress_msg}\n")
                            f.flush()
                        last_progress_msg = progress_msg
                        
                    last_progress_time = current_time
                
                # Check if file exists
                if not os.path.exists(INPUT_FILE):
                    continue
                
                # Read the input file
                with open(INPUT_FILE, "r") as f:
                    result = f.read().strip()
                
                # Skip if empty or heartbeat
                if not result:
                    continue
                
                # Handle heartbeat messages
                if result == "__HEARTBEAT__":
                    with open(INPUT_FILE, "w") as f:
                        f.write("")
                        f.flush()
                    continue
                
                # Process valid result
                print(f"Received result for {tool_name} tool call after {int(time.time() - start_time)} seconds")
                
                # Clear the input file
                with open(INPUT_FILE, "w") as f:
                    f.write("")
                    f.flush()
                
                # Clear the flag file
                if os.path.exists(flag_file):
                    try:
                        os.remove(flag_file)
                    except:
                        pass
                
                # Send confirmation to output
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\nReceived response for {tool_name}\n")
                    f.flush()
                
                # Return mock response with result
                return MockResponse(result)
                
            except Exception as e:
                print(f"Error waiting for tool result: {e}")
                
                # Brief pause on errors
                await asyncio.sleep(0.5)
                
                # Only fail after multiple retries
                if time.time() - start_time > 10:  # Give at least 10 seconds before giving up
                    # Clear the flag file
                    if os.path.exists(flag_file):
                        try:
                            os.remove(flag_file)
                        except:
                            pass
                    
                    # Special handling for different tools
                    if tool_name == "test_connection":
                        return MockResponse(f"Connection failed: An error occurred: {str(e)}")
                    elif tool_name == "get_table_schema":
                        return MockResponse(f"Error retrieving schema: {str(e)}")
                    elif tool_name == "query_table":
                        return MockResponse(f"Error: SQL query execution failed: {str(e)}\nQuery: {args.get('sql', 'Unknown')}")
                    else:
                        return MockResponse(f"Error: {str(e)}")

@dataclass
class QueryIteration:
    """Store information about each iteration of SQL query generation."""
    iteration_number: int
    natural_language_query: str
    generated_sql: str
    feedback: Optional[str] = None
    executed: bool = False
    results: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class Chat:
    messages: list[dict] = field(default_factory=list)
    table_schema: str = ""
    schema_summary: str = ""  # Add a more concise schema summary
    current_query_iterations: List[QueryIteration] = field(default_factory=list)
    query_history: List[Dict[str, Any]] = field(default_factory=list)
    response_cache: Dict[str, Any] = field(default_factory=dict)  # Cache for model responses
    
    # Minimal system prompt for initial schema retrieval
    schema_system_prompt: str = (
        "You are an assistant that creates SQL queries for table {table_name}. "
        "Examine the schema and create a concise summary highlighting the most important aspects. "
        "Focus on key columns, data types, relationships, and typical query patterns."
    )
    
    # More focused system prompt for query generation
    system_prompt: str = (
        "You are an AI assistant that helps users query and interact with the {table_name} table in SQL Server.\n\n"
        "You only have access to this specific table, not the entire database.\n\n"
        "CONTEXT ABOUT THE TABLE:\n"
        "{schema_summary}\n\n"
        "IMPORTANT INSTRUCTIONS:\n"
        "1. Always generate standard SQL Server T-SQL syntax\n"
        "2. Reference columns EXACTLY as they appear in the schema\n"
        "3. For any data modification, ask for user confirmation before executing\n"
        "4. You can provide sample queries to help the user understand the table\n"
        "5. When users ask complex questions, break down the approach\n"
        "6. Inform users if a requested operation isn't possible with the table's structure\n"
        "7. You can use the get_recent_query_logs tool to retrieve summaries of recent SQL queries and their results\n\n"
        "COMMANDS:\n"
        "- To run diagnostics on table access: /diagnose\n"
        "- To view recent query logs: /show-logs [number]\n"
        "- To refresh table schema: /refresh_schema\n"
        "- To view query history: /history\n\n"
        "Format: TOOL: query_table, ARGS: {{\"sql\": \"<SQL_QUERY>\"}}"
    )
    
    # Minimal system prompt for result explanation
    explanation_system_prompt: str = (
        "You are a data analyst explaining SQL query results in plain language. "
        "Be brief and focus on key insights from the data."
    )

    async def create_schema_summary(self, full_schema: str) -> str:
        """Create a concise summary of the schema for use in the system prompt."""
        # Check if we can parse it ourselves first
        try:
            lines = full_schema.split('\n')
            table_name = ""
            columns = []
            primary_key = ""
            
            # Extract basic schema elements
            for line in lines:
                if line.startswith("Table:"):
                    table_name = line.split("Table:")[1].strip()
                elif ":" not in line and line.strip().startswith("- ") and ":" in line:
                    columns.append(line.strip()[2:])  # Remove "- " prefix
                elif line.startswith("Primary Key:"):
                    primary_key = line.split("Primary Key:")[1].strip()
            
            if table_name and columns:
                summary = f"Table: {table_name}\nColumns: {', '.join(columns[:10])}"
                if len(columns) > 10:
                    summary += f" plus {len(columns) - 10} more"
                if primary_key:
                    summary += f"\nPrimary Key: {primary_key}"
                return summary
        except Exception:
            pass
        
        # If parsing fails, use the model (but with minimal token usage)
        cache_key = f"schema_summary:{hash(full_schema)}"
        if cache_key in self.response_cache:
            return self.response_cache[cache_key]
        
        try:
            # Only send a concise version of the schema
            schema_preview = "\n".join(full_schema.split('\n')[:50])
            if len(full_schema.split('\n')) > 50:
                schema_preview += "\n... (additional schema details omitted)"
            
            prompt = f"Create a concise summary of this database schema, highlighting only the most important columns and relationships:\n\n{schema_preview}"
            
            completion_params = {
                "messages": [
                    {"role": "system", "content": self.schema_system_prompt.format(table_name=FULLY_QUALIFIED_TABLE_NAME)},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 500,
                "temperature": 0.0,
                "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")
            }
            
            completion = client.chat.completions.create(**completion_params)
            summary = completion.choices[0].message.content.strip()
            
            # Cache the result
            self.response_cache[cache_key] = summary
            return summary
        except Exception as e:
            print(f"Warning: Could not create schema summary: {e}")
            return "Schema available but summarization failed. Refer to full table name and use column names exactly as they appear."

    async def fetch_schema(self, session: ClientSession) -> None:
        """Fetch the table schema and update the system prompt."""
        print(f"Fetching schema for table {FULLY_QUALIFIED_TABLE_NAME}...")
        schema_error = False
        basic_info = ""
        
        # Write initial status to output file
        with open(OUTPUT_FILE, "a") as f:
            f.write(f"\n===== FETCHING SCHEMA FOR {FULLY_QUALIFIED_TABLE_NAME} =====\n")
            f.write(f"SQL Server: {os.getenv('MSSQL_SERVER', 'Not set')}\n")
            f.write(f"Database: {os.getenv('MSSQL_DATABASE', 'Not set')}\n")
            f.write(f"Authentication: {os.getenv('MSSQL_USERNAME', 'Windows Authentication')}\n")
            f.write("Connecting to database...\n")
        
        try:
            print("Calling get_table_schema tool...")
            result = await session.call_tool("get_table_schema", {})
            print("get_table_schema tool returned")
            self.table_schema = getattr(result.content[0], "text", "")
            
            # Check if schema contains error messages
            if "Error retrieving schema:" in self.table_schema or "Database connection error details:" in self.table_schema:
                print("\n===== TABLE SCHEMA ERROR =====")
                print("Failed to retrieve complete table schema:")
                print(self.table_schema)
                print("===================================\n")
                
                # Write error to output file
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\n===== TABLE SCHEMA ERROR =====\n")
                    f.write("Failed to retrieve complete table schema:\n")
                    f.write(self.table_schema)
                    f.write("\n===================================\n")
                
                # Save the error trace
                schema_error = True
                print("Attempting to retrieve basic table information instead...")
            else:
                print("Schema information fetched successfully.")
                # Write success to output file
                with open(OUTPUT_FILE, "a") as f:
                    f.write("Schema retrieved successfully!\n")
                
                # Create a concise schema summary to reduce token usage
                self.schema_summary = await self.create_schema_summary(self.table_schema)
                print("Created concise schema summary.")
        except Exception as e:
            error_message = f"Error fetching schema: {str(e)}"
            print("\n===== TABLE SCHEMA ERROR =====")
            print(error_message)
            print("Full exception details:", repr(e))
            print("===================================\n")
            
            # Write error to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n===== TABLE SCHEMA ERROR =====\n")
                f.write(f"Error fetching schema: {str(e)}\n")
                f.write(f"Full exception details: {repr(e)}\n")
                f.write("===================================\n")
            
            schema_error = True
            self.table_schema = f"Schema information not available due to error: {str(e)}"
            
        # If schema retrieval failed, try to get basic information
        if schema_error:
            try:
                print("Attempting to fetch basic table information as fallback...")
                
                # Write to output file
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\nAttempting to fetch basic table information as fallback...\n")
                
                basic_result = await session.call_tool("get_table_info", {})
                basic_info = getattr(basic_result.content[0], "text", "")
                print("Basic table information retrieved:")
                print(basic_info)
                
                # Write to output file
                with open(OUTPUT_FILE, "a") as f:
                    f.write("Basic table information retrieved:\n")
                    f.write(basic_info)
                    f.write("\n")
                
                # Use basic info as the schema summary
                self.schema_summary = basic_info
                
                # Combine error and basic info for the full schema
                self.table_schema = f"""
Schema retrieval encountered errors. Limited table information available:

{basic_info}

(Note: Full schema details could not be retrieved. SQL generation may be limited.)
"""
            except Exception as basic_error:
                print(f"Error retrieving basic table info: {basic_error}")
                
                # Write to output file
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\nError retrieving basic table info: {basic_error}\n")
                    f.write("Both schema and basic info retrieval failed.\n")
                
                self.table_schema = "Both full schema and basic table information retrieval failed."
                self.schema_summary = f"Table: {FULLY_QUALIFIED_TABLE_NAME}"
            
        # Update the system prompt with schema information - use the summary instead of full schema
        try:
            self.system_prompt = self.system_prompt.format(
                schema_summary=self.schema_summary,
                table_name=FULLY_QUALIFIED_TABLE_NAME
            )
            print("System prompt updated with schema summary.")
        except Exception as format_error:
            print(f"Error formatting system prompt: {format_error}")
            
            # Write to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\nError formatting system prompt: {format_error}\n")
            
            # Fallback to direct replacement if formatting fails
            self.system_prompt = self.system_prompt.replace("{schema_summary}", self.schema_summary)
            self.system_prompt = self.system_prompt.replace("{table_name}", FULLY_QUALIFIED_TABLE_NAME)

    def extract_sql_from_assistant_reply(self, assistant_reply: str) -> dict:
        """Extract SQL from the assistant's reply, handling multiple formats."""
        # First check for the TOOL format
        tool_pattern = r"TOOL:\s*(\w+),\s*ARGS:\s*(\{.*\})"
        tool_matches = re.search(tool_pattern, assistant_reply, re.DOTALL)
        
        if tool_matches:
            try:
                tool_name = tool_matches.group(1)
                args_str = tool_matches.group(2)
                # Try to parse the args as JSON
                args = json.loads(args_str)
                return {"tool_name": tool_name, "args": args}
            except json.JSONDecodeError:
                pass  # Fall through to other extraction methods
        
        # Check for code blocks with SQL
        sql_code_block_pattern = r"```sql\s*(.*?)\s*```"
        sql_matches = re.search(sql_code_block_pattern, assistant_reply, re.DOTALL)
        
        if sql_matches:
            sql = sql_matches.group(1).strip()
            return {"tool_name": "query_table", "args": {"sql": sql}}
        
        # Check for generic code blocks that might contain SQL
        code_block_pattern = r"```\s*(.*?)\s*```"
        code_matches = re.search(code_block_pattern, assistant_reply, re.DOTALL)
        
        if code_matches:
            potential_sql = code_matches.group(1).strip()
            # Simple validation that it looks like SQL
            if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b", potential_sql, re.IGNORECASE):
                return {"tool_name": "query_table", "args": {"sql": potential_sql}}
        
        # Last resort: try to find any SQL-like statement
        fallback_sql_pattern = r'"sql":\s*"(.+?)"'
        fallback_matches = re.search(fallback_sql_pattern, assistant_reply, re.DOTALL)
        
        if fallback_matches:
            sql = fallback_matches.group(1).strip()
            return {"tool_name": "query_table", "args": {"sql": sql}}
        
        # If we get here, we couldn't extract SQL
        return None

    # Add a custom JSON encoder class to handle special float values
    class CustomJSONEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (datetime, bytes, bytearray)):
                return str(obj)
            elif isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return str(obj)
            return super().default(obj)

    async def process_query(self, session: ClientSession, query: str) -> None:
        """Process a natural language query, generate SQL, and execute it with user approval."""
        print(f"\nProcessing query: {query}")
        
        # Check for special commands and inputs
        if query.startswith("__") and query.endswith("__"):
            print(f"Received special command: {query}")
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\nReceived special command: {query}\n")
                f.write("This is a system command and will not be processed as a query.\n")
                f.flush()
            return
        
        # Write processing acknowledgement to output
        with open(OUTPUT_FILE, "a") as f:
            f.write(f"\nProcessing your query: {query}\n")
            f.write("Generating SQL query...\n")
            f.flush()
        
        # Reset query iterations for new query
        self.current_query_iterations = []
        
        # Add user query to conversation history 
        # But limit history to just the last 3 exchanges to save tokens
        self.messages = self.messages[-6:] if len(self.messages) > 6 else self.messages
        self.messages.append({"role": "user", "content": query})
        
        # Generate SQL (first iteration)
        try:
            await self.generate_sql_iteration(session, query)
        except Exception as e:
            print(f"Error generating SQL: {e}")
            import traceback
            traceback.print_exc()
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\nError generating SQL: {str(e)}\n")
                f.write("Please try rephrasing your query.\n")
                f.flush()
            return
        
        # Main query refinement loop
        while True:
            if not self.current_query_iterations:
                print("No SQL iterations generated")
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\nFailed to generate SQL for your query.\n")
                    f.write("Please try rephrasing your question.\n")
                    f.flush()
                return
                
            current_iteration = self.current_query_iterations[-1]
            
            # Display the generated SQL
            print("\n===== GENERATED SQL QUERY =====")
            print(current_iteration.generated_sql)
            print("===============================")
            
            # Write SQL to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n===== GENERATED SQL QUERY =====\n")
                f.write(f"{current_iteration.generated_sql}\n")
                f.write("===============================\n\n")
                f.write("Do you want to (e)xecute this query, provide (f)eedback to refine it, or (c)ancel? (e/f/c): ")
                f.flush()
            
            # Get user decision - wait for execute, refine, or cancel
            print("\nDo you want to (e)xecute this query, provide (f)eedback to refine it, or (c)ancel? (e/f/c): ")
            decision = get_input("").strip().lower()
            
            # Handle special decision inputs like __PROBE__
            if decision.startswith("__") and decision.endswith("__"):
                print(f"Received special command as decision: {decision}")
                continue
            
            if decision == 'c':
                print("Query canceled.")
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\nQuery canceled.\n")
                    f.flush()
                break
            
            elif decision == 'f':
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\nEnter your feedback for improving the SQL query: ")
                    f.flush()
                    
                feedback = get_input("Enter your feedback for improving the SQL query: ")
                
                # Handle special inputs in feedback
                if feedback.startswith("__") and feedback.endswith("__"):
                    print(f"Received special command as feedback: {feedback}")
                    with open(OUTPUT_FILE, "a") as f:
                        f.write("\nQuery refinement canceled due to special command.\n")
                        f.flush()
                    break
                
                current_iteration.feedback = feedback
                
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\nRefining SQL with feedback: {feedback}\n")
                    f.write("Generating improved SQL...\n")
                    f.flush()
                
                # Generate new SQL iteration based on feedback
                try:
                    await self.generate_sql_iteration(session, query, feedback)
                except Exception as e:
                    print(f"Error generating refined SQL: {e}")
                    import traceback
                    traceback.print_exc()
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(f"\nError generating refined SQL: {str(e)}\n")
                        f.write("Please try providing different feedback or executing the current query.\n")
                        f.flush()
                continue
            
            elif decision == 'e':
                # Execute the query
                current_iteration.executed = True
                
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\nExecuting SQL query...\n")
                    f.flush()
                
                try:
                    # Detect if this is likely a calculation/percentage query
                    has_calculation = any(op in current_iteration.generated_sql.upper() 
                                          for op in [' / ', '*', '+', '-', 'AVG(', 'SUM(', 'COUNT(', 'CAST(', 'CONVERT('])
                    
                    result = await session.call_tool("query_table", {"sql": current_iteration.generated_sql})
                    result_text = getattr(result.content[0], "text", "")
                    current_iteration.results = result_text
                    
                    # Extract and display tabular results
                    self.display_query_results(result_text)
                    
                    # Add just the execution result to conversation history (not the full result text)
                    execution_summary = "Query executed successfully."
                    if "rows returned" in result_text:
                        try:
                            rows_count = re.search(r"(\d+) rows returned", result_text).group(1)
                            execution_summary = f"Query executed successfully. {rows_count} rows returned."
                        except:
                            pass
                            
                    self.messages.append({
                        "role": "system",
                        "content": f"SQL query executed: {execution_summary}"
                    })
                    
                    # Save query log - handle JSON serialization carefully
                    iterations_data = []
                    for i, iter_data in enumerate(self.current_query_iterations):
                        iterations_data.append({
                            "iteration": i + 1,
                            "sql": iter_data.generated_sql,
                            "feedback": iter_data.feedback,
                            "executed": iter_data.executed
                        })
                    
                    # Prepare a simplified result summary for logging if there are calculation issues
                    safe_result_summary = result_text
                    if has_calculation and "JSON_DATA:" in result_text:
                        # Only keep the tabular part for the log to avoid serialization issues
                        safe_result_summary = result_text.split("\n\nJSON_DATA:")[0]
                        safe_result_summary += "\n\n[JSON data omitted for calculation query]"
                    
                    try:
                        with open(OUTPUT_FILE, "a") as f:
                            f.write("\nSaving query log...\n")
                            f.flush()
                            
                        log_result = await session.call_tool("save_query_log", {
                            "natural_language_query": query,
                            "sql_query": current_iteration.generated_sql,
                            "result_summary": safe_result_summary,
                            "iterations": iterations_data
                        })
                        log_message = getattr(log_result.content[0], "text", "")
                        print(f"\n{log_message}")
                        
                        with open(OUTPUT_FILE, "a") as f:
                            f.write(f"\n{log_message}\n")
                            f.flush()
                    except Exception as log_err:
                        print(f"Error saving query log: {log_err}")
                        with open(OUTPUT_FILE, "a") as f:
                            f.write(f"\nError saving query log: {str(log_err)}\n")
                            f.flush()
                            
                        # Try with a more minimal result summary if the first attempt failed
                        try:
                            minimal_summary = f"Query executed successfully. Results not logged due to serialization issues."
                            log_result = await session.call_tool("save_query_log", {
                                "natural_language_query": query,
                                "sql_query": current_iteration.generated_sql,
                                "result_summary": minimal_summary,
                                "iterations": iterations_data
                            })
                            print("Query log saved with minimal results due to serialization issues.")
                            
                            with open(OUTPUT_FILE, "a") as f:
                                f.write("\nQuery log saved with minimal results due to serialization issues.\n")
                                f.flush()
                        except Exception as retry_err:
                            print(f"Failed to save query log even with minimal results: {retry_err}")
                    
                    # Add to query history
                    query_record = {
                        "timestamp": datetime.now().isoformat(),
                        "natural_language": query,
                        "final_sql": current_iteration.generated_sql,
                        "iterations": len(self.current_query_iterations),
                        "success": not result_text.startswith("Error")
                    }
                    self.query_history.append(query_record)
                    
                    # Generate natural language explanation of results, but with fewer tokens
                    # Use the dedicated explanation system prompt
                    with open(OUTPUT_FILE, "a") as f:
                        f.write("\nGenerating explanation of results...\n")
                        f.flush()
                        
                    await self.generate_result_explanation(session, query, current_iteration.generated_sql, result_text)
                    
                except Exception as e:
                    error_message = f"Error executing query: {str(e)}"
                    print(f"\n===== QUERY ERROR =====")
                    print(error_message)
                    print("========================\n")
                    
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(f"\n===== QUERY ERROR =====\n")
                        f.write(f"{error_message}\n")
                        f.write("========================\n")
                        f.flush()
                        
                    self.messages.append({"role": "system", "content": error_message})
                
                break
            
            else:
                print(f"Invalid choice: {decision}. Please enter 'e' to execute, 'f' for feedback, or 'c' to cancel.")
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\nInvalid choice: {decision}. Please enter 'e' to execute, 'f' for feedback, or 'c' to cancel.\n")
                    f.write("Do you want to (e)xecute this query, provide (f)eedback to refine it, or (c)ancel? (e/f/c): ")
                    f.flush()

    def display_query_results(self, result_text: str) -> None:
        """Extract and display the tabular results from the query execution."""
        print("\n===== QUERY RESULTS =====")
        
        # Write to output file
        with open(OUTPUT_FILE, "a") as f:
            f.write("\n===== QUERY RESULTS =====\n")
            f.flush()
        
        if result_text.startswith("Error:"):
            print(result_text)
            # Write error to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"{result_text}\n")
                f.write("========================\n")
                f.flush()
            return
        
        # Split off JSON data if present
        display_text = result_text.split("\n\nJSON_DATA:")[0] if "JSON_DATA:" in result_text else result_text
        print(display_text)
        
        # Write results to output file
        with open(OUTPUT_FILE, "a") as f:
            f.write(f"{display_text}\n")
            f.flush()
        
        # Extract JSON data for potential programmatic use
        if "JSON_DATA:" in result_text:
            try:
                json_str = result_text.split("JSON_DATA:")[1]
                # This would be available for programmatic use but we don't display it
                json_data = json.loads(json_str)
            except json.JSONDecodeError as e:
                print(f"\nWarning: Could not parse JSON results: {str(e)}")
                # Write warning to output file
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\nWarning: Could not parse JSON results: {str(e)}\n")
                    f.flush()
                
                # Try to extract the JSON data with manual processing if the automatic parsing failed
                try:
                    # This is a fallback for when standard JSON parsing fails
                    json_str = result_text.split("JSON_DATA:")[1].strip()
                    # Replace common problematic values that might cause JSON parsing issues
                    json_str = json_str.replace('NaN', '"NaN"').replace('Infinity', '"Infinity"').replace('-Infinity', '"-Infinity"')
                    json_data = json.loads(json_str)
                    print("Successfully recovered JSON data with fallback method.")
                    
                    # Write success to output file
                    with open(OUTPUT_FILE, "a") as f:
                        f.write("Successfully recovered JSON data with fallback method.\n")
                        f.flush()
                except Exception as deep_error:
                    print(f"Failed to recover JSON data: {deep_error}")
                    
                    # Write error to output file
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(f"Failed to recover JSON data: {deep_error}\n")
                        f.flush()
        
        print("==========================\n")
        
        # Write closing block to output file
        with open(OUTPUT_FILE, "a") as f:
            f.write("==========================\n\n")
            f.write("Enter your next query, or type /exit to quit: ")
            f.flush()

    async def generate_sql_iteration(self, session: ClientSession, original_query: str, feedback: str = None) -> None:
        """Generate a SQL query iteration based on the original query and optional feedback."""
        iteration_number = len(self.current_query_iterations) + 1
        
        # Build the prompt based on iteration - keep it minimal
        if feedback and iteration_number > 1:
            # For subsequent iterations, just include what's changed - be token efficient
            previous_sql = self.current_query_iterations[-1].generated_sql
            prompt = f"Original question: {original_query}\n\nCurrent SQL: {previous_sql}\n\nFeedback: {feedback}\n\nGenerate improved SQL."
        else:
            # First iteration, just the query
            prompt = original_query
        
        # Generate a cache key for this query/feedback combination
        cache_key = f"sql:{hash(prompt)}"
        if cache_key in self.response_cache:
            print("Using cached SQL response")
            assistant_reply = self.response_cache[cache_key]
        else:
            # Build minimal conversation for OpenAI
            openai_messages = [
                {"role": "system", "content": self.system_prompt},
            ]
            
            # Only include 1-2 previous exchanges to minimize tokens
            if iteration_number > 1 and len(self.messages) >= 2:
                # Add just the most recent exchange
                openai_messages.extend(self.messages[-2:])
            
            openai_messages.append({"role": "user", "content": prompt})
            
            # Send to OpenAI with minimal token settings
            completion_params = {
                "messages": openai_messages,
                "max_tokens": 1000,  # Reduced from 2000
                "temperature": 0.0,
                "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")
            }
            
            try:
                completion = client.chat.completions.create(**completion_params)
                assistant_reply = completion.choices[0].message.content
                
                # Cache the response
                self.response_cache[cache_key] = assistant_reply
            except Exception as e:
                print(f"Error generating SQL: {str(e)}")
                return
        
        # Extract SQL from reply
        extracted = self.extract_sql_from_assistant_reply(assistant_reply)
        
        if extracted and extracted.get("tool_name") == "query_table" and "sql" in extracted.get("args", {}):
            sql_query = extracted["args"]["sql"]
            
            # Create a new iteration record
            iteration = QueryIteration(
                iteration_number=iteration_number,
                natural_language_query=prompt,
                generated_sql=sql_query,
                feedback=feedback if iteration_number > 1 else None
            )
            
            self.current_query_iterations.append(iteration)
            
            # For first iteration, add assistant's response to conversation history (but not the full response)
            if iteration_number == 1:
                self.messages.append({
                    "role": "assistant", 
                    "content": f"I'll run a SQL query to answer this question."
                })
            
            print(f"\nSQL query generated (iteration {iteration_number}).")
        else:
            print(f"\n===== SQL EXTRACTION ERROR =====")
            print("Could not extract valid SQL from assistant's response:")
            print(assistant_reply)
            print("=================================\n")
            
            # Fall back to asking the user for SQL directly
            sql_query = get_input("Please enter the SQL query manually: ")
            
            # Create a new iteration record with manual SQL
            iteration = QueryIteration(
                iteration_number=iteration_number,
                natural_language_query=prompt,
                generated_sql=sql_query,
                feedback=feedback if iteration_number > 1 else None
            )
            
            self.current_query_iterations.append(iteration)
    
    async def generate_result_explanation(self, session: ClientSession, 
                                     query: str, sql: str, results: str) -> None:
        """Generate a natural language explanation of the query results with minimal tokens."""
        # Check cache first
        cache_key = f"explanation:{hash(results)}"
        if cache_key in self.response_cache:
            explanation = self.response_cache[cache_key]
            print("\n===== RESULT EXPLANATION =====")
            print(explanation)
            print("==============================\n")
            
            # Write to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n===== RESULT EXPLANATION =====\n")
                f.write(explanation + "\n")
                f.write("==============================\n\n")
                f.flush()
                
            self.messages.append({"role": "assistant", "content": explanation})
            return
            
        # Write to output file that we're generating an explanation
        with open(OUTPUT_FILE, "a") as f:
            f.write("\nGenerating explanation of results...\n")
            f.flush()
            
        # Extract just the tabular part for the explanation (without the JSON)
        # And limit the size to reduce token usage
        results_for_explanation = results.split("\n\nJSON_DATA:")[0] if "JSON_DATA:" in results else results
        
        # Further reduce token count by limiting the result size if needed
        if len(results_for_explanation.split('\n')) > 15:
            results_preview = "\n".join(results_for_explanation.split('\n')[:15])
            results_for_explanation = f"{results_preview}\n\n[...additional rows omitted for brevity...]"
        
        # Keep the prompt minimal
        prompt = (
            f"Question: {query}\n\n"
            f"SQL: {sql}\n\n"
            f"Results:\n{results_for_explanation}\n\n"
            f"Provide a brief explanation of these results."
        )
        
        # Build minimal conversation for OpenAI, using the dedicated explanation system prompt
        openai_messages = [
            {"role": "system", "content": self.explanation_system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # Send to OpenAI with minimal token settings
        completion_params = {
            "messages": openai_messages,
            "max_tokens": 500,  # Reduced from 1000
            "temperature": 0.1,
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")
        }
        
        try:
            completion = client.chat.completions.create(**completion_params)
            explanation = completion.choices[0].message.content
            
            # Cache the explanation
            self.response_cache[cache_key] = explanation
            
            print("\n===== RESULT EXPLANATION =====")
            print(explanation)
            print("==============================\n")
            
            # Write explanation to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n===== RESULT EXPLANATION =====\n")
                f.write(explanation + "\n")
                f.write("==============================\n\n")
                f.write("Enter your next query, or type /exit to quit: ")
                f.flush()
            
            # Add explanation to conversation history
            self.messages.append({"role": "assistant", "content": explanation})
        except Exception as e:
            print(f"Error generating result explanation: {str(e)}")
            import traceback
            traceback.print_exc()
            
            # Write error to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\nError generating result explanation: {str(e)}\n")
                f.write("Please try with a different query.\n")
                f.write("Enter your next query, or type /exit to quit: ")
                f.flush()

    async def show_query_history(self):
        """Display the history of queries executed in this session."""
        if not self.query_history:
            print("\nNo queries have been executed in this session.")
            return
        
        print("\n===== QUERY HISTORY =====")
        for i, query in enumerate(self.query_history):
            timestamp = query.get("timestamp", "Unknown time")
            if isinstance(timestamp, str) and len(timestamp) > 19:
                timestamp = timestamp[:19].replace('T', ' ')  # Format ISO timestamp
                
            print(f"{i+1}. [{timestamp}] {query.get('natural_language', 'Unknown query')}")
            print(f"   SQL: {query.get('final_sql', 'No SQL generated')[:80]}..." if len(query.get('final_sql', '')) > 80 else f"   SQL: {query.get('final_sql', 'No SQL generated')}")
            print(f"   Iterations: {query.get('iterations', 1)}, Success: {query.get('success', False)}")
            print()
        
        print("=======================\n")

    async def run_diagnostics(self, session: ClientSession):
        """Run comprehensive diagnostics to troubleshoot table access issues."""
        print(f"\n===== RUNNING DIAGNOSTICS FOR TABLE {FULLY_QUALIFIED_TABLE_NAME} =====")
        with open(OUTPUT_FILE, "a") as f:
            f.write(f"\n===== RUNNING DIAGNOSTICS FOR TABLE {FULLY_QUALIFIED_TABLE_NAME} =====\n")
            f.flush()
        
        # First, show environment variables (with password masked)
        print("\n1. ENVIRONMENT VARIABLES:")
        print(f"   MSSQL_SERVER = {MSSQL_SERVER}")
        print(f"   MSSQL_DATABASE = {MSSQL_DATABASE}")
        print(f"   MSSQL_USERNAME = {MSSQL_USERNAME}")
        print(f"   MSSQL_PASSWORD = {'[PROVIDED]' if MSSQL_PASSWORD else '[NOT SET]'}")
        print(f"   MSSQL_DRIVER = {MSSQL_DRIVER}")
        print(f"   MSSQL_TABLE_SCHEMA = {MSSQL_TABLE_SCHEMA}")
        print(f"   MSSQL_TABLE_NAME = {MSSQL_TABLE_NAME}")
        
        with open(OUTPUT_FILE, "a") as f:
            f.write("\n1. ENVIRONMENT VARIABLES:\n")
            f.write(f"   MSSQL_SERVER = {MSSQL_SERVER}\n")
            f.write(f"   MSSQL_DATABASE = {MSSQL_DATABASE}\n")
            f.write(f"   MSSQL_USERNAME = {MSSQL_USERNAME}\n")
            f.write(f"   MSSQL_PASSWORD = {'[PROVIDED]' if MSSQL_PASSWORD else '[NOT SET]'}\n")
            f.write(f"   MSSQL_DRIVER = {MSSQL_DRIVER}\n")
            f.write(f"   MSSQL_TABLE_SCHEMA = {MSSQL_TABLE_SCHEMA}\n")
            f.write(f"   MSSQL_TABLE_NAME = {MSSQL_TABLE_NAME}\n")
            f.flush()
        
        # Check that required variables are set
        missing_vars = []
        if not MSSQL_SERVER:
            missing_vars.append("MSSQL_SERVER")
        if not MSSQL_DATABASE:
            missing_vars.append("MSSQL_DATABASE")
        if not MSSQL_USERNAME:
            missing_vars.append("MSSQL_USERNAME")
        if not MSSQL_PASSWORD:
            missing_vars.append("MSSQL_PASSWORD")
        if not MSSQL_DRIVER:
            missing_vars.append("MSSQL_DRIVER")
        
        if missing_vars:
            print(f"\n[ERROR] Missing required environment variables: {', '.join(missing_vars)}")
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\n[ERROR] Missing required environment variables: {', '.join(missing_vars)}\n")
                f.write("These variables must be set in your .env file or environment.\n")
                f.flush()
        else:
            print("\n[OK] All required environment variables are set")
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n[OK] All required environment variables are set\n")
                f.flush()
        
        # Check for ODBC drivers
        try:
            import pyodbc
            drivers = pyodbc.drivers()
            print("\n2. AVAILABLE ODBC DRIVERS:")
            for driver in drivers:
                print(f"   - {driver}")
            
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n2. AVAILABLE ODBC DRIVERS:\n")
                for driver in drivers:
                    f.write(f"   - {driver}\n")
                f.flush()
            
            # Check if our driver is available
            driver_name = MSSQL_DRIVER.strip("{}")
            if driver_name in drivers:
                print(f"\n[OK] Configured driver '{driver_name}' is available")
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\n[OK] Configured driver '{driver_name}' is available\n")
                    f.flush()
            elif any('SQL Server' in driver for driver in drivers):
                print(f"\n[WARNING] Configured driver '{driver_name}' not found, but other SQL Server drivers are available")
                sql_drivers = [d for d in drivers if 'SQL Server' in d]
                print(f"   Recommended drivers: {', '.join(sql_drivers)}")
                
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\n[WARNING] Configured driver '{driver_name}' not found, but other SQL Server drivers are available\n")
                    f.write(f"   Recommended drivers: {', '.join(sql_drivers)}\n")
                    f.flush()
            else:
                print(f"\n[ERROR] No SQL Server ODBC drivers found on this system")
                print(f"   Install the Microsoft ODBC Driver for SQL Server")
                
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\n[ERROR] No SQL Server ODBC drivers found on this system\n")
                    f.write(f"   Install the Microsoft ODBC Driver for SQL Server\n")
                    f.flush()
        except Exception as e:
            print(f"\n[ERROR] Error checking ODBC drivers: {str(e)}")
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\n[ERROR] Error checking ODBC drivers: {str(e)}\n")
                f.flush()
        
        # Run the comprehensive connection test
        print("\n3. SQL CONNECTION TEST:")
        with open(OUTPUT_FILE, "a") as f:
            f.write("\n3. SQL CONNECTION TEST:\n")
            f.flush()
        
        connection_ok = await self.test_sql_connection(session)
        
        # Get schema summary (if available)
        print("\n4. SCHEMA STATUS:")
        if self.schema_summary:
            print("[OK] Schema summary available:")
            print(self.schema_summary[:300] + "..." if len(self.schema_summary) > 300 else self.schema_summary)
            
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n4. SCHEMA STATUS:\n")
                f.write("[OK] Schema summary available:\n")
                f.write(self.schema_summary[:500] + "..." if len(self.schema_summary) > 500 else self.schema_summary)
                f.write("\n")
                f.flush()
        else:
            print("[ERROR] Schema summary not available")
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n4. SCHEMA STATUS:\n")
                f.write("[ERROR] Schema summary not available\n")
                f.flush()
        
        # Show system status
        print("\n5. SYSTEM STATUS:")
        import platform
        print(f"   OS: {platform.system()} {platform.version()}")
        print(f"   Python: {platform.python_version()}")
        
        with open(OUTPUT_FILE, "a") as f:
            f.write("\n5. SYSTEM STATUS:\n")
            f.write(f"   OS: {platform.system()} {platform.version()}\n")
            f.write(f"   Python: {platform.python_version()}\n")
            f.flush()
        
        # Final diagnostic summary
        print("\n===== DIAGNOSTIC SUMMARY =====")
        if connection_ok:
            print("[OK] SQL CONNECTION: Working properly")
        else:
            print("[ERROR] SQL CONNECTION: Not working")
        
        if self.schema_summary:
            print("[OK] SCHEMA: Available")
        else:
            print("[ERROR] SCHEMA: Not available")
        
        with open(OUTPUT_FILE, "a") as f:
            f.write("\n===== DIAGNOSTIC SUMMARY =====\n")
            if connection_ok:
                f.write("[OK] SQL CONNECTION: Working properly\n")
            else:
                f.write("[ERROR] SQL CONNECTION: Not working\n")
            
            if self.schema_summary:
                f.write("[OK] SCHEMA: Available\n")
            else:
                f.write("[ERROR] SCHEMA: Not available\n")
            
            f.write("\nTroubleshooting steps if you have issues:\n")
            f.write("1. Check that all environment variables are correctly set in your .env file\n")
            f.write("2. Verify that the SQL Server is running and accessible\n")
            f.write("3. Ensure you have the correct ODBC driver installed\n")
            f.write("4. Verify table exists and permissions are correct\n")
            f.write("5. Verify network connectivity if using a remote SQL Server\n")
            f.write("6. Try running /refresh_schema to reload the schema\n")
            f.flush()
        
        print("\nDiagnostics complete.")
        print("If you're experiencing issues:")
        print("1. Check environment variables in your .env file")
        print("2. Verify SQL Server is running and accessible")
        print("3. Ensure proper ODBC driver is installed")
        print("4. Verify table exists and permissions are correct")
        print("===============================\n")

    async def show_recent_logs(self, session: ClientSession, n: int = 5):
        """Show recent query logs with their results."""
        print(f"\n===== SHOWING {n} RECENT QUERY LOGS =====")
        try:
            result = await session.call_tool("get_recent_query_logs", {"n": n})
            logs = getattr(result.content[0], "text", "")
            if logs:
                print(logs)
            else:
                print("No query logs found.")
        except Exception as e:
            print(f"Error retrieving query logs: {e}")
        print("===============================\n")

    async def chat_loop(self, session: ClientSession):
        """Main chat loop for interactive querying."""
        # Write initial welcome message to output
        with open(OUTPUT_FILE, "w") as f:
            f.write(f"\nTable Assistant is ready. You are working with table: {FULLY_QUALIFIED_TABLE_NAME}\n")
            f.write("A data preview has been requested to verify the database connection.\n")
            f.write("This will appear above when the connection is established.\n\n")
            f.write("Type your questions about the table in natural language, and I'll translate them to SQL.\n")
            f.write("Special commands:\n")
            f.write("  /diagnose - Run diagnostics\n")
            f.write("  /refresh_schema - Refresh table schema\n")
            f.write("  /preview - Show data preview again\n")
            f.write("  /history - View query history\n")
            f.write("  /show-logs [n] - View recent query logs (default: 5)\n\n")
            f.write("Waiting for your first query...\n")
            f.flush()
        
        print(f"\nTable Assistant is ready. You are working with table: {FULLY_QUALIFIED_TABLE_NAME}")
        print("A data preview has been requested to verify the database connection.")
        print("Type your questions about the table in natural language, and I'll translate them to SQL.")
        print("Special commands:")
        print("  /diagnose - Run diagnostics")
        print("  /refresh_schema - Refresh table schema")
        print("  /preview - Show data preview again")
        print("  /history - View query history")
        print("  /show-logs [n] - View recent query logs (default: 5)")
        
        # Ping back to the web server
        with open(OUTPUT_FILE, "a") as f:
            f.write("\nEnter your Query (or type /exit to quit): ")
            f.flush()
        
        while True:
            try:
                # Request input only once per loop iteration
                query = get_input("\nEnter your Query (or type /exit to quit): ").strip()
                
                # Skip heartbeat messages
                if query == "__HEARTBEAT__":
                    continue
                
                if not query:
                    print("Empty query received, please try again")
                    continue
                    
                if query.lower() == "/exit":
                    print("\nExiting...")
                    # Write exit message to output file
                    with open(OUTPUT_FILE, "a") as f:
                        f.write("\nExiting SQL Assistant...\n")
                        f.flush()
                    break
                    
                # Process special commands
                if query.lower() == "/diagnose":
                    await self.run_diagnostics(session)
                    continue
                elif query.lower() == "/refresh_schema":
                    await self.fetch_schema(session)
                    continue
                elif query.lower() == "/preview":
                    await self.fetch_data_preview(session)
                    continue
                elif query.lower() == "/history":
                    await self.show_query_history()
                    continue
                elif query.lower().startswith("/show-logs"):
                    # Parse the number of logs to show
                    parts = query.split()
                    n = 5  # Default
                    if len(parts) > 1:
                        try:
                            n = int(parts[1])
                        except ValueError:
                            print("Invalid number. Using default of 5 logs.")
                    await self.show_recent_logs(session, n)
                    continue
                
                # Process regular queries - write to output file first to confirm receipt
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\nProcessing: {query}\n")
                    f.write("Generating SQL query...\n")
                    f.flush()
                
                # Process the query
                try:
                    await self.process_query(session, query)
                except Exception as query_err:
                    print(f"Error processing query: {query_err}")
                    import traceback
                    traceback.print_exc()
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(f"\nError processing query: {str(query_err)}\n")
                        f.write("Please try again with a different query.\n")
                        f.flush()
                
            except Exception as e:
                print(f"Error in chat loop: {e}")
                import traceback
                traceback.print_exc()
                
                # Write error to output file
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\nError in chat loop: {str(e)}\n")
                    f.write("Please try again.\n")
                    f.flush()
                
                # Sleep briefly to avoid busy-waiting in case of persistent errors
                await asyncio.sleep(1)

    async def test_sql_connection(self, session: ClientSession) -> bool:
        """
        Performs a comprehensive SQL connection test and displays the results.
        Returns True if connection is successful, False otherwise.
        """
        try:
            print("\n===== TESTING SQL CONNECTION =====")
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n===== TESTING SQL CONNECTION =====\n")
                f.write("Running comprehensive connection diagnostics...\n")
                f.flush()
            
            # First try a direct connection test (doesn't depend on server process)
            direct_result = await self.direct_sql_test()
            if not direct_result:
                # If direct test fails, we know there's a fundamental connection issue
                return False
                
            # If direct test succeeds, continue with the regular test for more details
            logger.info("Calling test_connection tool for comprehensive diagnostics")
            test_result = await session.call_tool("test_connection", {})
            test_output = getattr(test_result.content[0], "text", "")
            
            # Log the full test output for diagnostics
            logger.info("Connection test complete, full results:\n" + test_output)
            
            # Display the test results
            print("\n" + test_output)
            
            # Write comprehensive results to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n=== CONNECTION TEST RESULTS ===\n")
                f.write(test_output)
                f.write("\n================================\n")
            
            # Parse the test output to determine success/failure
            if "[FAILED]" in test_output or "[ERROR]" in test_output:
                # Extract specific error for focused debugging
                error_lines = [line for line in test_output.split('\n') if "[FAILED]" in line or "[ERROR]" in line]
                
                logger.error(f"Connection test failed with {len(error_lines)} errors")
                for err in error_lines:
                    logger.error(f"Connection error: {err}")
                
                # Identify the primary issue for the user
                primary_issue = "Unknown connection issue"
                if any("Server connection failed" in line for line in error_lines):
                    primary_issue = "Cannot connect to SQL Server - server may be unreachable or credentials incorrect"
                elif any("Database connection failed" in line for line in error_lines):
                    primary_issue = "Cannot connect to database - database may not exist or user lacks permission"
                elif any("table existence" in line.lower() for line in error_lines):
                    primary_issue = "Table not found - check table name and schema"
                elif any("accessing table" in line.lower() for line in error_lines):
                    primary_issue = "Cannot access table - user may lack SELECT permission"
                
                # Display a focused error message
                print(f"\n[ERROR] CONNECTION ERROR: {primary_issue}")
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"\n[ERROR] CONNECTION ERROR: {primary_issue}\n")
                    f.write("Please fix this issue before proceeding.\n")
                    f.write("You can run the /diagnose command for more details.\n")
                
                return False
            else:
                print("\n[SUCCESS] Connection test PASSED!")
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\n[SUCCESS] Connection test successful!\n")
                    f.flush()
                return True
            
        except Exception as e:
            logger.error(f"Error running connection test: {e}", exc_info=True)
            print(f"\n[ERROR] Error running connection test: {e}")
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\n[ERROR] Error running connection test: {e}\n")
                f.flush()
            return False

    async def direct_sql_test(self) -> bool:
        """
        Directly test SQL connectivity using pyodbc without relying on server process.
        This helps identify if the issue is with the connection or the server process.
        """
        print("\n--- Direct SQL Connection Test ---")
        with open(OUTPUT_FILE, "a") as f:
            f.write("\n--- Direct SQL Connection Test ---\n")
            f.write("Testing direct connection to SQL Server using pyodbc...\n")
            f.flush()
            
        try:
            # Build connection string - same as server side
            connection_string = (
                f"DRIVER={MSSQL_DRIVER};"
                f"SERVER={MSSQL_SERVER};"
                f"DATABASE={MSSQL_DATABASE};"
                f"UID={MSSQL_USERNAME};"
                f"PWD={MSSQL_PASSWORD};"
                f"Authentication=SqlPassword;"
                f"Encrypt=yes;"
                f"TrustServerCertificate=yes;"
                f"Connection Timeout=10"
            )
            
            print(f"Attempting to connect to SQL Server: {MSSQL_SERVER}")
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"Connecting to: {MSSQL_SERVER}, Database: {MSSQL_DATABASE}\n")
                f.write(f"Using driver: {MSSQL_DRIVER}\n")
                f.flush()
                
            # Wrapped in a task with timeout to prevent hanging
            async def try_connect():
                # Run in a thread to not block asyncio
                def connect_sync():
                    try:
                        conn = pyodbc.connect(connection_string, timeout=10)
                        cursor = conn.cursor()
                        cursor.execute("SELECT @@VERSION")
                        version = cursor.fetchone()[0]
                        conn.close()
                        return True, version
                    except Exception as e:
                        return False, str(e)
                
                # Run the synchronous pyodbc code in a thread pool
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, connect_sync)
            
            # Execute with timeout
            try:
                success, result = await asyncio.wait_for(try_connect(), timeout=15)
                
                if success:
                    version_summary = result[:100] + "..." if len(result) > 100 else result
                    print(f"[SUCCESS] Direct connection successful")
                    print(f"SQL Server version: {version_summary}")
                    
                    with open(OUTPUT_FILE, "a") as f:
                        f.write("[SUCCESS] Direct connection successful\n")
                        f.write(f"SQL Server version: {version_summary}\n")
                        f.flush()
                    return True
                else:
                    print(f"[FAILED] Direct connection failed: {result}")
                    
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(f"[FAILED] Direct connection failed: {result}\n")
                        f.write("This indicates a fundamental connection issue with SQL Server.\n")
                        f.write("Possible causes:\n")
                        f.write("1. SQL Server is not running or unreachable\n") 
                        f.write("2. Incorrect server name, credentials, or database\n")
                        f.write("3. Firewall blocking the connection\n")
                        f.write("4. ODBC driver issue\n")
                        f.flush()
                    return False
            except asyncio.TimeoutError:
                print(f"[TIMEOUT] Connection attempt timed out after 15 seconds")
                
                with open(OUTPUT_FILE, "a") as f:
                    f.write(f"[TIMEOUT] Connection attempt timed out after 15 seconds\n")
                    f.write("This typically indicates network connectivity issues\n")
                    f.write("or that the SQL Server is unreachable.\n")
                    f.flush()
                return False
                
        except Exception as e:
            print(f"[ERROR] Error during direct connection test: {str(e)}")
            
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"[ERROR] Error during direct connection test: {str(e)}\n")
                f.flush()
            return False

    async def fetch_data_preview(self, session: ClientSession) -> None:
        """Fetch a preview of the data (first row) to verify SQL connection."""
        print(f"\n===== FETCHING DATA PREVIEW FOR {FULLY_QUALIFIED_TABLE_NAME} =====")
        
        # First validate connection parameters
        if not validate_sql_connection_params():
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n===== CONNECTION PARAMETER ERROR =====\n")
                f.write("SQL Server connection cannot be established due to missing parameters.\n")
                f.write("Please check your .env file and ensure all required variables are set.\n")
                f.write("Required parameters:\n")
                f.write("- MSSQL_SERVER\n")
                f.write("- MSSQL_DATABASE\n")
                f.write("- MSSQL_USERNAME\n")
                f.write("- MSSQL_PASSWORD\n")
                f.write("- MSSQL_DRIVER (or a system ODBC driver must be available)\n")
                f.write("================================\n")
            return False
        
        # Start by testing the SQL connection thoroughly
        connection_ok = await self.test_sql_connection(session)
        if not connection_ok:
            logger.error("SQL connection test failed, aborting data preview")
            return False
        
        try:
            # Now that connection is verified, try running a simple preview query
            preview_sql = f"SELECT TOP 5 * FROM {FULLY_QUALIFIED_TABLE_NAME}"
            print(f"Executing preview query: {preview_sql}")
            
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\nExecuting data preview query: {preview_sql}\n")
                f.flush()
            
            # Execute query
            result = await session.call_tool("query_table", {"sql": preview_sql})
            preview_data = getattr(result.content[0], "text", "")
            
            # Check if the result contains error message
            if preview_data.startswith("Error:") or "Error retrieving data" in preview_data:
                raise Exception(preview_data)
            
            # Write to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n===== DATA PREVIEW =====\n")
                f.write("Showing first 5 rows of data to verify connection:\n\n")
                f.write(preview_data)
                f.write("\n========================\n")
            
            print("Data preview fetched successfully:")
            print(preview_data)
            print("==================================\n")
            return True
        except Exception as e:
            error_message = f"Error fetching data preview: {str(e)}"
            print(f"\n===== DATA PREVIEW ERROR =====")
            print(error_message)
            print("==============================\n")
            
            # Write detailed error to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write("\n===== DATA PREVIEW ERROR =====\n")
                f.write(f"Failed to fetch data preview: {str(e)}\n\n")
                f.write("This may indicate connection issues with the SQL server.\n\n")
                f.write("Try running /diagnose for more information.\n")
                f.write("==============================\n")
            return False

    async def run(self):
        """Main entry point to run the chat session."""
        try:
            # Print startup information
            print(f"\n===== SQL TABLE ASSISTANT STARTUP =====")
            print(f"Starting file-based client with:")
            print(f"Input file: {INPUT_FILE}")
            print(f"Output file: {OUTPUT_FILE}")
            print(f"Table: {FULLY_QUALIFIED_TABLE_NAME}")
            print(f"SQL Server environment variables:")
            print(f"  MSSQL_SERVER: {os.getenv('MSSQL_SERVER', 'Not set')}")
            print(f"  MSSQL_DATABASE: {os.getenv('MSSQL_DATABASE', 'Not set')}")
            print(f"  MSSQL_USERNAME: {os.getenv('MSSQL_USERNAME', 'Not set (using Windows Auth)')}")
            print(f"  MSSQL_PASSWORD: {'[PROVIDED]' if os.getenv('MSSQL_PASSWORD') else 'Not set'}")
            print(f"  MSSQL_DRIVER: {os.getenv('MSSQL_DRIVER', 'Not set')}")
            print(f"===================================\n")
            
            # Write startup info to output file
            with open(OUTPUT_FILE, "w") as f:
                f.write(f"\n===== SQL TABLE ASSISTANT STARTUP =====\n")
                f.write(f"Starting assistant for table: {FULLY_QUALIFIED_TABLE_NAME}\n")
                f.write(f"Connected to server: {os.getenv('MSSQL_SERVER', 'Not set')}\n")
                f.write(f"Database: {os.getenv('MSSQL_DATABASE', 'Not set')}\n")
                f.write("Initializing connection...\n")
            
            # Use the FileBasedClientSession directly
            print("Creating file-based client session...")
            session = FileBasedClientSession()
            print("Initializing session...")
            
            # Initialize with timeout
            init_task = asyncio.create_task(session.initialize())
            try:
                await asyncio.wait_for(init_task, timeout=10)
                print("Session initialized successfully.")
            except asyncio.TimeoutError:
                print("Session initialization timed out after 10 seconds")
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\nSESSION INITIALIZATION TIMEOUT\n")
                    f.write("The SQL connection initialization process timed out after 10 seconds.\n")
                    f.write("This usually indicates a network connectivity issue to the SQL Server.\n")
                    f.write("Please check your SQL Server connection settings and network connectivity.\n")
            
            # First validate SQL connection through explicit testing - with retries
            connection_ok = False
            max_retries = 3
            
            for attempt in range(max_retries):
                if attempt > 0:
                    retry_delay = 5  # seconds
                    print(f"\nRetrying SQL connection (attempt {attempt+1}/{max_retries}) after {retry_delay} seconds...")
                    with open(OUTPUT_FILE, "a") as f:
                        f.write(f"\nRetrying SQL connection (attempt {attempt+1}/{max_retries}) after {retry_delay} seconds...\n")
                        f.flush()
                    await asyncio.sleep(retry_delay)
                
                # Try direct test first (faster)
                direct_ok = await self.direct_sql_test()
                if direct_ok:
                    # If direct test passes, we can proceed - no need for full test
                    print("Direct SQL connection test passed, skipping full test")
                    connection_ok = True
                    break
                elif attempt == max_retries - 1:
                    # On final attempt, try the full test as well
                    connection_ok = await self.test_sql_connection(session)
                    if connection_ok:
                        break
            
            if not connection_ok:
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\n===== CONNECTION FAILURE =====\n")
                    f.write("CRITICAL: Cannot establish SQL connection after multiple attempts.\n")
                    f.write("The SQL Table Assistant requires a working SQL connection to function.\n")
                    f.write("Please fix the connection issues before proceeding.\n\n")
                    f.write("Common issues:\n")
                    f.write("1. SQL Server not running or unreachable\n")
                    f.write("2. Incorrect server name or credentials\n")
                    f.write("3. Firewall blocking the connection\n")
                    f.write("4. Wrong ODBC driver or missing driver\n\n")
                    f.write("You may continue in limited mode, but SQL queries will fail.\n")
                    f.write("==============================\n")
                    f.write("\nEnter your Query (or type /exit to quit): ")
                    f.flush()
                    
                # Still try to fetch schema, but it will likely fail
                print("WARNING: Continuing without SQL connection - queries will fail")
            
            # Fetch schema information with timeout
            print("Fetching schema information...")
            with open(OUTPUT_FILE, "a") as f:
                f.write("\nFetching table schema...\n")
                
            schema_task = asyncio.create_task(self.fetch_schema(session))
            try:
                await asyncio.wait_for(schema_task, timeout=30)
                print("Schema fetched successfully.")
            except asyncio.TimeoutError:
                print("Schema fetch timed out after 30 seconds")
                with open(OUTPUT_FILE, "a") as f:
                    f.write("\nSCHEMA FETCH TIMEOUT\n")
                    f.write("The schema retrieval process timed out after 30 seconds.\n")
                    f.write("This usually indicates a problem with the SQL Server connection or permissions.\n")
                    f.write("Continuing with limited functionality...\n")
                    
                # Set default schema if timeout occurred
                if not self.schema_summary:
                    self.schema_summary = f"Table: {FULLY_QUALIFIED_TABLE_NAME}\nSchema could not be retrieved due to timeout."
                    self.system_prompt = self.system_prompt.replace("{schema_summary}", self.schema_summary)
                    self.system_prompt = self.system_prompt.replace("{table_name}", FULLY_QUALIFIED_TABLE_NAME)
            
            # Start the interactive chat loop
            print("Starting chat loop...")
            await self.chat_loop(session)
        except Exception as e:
            print(f"Error running chat: {e}")
            import traceback
            traceback.print_exc()
            
            # Write error to output file
            with open(OUTPUT_FILE, "a") as f:
                f.write(f"\n===== FATAL ERROR =====\n")
                f.write(f"The SQL Table Assistant encountered a fatal error:\n{str(e)}\n\n")
                f.write("This might be due to SQL Server connection issues or configuration problems.\n")
                f.write("Please check the following:\n")
                f.write("1. SQL Server connection settings in your .env file\n")
                f.write("2. Network connectivity to the SQL Server\n")
                f.write("3. Proper installation of SQL Server drivers\n")
                f.write("4. Table name and permissions\n")
                f.write("===========================\n")

# Main entry point
if __name__ == "__main__":
    try:
        asyncio.run(Chat().run())
    except KeyboardInterrupt:
        print("\nProcess interrupted.")
    except Exception as e:
        print(f"Application error: {e}")
        # Don't wait for Enter in file-based mode
        print("Exiting...") 