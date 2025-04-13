from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
import os
import re
import json
import math
from mcp import ClientSession, stdio_client
import asyncio
from openai import AzureOpenAI

# Initialize OpenAI client
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2023-05-15"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

# Get the table name from environment variables or use a default
FULLY_QUALIFIED_TABLE_NAME = os.getenv("SQL_TABLE_NAME", "dbo.YourTableName")

# Define input/output file paths
INPUT_FILE = os.getenv("MCP_INPUT_FILE", "input.txt")
OUTPUT_FILE = os.getenv("MCP_OUTPUT_FILE", "output.txt")

# Server params for MCP connection
server_params = {"stdio": True}

# Function to read input from file - for file-based operation
def get_input(prompt):
    """Get user input from file instead of stdin."""
    # Print the prompt to output file
    with open(OUTPUT_FILE, "a") as f:
        f.write(f"{prompt}\nWAITING_FOR_INPUT\n")
    
    # Wait for input in the input file
    last_modified = os.path.getmtime(INPUT_FILE) if os.path.exists(INPUT_FILE) else 0
    while True:
        try:
            if os.path.exists(INPUT_FILE) and os.path.getmtime(INPUT_FILE) > last_modified:
                with open(INPUT_FILE, "r") as f:
                    content = f.read().strip()
                
                # Clear the file after reading
                with open(INPUT_FILE, "w") as f:
                    pass
                
                return content
            
            # Sleep briefly to avoid CPU spinning
            asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error reading input: {e}")
            return ""

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
        
        try:
            result = await session.call_tool("get_table_schema", {})
            self.table_schema = getattr(result.content[0], "text", "")
            
            # Check if schema contains error messages
            if "Error retrieving schema:" in self.table_schema or "Database connection error details:" in self.table_schema:
                print("\n===== TABLE SCHEMA ERROR =====")
                print("Failed to retrieve complete table schema:")
                print(self.table_schema)
                print("===================================\n")
                
                # Save the error trace
                schema_error = True
                print("Attempting to retrieve basic table information instead...")
            else:
                print("Schema information fetched successfully.")
                # Create a concise schema summary to reduce token usage
                self.schema_summary = await self.create_schema_summary(self.table_schema)
                print("Created concise schema summary.")
        except Exception as e:
            error_message = f"Error fetching schema: {str(e)}"
            print("\n===== TABLE SCHEMA ERROR =====")
            print(error_message)
            print("Full exception details:", repr(e))
            print("===================================\n")
            schema_error = True
            self.table_schema = f"Schema information not available due to error: {str(e)}"
            
        # If schema retrieval failed, try to get basic information
        if schema_error:
            try:
                print("Attempting to fetch basic table information as fallback...")
                basic_result = await session.call_tool("get_table_info", {})
                basic_info = getattr(basic_result.content[0], "text", "")
                print("Basic table information retrieved:")
                print(basic_info)
                
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
        
        # Reset query iterations for new query
        self.current_query_iterations = []
        
        # Add user query to conversation history 
        # But limit history to just the last 3 exchanges to save tokens
        self.messages = self.messages[-6:] if len(self.messages) > 6 else self.messages
        self.messages.append({"role": "user", "content": query})
        
        # Generate SQL (first iteration)
        await self.generate_sql_iteration(session, query)
        
        # Main query refinement loop
        while True:
            current_iteration = self.current_query_iterations[-1]
            
            # Display the generated SQL
            print("\n===== GENERATED SQL QUERY =====")
            print(current_iteration.generated_sql)
            print("===============================")
            
            # Get user decision - wait for execute, refine, or cancel
            print("\nDo you want to (e)xecute this query, provide (f)eedback to refine it, or (c)ancel? (e/f/c): ")
            decision = get_input("").strip().lower()
            
            if decision == 'c':
                print("Query canceled.")
                break
            
            elif decision == 'f':
                feedback = get_input("Enter your feedback for improving the SQL query: ")
                current_iteration.feedback = feedback
                
                # Generate new SQL iteration based on feedback
                await self.generate_sql_iteration(session, query, feedback)
                continue
            
            elif decision == 'e':
                # Execute the query
                current_iteration.executed = True
                
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
                        log_result = await session.call_tool("save_query_log", {
                            "natural_language_query": query,
                            "sql_query": current_iteration.generated_sql,
                            "result_summary": safe_result_summary,
                            "iterations": iterations_data
                        })
                        log_message = getattr(log_result.content[0], "text", "")
                        print(f"\n{log_message}")
                    except Exception as log_err:
                        print(f"Error saving query log: {log_err}")
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
                    await self.generate_result_explanation(session, query, current_iteration.generated_sql, result_text)
                    
                except Exception as e:
                    error_message = f"Error executing query: {str(e)}"
                    print(f"\n===== QUERY ERROR =====")
                    print(error_message)
                    print("========================\n")
                    self.messages.append({"role": "system", "content": error_message})
                
                break
            
            else:
                print(f"Invalid choice: {decision}. Please enter 'e' to execute, 'f' for feedback, or 'c' to cancel.")

    def display_query_results(self, result_text: str) -> None:
        """Extract and display the tabular results from the query execution."""
        print("\n===== QUERY RESULTS =====")
        
        if result_text.startswith("Error:"):
            print(result_text)
            return
        
        # Split off JSON data if present
        display_text = result_text.split("\n\nJSON_DATA:")[0] if "JSON_DATA:" in result_text else result_text
        print(display_text)
        
        # Extract JSON data for potential programmatic use
        if "JSON_DATA:" in result_text:
            try:
                json_str = result_text.split("JSON_DATA:")[1]
                # This would be available for programmatic use but we don't display it
                json_data = json.loads(json_str)
            except json.JSONDecodeError as e:
                print(f"\nWarning: Could not parse JSON results: {str(e)}")
                # Try to extract the JSON data with manual processing if the automatic parsing failed
                try:
                    # This is a fallback for when standard JSON parsing fails
                    json_str = result_text.split("JSON_DATA:")[1].strip()
                    # Replace common problematic values that might cause JSON parsing issues
                    json_str = json_str.replace('NaN', '"NaN"').replace('Infinity', '"Infinity"').replace('-Infinity', '"-Infinity"')
                    json_data = json.loads(json_str)
                    print("Successfully recovered JSON data with fallback method.")
                except Exception as deep_error:
                    print(f"Failed to recover JSON data: {deep_error}")
        
        print("==========================\n")
    
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
            self.messages.append({"role": "assistant", "content": explanation})
            return
            
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
            
            # Add explanation to conversation history
            self.messages.append({"role": "assistant", "content": explanation})
        except Exception as e:
            print(f"Error generating result explanation: {str(e)}") 

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
        """Run diagnostics to troubleshoot table access issues."""
        print(f"\n===== RUNNING DIAGNOSTICS FOR TABLE {FULLY_QUALIFIED_TABLE_NAME} =====")
        try:
            # Test table access diagnostics
            print("Testing table access...")
            result = await session.call_tool("diagnose_table_access", {})
            diagnostics = getattr(result.content[0], "text", "")
            print("\nDiagnostic Results:")
            print(diagnostics)
            
            # Test basic table info
            print("\nRetrieving basic table information...")
            basic_result = await session.call_tool("get_table_info", {})
            basic_info = getattr(basic_result.content[0], "text", "")
            print("\nBasic Table Information:")
            print(basic_info)
            
            print("\nDiagnostics complete. If you're experiencing issues:")
            print(f"1. Check if the table {FULLY_QUALIFIED_TABLE_NAME} exists")
            print("2. Verify that your user has permissions to access this table")
            print("3. Check the logs directory for detailed error traces")
            print("4. Use /refresh_schema to attempt to reload the schema")
        except Exception as e:
            print(f"Error running diagnostics: {e}")
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
        print(f"\nTable Assistant is ready. You are working with table: {FULLY_QUALIFIED_TABLE_NAME}")
        print("Type your questions about the table in natural language, and I'll translate them to SQL.")
        print("Special commands:")
        print("  /diagnose - Run diagnostics")
        print("  /refresh_schema - Refresh table schema")
        print("  /history - View query history")
        print("  /show-logs [n] - View recent query logs (default: 5)")
        
        while True:
            try:
                query = get_input("\nEnter your Query (or type /exit to quit): ").strip()
                
                if not query:
                    continue
                    
                if query.lower() == "/exit":
                    print("\nExiting...")
                    break
                    
                # Special commands
                if query.lower() == "/diagnose":
                    await self.run_diagnostics(session)
                    continue
                elif query.lower() == "/refresh_schema":
                    await self.fetch_schema(session)
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
                
                # Process regular queries
                await self.process_query(session, query)
                
            except Exception as e:
                print(f"Error in chat loop: {e}")
                # Sleep briefly to avoid busy-waiting in case of persistent errors
                await asyncio.sleep(1)

    async def run(self):
        """Main entry point to run the chat session."""
        try:
            # Initialize with MCP server - fix the dict has no attribute 'command' error
            async with stdio_client() as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    
                    # Fetch schema information before starting chat loop
                    await self.fetch_schema(session)
                    
                    # Start the interactive chat loop
                    await self.chat_loop(session)
        except Exception as e:
            print(f"Error running chat: {e}")
            import traceback
            traceback.print_exc()

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