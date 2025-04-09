import asyncio
import os
import re
import json
import time
from dataclasses import dataclass, field
from typing import cast, List, Dict, Any, Optional
import sys
from datetime import datetime
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

# Get the table configuration
TABLE_SCHEMA = os.getenv("MSSQL_TABLE_SCHEMA", "dbo")
TABLE_NAME = os.getenv("MSSQL_TABLE_NAME", "your_table_name")
FULLY_QUALIFIED_TABLE_NAME = f"{TABLE_SCHEMA}.{TABLE_NAME}" if TABLE_SCHEMA else TABLE_NAME

# Using Azure OpenAI only
from openai import AzureOpenAI
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),  
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2023-05-15"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

# Create server parameters for stdio connection
server_params = StdioServerParameters(
    command="python",          # Executable
    args=["./mcp-ssms-server.py"],  # Command line arguments to run the server script
    env=None,                  # Optional environment variables
)

if os.name == 'nt':
    import msvcrt

    def get_input(prompt: str) -> str:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        buf = []
        while True:
            ch = msvcrt.getwch()
            if ch == '\r':
                sys.stdout.write('\n')
                return ''.join(buf)
            elif ch == '\x1b':
                raise KeyboardInterrupt
            elif ch == '\b':
                if buf:
                    buf.pop()
                    sys.stdout.write('\b \b')
            else:
                buf.append(ch)
                sys.stdout.write(ch)
                sys.stdout.flush()

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
    current_query_iterations: List[QueryIteration] = field(default_factory=list)
    query_history: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = (
        "You are a MS SQL Server assistant focused on the specific table {table_name}. "
        "Your job is to help users query and interact with this table using SQL. "
        "You have access to a single table and cannot access any other tables in the database.\n\n"
        "{schema_info}\n\n"
        "When writing SQL queries:\n"
        "1. Only reference the table {table_name} in your queries\n"
        "2. Always reference columns exactly as they appear in the schema\n"
        "3. Pay attention to data types when filtering\n"
        "4. Consider the nullability of columns in your WHERE clauses\n"
        "5. Use appropriate SQL Server syntax for your queries\n"
        "6. Make your queries efficient with appropriate WHERE clauses\n"
        "7. Include ORDER BY clauses when relevant for better presentation\n\n"
        "If the user provides feedback on your SQL query, incorporate that feedback in your next iteration.\n\n"
        "When you need to execute a SQL query, you MUST use this EXACT format with no additional text:\n"
        "TOOL: query_table, ARGS: {{\"sql\": \"<YOUR_SQL_QUERY>\"}}\n\n"
        "This is critical: always formulate valid SQL queries that only work with the table {table_name} "
        "and use the exact TOOL format above. Be precise and careful with your SQL queries as they "
        "will be shown to the user for approval before execution. Use the provided table schema to "
        "write accurate queries with correct column names. Do not explain that you're going to execute "
        "a query, just execute it directly."
    )

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
                
                # Combine error and basic info
                self.table_schema = f"""
Schema retrieval encountered errors. Limited table information available:

{basic_info}

(Note: Full schema details could not be retrieved. SQL generation may be limited.)
"""
            except Exception as basic_error:
                print(f"Error retrieving basic table info: {basic_error}")
                self.table_schema = "Both full schema and basic table information retrieval failed."
            
        # Update the system prompt with schema information
        formatted_schema_info = f"TABLE SCHEMA INFORMATION:\n{self.table_schema}" if self.table_schema else "Schema information not available."
        
        try:
            self.system_prompt = self.system_prompt.format(
                schema_info=formatted_schema_info,
                table_name=FULLY_QUALIFIED_TABLE_NAME
            )
            print("System prompt updated with table schema information.")
        except Exception as format_error:
            print(f"Error formatting system prompt: {format_error}")
            # Fallback to direct replacement if formatting fails
            self.system_prompt = self.system_prompt.replace("{schema_info}", formatted_schema_info)
            self.system_prompt = self.system_prompt.replace("{table_name}", FULLY_QUALIFIED_TABLE_NAME)

    def extract_sql_from_assistant_reply(self, assistant_reply: str) -> Optional[Dict[str, Any]]:
        """Extract SQL query from assistant reply using multiple methods."""
        # Try the TOOL format first
        if "TOOL:" in assistant_reply:
            try:
                pattern = r"TOOL:\s*(\w+),\s*ARGS:\s*(\{.*\})"
                match = re.search(pattern, assistant_reply)
                if match:
                    tool_name = match.group(1)
                    tool_args_str = match.group(2)
                    
                    try:
                        tool_args = json.loads(tool_args_str)
                        return {"tool_name": tool_name, "args": tool_args}
                    except json.JSONDecodeError as json_err:
                        # Try to extract SQL directly as fallback
                        sql_pattern = r'"sql":\s*"(.+?)"'
                        sql_match = re.search(sql_pattern, tool_args_str)
                        if sql_match:
                            sql = sql_match.group(1)
                            return {"tool_name": tool_name, "args": {"sql": sql}}
            except Exception:
                pass
        
        # Try SQL code block extraction as fallback
        sql_pattern = r'```sql\s*(.*?)\s*```'
        sql_match = re.search(sql_pattern, assistant_reply, re.DOTALL)
        if sql_match:
            sql = sql_match.group(1).strip()
            return {"tool_name": "query_table", "args": {"sql": sql}}
        
        # Try direct SQL extraction (for cases where model outputs just the SQL)
        if re.search(r'\bSELECT\b', assistant_reply, re.IGNORECASE) and \
           re.search(r'\bFROM\b', assistant_reply, re.IGNORECASE):
            # Extract what looks like a SQL query
            lines = assistant_reply.split('\n')
            sql_lines = []
            for line in lines:
                if re.search(r'\b(SELECT|FROM|WHERE|ORDER BY|GROUP BY|HAVING|JOIN)\b', line, re.IGNORECASE):
                    sql_lines.append(line)
            
            if sql_lines:
                potential_sql = ' '.join(sql_lines)
                return {"tool_name": "query_table", "args": {"sql": potential_sql}}
        
        return None

    async def process_query(self, session: ClientSession, query: str) -> None:
        """Process a natural language query, generate SQL, and execute it with user approval."""
        print(f"\nProcessing query: {query}")
        
        # Reset query iterations for new query
        self.current_query_iterations = []
        
        # Add user query to conversation history
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
            
            # Get user decision
            decision = get_input("\nDo you want to (e)xecute this query, provide (f)eedback to refine it, or (c)ancel? (e/f/c): ").strip().lower()
            
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
                    result = await session.call_tool("query_table", {"sql": current_iteration.generated_sql})
                    result_text = getattr(result.content[0], "text", "")
                    current_iteration.results = result_text
                    
                    # Extract and display tabular results
                    self.display_query_results(result_text)
                    
                    # Add the result to conversation history
                    self.messages.append({
                        "role": "system",
                        "content": f"SQL query executed:\n{current_iteration.generated_sql}\n\nResults:\n{result_text}"
                    })
                    
                    # Save query log
                    iterations_data = []
                    for i, iter_data in enumerate(self.current_query_iterations):
                        iterations_data.append({
                            "iteration": i + 1,
                            "sql": iter_data.generated_sql,
                            "feedback": iter_data.feedback,
                            "executed": iter_data.executed
                        })
                    
                    try:
                        log_result = await session.call_tool("save_query_log", {
                            "natural_language_query": query,
                            "sql_query": current_iteration.generated_sql,
                            "result_summary": result_text,
                            "iterations": iterations_data
                        })
                        log_message = getattr(log_result.content[0], "text", "")
                        print(f"\n{log_message}")
                    except Exception as log_err:
                        print(f"Error saving query log: {log_err}")
                    
                    # Add to query history
                    query_record = {
                        "timestamp": datetime.now().isoformat(),
                        "natural_language": query,
                        "final_sql": current_iteration.generated_sql,
                        "iterations": len(self.current_query_iterations),
                        "success": not result_text.startswith("Error")
                    }
                    self.query_history.append(query_record)
                    
                    # Generate natural language explanation of results
                    await self.generate_result_explanation(session, query, current_iteration.generated_sql, result_text)
                    
                except Exception as e:
                    error_message = f"Error executing query: {str(e)}"
                    print(f"\n===== QUERY ERROR =====")
                    print(error_message)
                    print("========================\n")
                    self.messages.append({"role": "system", "content": error_message})
                
                break
            
            else:
                print("Invalid choice. Please enter 'e' to execute, 'f' for feedback, or 'c' to cancel.")
    
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
            json_str = result_text.split("JSON_DATA:")[1]
            try:
                # This would be available for programmatic use but we don't display it
                json_data = json.loads(json_str)
            except:
                pass
        
        print("==========================\n")
    
    async def generate_sql_iteration(self, session: ClientSession, original_query: str, feedback: str = None) -> None:
        """Generate a SQL query iteration based on the original query and optional feedback."""
        iteration_number = len(self.current_query_iterations) + 1
        
        # Build the prompt based on iteration
        prompt = original_query
        
        if iteration_number > 1 and feedback:
            # For subsequent iterations, include the original query, previous SQL, and feedback
            previous_sql = self.current_query_iterations[-1].generated_sql
            prompt = (
                f"Original question: {original_query}\n\n"
                f"Your previous SQL query: {previous_sql}\n\n"
                f"My feedback: {feedback}\n\n"
                f"Please generate an improved SQL query that addresses this feedback."
            )
        
        # Build conversation for OpenAI
        openai_messages = [
            {"role": "system", "content": self.system_prompt},
        ]
        
        # Include previous iterations in context if this isn't the first iteration
        if iteration_number > 1:
            # Add relevant conversation history but exclude the most recent feedback request
            for msg in self.messages[:-1]:  
                openai_messages.append(msg)
        
        openai_messages.append({"role": "user", "content": prompt})
        
        # Send to OpenAI
        completion_params = {
            "messages": openai_messages,
            "max_tokens": 2000,
            "temperature": 0.0,
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")
        }
        
        try:
            completion = client.chat.completions.create(**completion_params)
            assistant_reply = completion.choices[0].message.content
            
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
                
                # For first iteration, add assistant's full response to conversation history
                if iteration_number == 1:
                    self.messages.append({"role": "assistant", "content": assistant_reply})
                
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
        except Exception as e:
            print(f"Error generating SQL: {str(e)}")
    
    async def generate_result_explanation(self, session: ClientSession, 
                                         query: str, sql: str, results: str) -> None:
        """Generate a natural language explanation of the query results."""
        # Extract just the tabular part for the explanation (without the JSON)
        results_for_explanation = results.split("\n\nJSON_DATA:")[0] if "JSON_DATA:" in results else results
        
        prompt = (
            f"I executed the following SQL query to answer the question '{query}':\n\n"
            f"{sql}\n\n"
            f"Here are the results:\n{results_for_explanation}\n\n"
            f"Please provide a brief, clear explanation of these results in plain language. "
            f"If there were no results returned, explain why that might be the case."
        )
        
        # Build conversation for OpenAI
        openai_messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # Send to OpenAI
        completion_params = {
            "messages": openai_messages,
            "max_tokens": 1000,
            "temperature": 0.1,
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")
        }
        
        try:
            completion = client.chat.completions.create(**completion_params)
            explanation = completion.choices[0].message.content
            
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

    async def chat_loop(self, session: ClientSession):
        print(f"\nTable Assistant is ready. You are working with table: {FULLY_QUALIFIED_TABLE_NAME}")
        print("Type your questions about the table in natural language, and I'll translate them to SQL.")
        print("Special commands:")
        print("  /diagnose - Run diagnostics")
        print("  /refresh_schema - Refresh table schema")
        print("  /history - View query history")
        
        while True:
            try:
                query = get_input("\nEnter your Query (Press ESC to Quit): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting...")
                break
                
            if not query:
                continue
                
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
            
            # Process regular queries
            await self.process_query(session, query)
            
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

    async def run(self):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # Fetch schema information before starting chat loop
                await self.fetch_schema(session)
                
                await self.chat_loop(session)

if __name__ == "__main__":
    try:
        asyncio.run(Chat().run())
    except Exception as e:
        print(f"Application error: {e}")
        input("Press Enter to exit...")
