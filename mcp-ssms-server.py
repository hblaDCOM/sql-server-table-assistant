import os
import pyodbc
from loguru import logger
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# Database configurations
MSSQL_SERVER = os.getenv("MSSQL_SERVER", "localhost")
MSSQL_DATABASE = os.getenv("MSSQL_DATABASE", "my_database")
MSSQL_USERNAME = os.getenv("MSSQL_USERNAME", "sa")
MSSQL_PASSWORD = os.getenv("MSSQL_PASSWORD", "your_password")
MSSQL_DRIVER = os.getenv("MSSQL_DRIVER", "{ODBC Driver 17 for SQL Server}")

# Building the connection string
connection_string = (
    f"DRIVER={MSSQL_DRIVER};"
    f"SERVER={MSSQL_SERVER};"
    f"DATABASE={MSSQL_DATABASE};"
    f"UID={MSSQL_USERNAME};"
    f"PWD={MSSQL_PASSWORD}"
)

# Creating an MCP server instance
mcp = FastMCP("Demo")

@mcp.tool()
def query_data(sql: str) -> str:
    """Execute SQL queries safely on MSSQL."""
    logger.info(f"Processing your Query...")
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        cursor.execute(sql)
        
        if cursor.description is not None:
            result = cursor.fetchall()
            output = "\n".join(str(row) for row in result)
        else:
            output = "SQL executed successfully, no results returned."
        
        conn.commit() 
        return output
    except Exception as e:
        logger.error("Error executing query: " + str(e))
        return f"Error: {str(e)}"
    finally:
        conn.close()

@mcp.prompt()
def example_prompt(code: str) -> str:
    return f"Please review this code:\n\n{code}"

if __name__ == "__main__":
    print("Starting server...")
    mcp.run(transport="stdio")
