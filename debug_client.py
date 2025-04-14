import os
import sys
import pyodbc
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get connection parameters from environment variables
SQL_SERVER = os.getenv("MSSQL_SERVER")
SQL_DATABASE = os.getenv("MSSQL_DATABASE")
SQL_USERNAME = os.getenv("MSSQL_USERNAME") 
SQL_PASSWORD = os.getenv("MSSQL_PASSWORD")
TABLE_NAME = os.getenv("MSSQL_TABLE_NAME", "change.Change_Cherwell_Sep24toFeb25")

print("=== DEBUG SQL CONNECTION AND PREVIEW ===")
print(f"Attempting to connect to: {SQL_SERVER}")
print(f"Database: {SQL_DATABASE}")
print(f"Table: {TABLE_NAME}")

# Test direct connection
try:
    print("\n--- Testing Direct SQL Connection ---")
    connection_string = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};UID={SQL_USERNAME};PWD={SQL_PASSWORD}"
    print(f"Connection string (partial): DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};UID={SQL_USERNAME};PWD=******")
    
    conn = pyodbc.connect(connection_string)
    cursor = conn.cursor()
    
    print("[SUCCESS] Connected to database")
    
    # Get server version
    cursor.execute("SELECT @@version")
    version = cursor.fetchone()[0]
    print(f"SQL Server version: {version[:100]}...")
    
    # Try to get preview
    print("\n--- Attempting to fetch data preview ---")
    # Ensure we're using the fully qualified table name with schema
    if "." not in TABLE_NAME and TABLE_NAME.startswith("Change_"):
        TABLE_NAME = "change." + TABLE_NAME
    query = f"SELECT TOP 5 * FROM {TABLE_NAME}"
    print(f"Query: {query}")
    
    start_time = time.time()
    print("Executing query...")
    cursor.execute(query)
    
    print(f"Query executed in {time.time() - start_time:.2f} seconds")
    
    # Fetch column names
    columns = [column[0] for column in cursor.description]
    print(f"Columns: {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}")
    
    # Fetch and display rows
    print("\nData preview:")
    rows = cursor.fetchall()
    print(f"Retrieved {len(rows)} rows")
    
    if rows:
        # Display up to first 5 columns of each row
        for i, row in enumerate(rows):
            print(f"Row {i+1}:", end=" ")
            for j in range(min(5, len(columns))):
                val = str(row[j])
                if len(val) > 30:
                    val = val[:30] + "..."
                print(f"{columns[j]}={val}", end=" | ")
            print("...")
    else:
        print("No data found in table.")
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"[ERROR] {type(e).__name__}: {str(e)}")
    import traceback
    traceback.print_exc()

print("\n=== Debugging Complete ===") 