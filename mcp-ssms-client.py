import asyncio
import os
import re
import json
from dataclasses import dataclass, field
from typing import cast
import sys
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
class Chat:
    messages: list[dict] = field(default_factory=list)
    table_schema: str = ""
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
        "5. Use appropriate SQL Server syntax for your queries\n\n"
        "When you need to execute a SQL query, you MUST use this EXACT format with no additional text:\n"
        "TOOL: query_table, ARGS: {\"sql\": \"<YOUR_SQL_QUERY>\"}\n\n"
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

    async def process_query(self, session: ClientSession, query: str) -> None:
        # 1) Gather available tools (for reference only)
        response = await session.list_tools()
        available_tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in response.tools
        ]

        # 2) Build the conversation for OpenAI
        openai_messages = [
            {"role": "system", "content": self.system_prompt},
        ]
        openai_messages.extend(self.messages)
        openai_messages.append({"role": "user", "content": query})

        # 3) Send to OpenAI
        completion_params = {
            "messages": openai_messages,
            "max_tokens": 2000,
            "temperature": 0.0,
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")  # Azure uses deployment_id as the model name
        }
            
        completion = client.chat.completions.create(**completion_params)

        assistant_reply = completion.choices[0].message.content

        self.messages.append({"role": "user", "content": query})
        self.messages.append({"role": "assistant", "content": assistant_reply})

        # 4) Look for a tool call in the assistant reply
        print("\nRaw assistant response:")
        print(assistant_reply)
        
        if "TOOL:" in assistant_reply:
            try:
                print("\nDetected TOOL: pattern, attempting to extract...")
                pattern = r"TOOL:\s*(\w+),\s*ARGS:\s*(\{.*\})"
                match = re.search(pattern, assistant_reply)
                if match:
                    tool_name = match.group(1)
                    tool_args_str = match.group(2)
                    print(f"Extracted tool_name: {tool_name}")
                    print(f"Extracted args string: {tool_args_str}")
                    
                    try:
                        tool_args = json.loads(tool_args_str)
                        print(f"Parsed tool args: {tool_args}")
                    except json.JSONDecodeError as json_err:
                        print(f"JSON parsing error: {json_err}")
                        # Try to extract SQL directly as fallback
                        sql_pattern = r'"sql":\s*"(.+?)"'
                        sql_match = re.search(sql_pattern, tool_args_str)
                        if sql_match:
                            sql = sql_match.group(1)
                            tool_args = {"sql": sql}
                            print(f"Extracted SQL with regex fallback: {sql}")
                        else:
                            raise Exception("Could not parse SQL command")

                    # Now call the tool on the server
                    print(f"Calling tool '{tool_name}' with args: {tool_args}")
                    
                    # User validation step
                    sql_query = tool_args.get("sql", "")
                    if sql_query:
                        print("\n===== SQL QUERY VALIDATION =====")
                        print(f"The model wants to execute the following SQL query:")
                        print(f"\n{sql_query}\n")
                        approval = get_input("Do you want to execute this query? (y/n): ").strip().lower()
                        if approval != 'y':
                            print("Query execution canceled by user.")
                            tool_text = "Query execution was canceled by the user."
                            tool_result_msg = f"Tool '{tool_name}' result:\n{tool_text}"
                            self.messages.append({"role": "system", "content": tool_result_msg})
                            return
                        
                        # Check if this is a DDL operation (CREATE, ALTER, DROP)
                        should_refresh_schema = any(ddl_keyword in sql_query.upper() 
                                                 for ddl_keyword in ["ALTER TABLE", "DROP TABLE"])
                    
                    result = await session.call_tool(tool_name, cast(dict, tool_args))
                    tool_text = getattr(result.content[0], "text", "")
                    print(f"Tool result: {tool_text[:200]}..." if len(tool_text) > 200 else f"Tool result: {tool_text}")

                    # Refresh schema if a DDL operation was performed successfully
                    if sql_query and should_refresh_schema and "Error" not in tool_text:
                        print("\nTable schema may have changed. Refreshing schema information...")
                        await self.fetch_schema(session)
                        # Add a note about the schema refresh to the conversation
                        self.messages.append({
                            "role": "system", 
                            "content": "Note: Table schema has been refreshed due to structural changes."
                        })
                    
                    tool_result_msg = f"Tool '{tool_name}' result:\n{tool_text}"
                    self.messages.append({"role": "system", "content": tool_result_msg})
                    
                    completion_params_2 = {
                        "messages": [{"role": "system", "content": self.system_prompt}] + self.messages,
                        "max_tokens": 1000,
                        "temperature": 0.0,
                        "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")  # Azure uses deployment_id as the model name
                    }
                        
                    completion_2 = client.chat.completions.create(**completion_params_2)
                    final_reply = completion_2.choices[0].message.content
                    print("\nAssistant:", final_reply)
                    self.messages.append({"role": "assistant", "content": final_reply})
                else:
                    print("No valid tool command found in assistant response.")
                    print("Trying fallback pattern matching...")
                    
                    # Fallback pattern matching
                    sql_pattern = r'```sql\s*(.*?)\s*```'
                    sql_match = re.search(sql_pattern, assistant_reply, re.DOTALL)
                    if sql_match:
                        sql = sql_match.group(1).strip()
                        print(f"Extracted SQL via code block: {sql}")
                        tool_name = "query_table"
                        tool_args = {"sql": sql}
                        
                        try:
                            print(f"Calling tool '{tool_name}' with extracted SQL")
                            
                            # User validation step
                            if sql:
                                print("\n===== SQL QUERY VALIDATION =====")
                                print(f"The model wants to execute the following SQL query:")
                                print(f"\n{sql}\n")
                                approval = get_input("Do you want to execute this query? (y/n): ").strip().lower()
                                if approval != 'y':
                                    print("Query execution canceled by user.")
                                    tool_text = "Query execution was canceled by the user."
                                    tool_result_msg = f"Tool '{tool_name}' result:\n{tool_text}"
                                    self.messages.append({"role": "system", "content": tool_result_msg})
                                    return
                                
                                # Check if this is a DDL operation (ALTER, DROP)
                                should_refresh_schema = any(ddl_keyword in sql.upper() 
                                                         for ddl_keyword in ["ALTER TABLE", "DROP TABLE"])
                            
                            result = await session.call_tool(tool_name, cast(dict, tool_args))
                            tool_text = getattr(result.content[0], "text", "")
                            print(f"Tool result: {tool_text[:200]}..." if len(tool_text) > 200 else f"Tool result: {tool_text}")
                            
                            # Refresh schema if a DDL operation was performed successfully
                            if sql and should_refresh_schema and "Error" not in tool_text:
                                print("\nTable schema may have changed. Refreshing schema information...")
                                await self.fetch_schema(session)
                                # Add a note about the schema refresh to the conversation
                                self.messages.append({
                                    "role": "system", 
                                    "content": "Note: Table schema has been refreshed due to structural changes."
                                })
                            
                            tool_result_msg = f"Tool '{tool_name}' result:\n{tool_text}"
                            self.messages.append({"role": "system", "content": tool_result_msg})
                            
                            completion_params_2 = {
                                "messages": [{"role": "system", "content": self.system_prompt}] + self.messages,
                                "max_tokens": 1000,
                                "temperature": 0.0,
                                "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")
                            }
                            
                            completion_2 = client.chat.completions.create(**completion_params_2)
                            final_reply = completion_2.choices[0].message.content
                            print("\nAssistant:", final_reply)
                            self.messages.append({"role": "assistant", "content": final_reply})
                        except Exception as e:
                            print(f"Failed to execute extracted SQL: {e}")
                    else:
                        print("Could not find SQL in code blocks either.")
            except Exception as e:
                print(f"Failed to parse tool usage: {e}")

    async def chat_loop(self, session: ClientSession):
        print(f"\nTable Assistant is ready. You are working with table: {FULLY_QUALIFIED_TABLE_NAME}")
        print("Type your questions about the table in natural language, and I'll translate them to SQL.")
        print("Special commands: /diagnose - Run diagnostics, /refresh_schema - Refresh table schema")
        
        while True:
            try:
                query = get_input("\nEnter your Query (Press ESC to Quit): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting...")
                break
                
            if not query:
                break
                
            # Special commands for diagnostics
            if query.lower() == "/diagnose":
                await self.run_diagnostics(session)
                continue
            elif query.lower() == "/refresh_schema":
                await self.fetch_schema(session)
                continue
            
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
    chat = Chat()
    asyncio.run(chat.run())
