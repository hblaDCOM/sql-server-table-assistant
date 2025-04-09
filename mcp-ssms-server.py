import os
import pyodbc
from loguru import logger
import sys
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# Configure loguru logger
log_path = "logs"
os.makedirs(log_path, exist_ok=True)
log_file = os.path.join(log_path, f"mcp_server_{datetime.now().strftime('%Y%m%d')}.log")

# Remove default handler and add custom handlers
logger.remove()
# Console handler with INFO level
logger.add(sys.stderr, level="INFO")
# File handler with DEBUG level and rotation
logger.add(
    log_file, 
    level="DEBUG", 
    rotation="5 MB", 
    retention="1 week",
    backtrace=True, 
    diagnose=True
)

logger.info(f"Starting MCP SQL Server with logging to {log_file}")

# Database configurations
MSSQL_SERVER = os.getenv("MSSQL_SERVER", "localhost")
MSSQL_DATABASE = os.getenv("MSSQL_DATABASE", "my_database")
MSSQL_USERNAME = os.getenv("MSSQL_USERNAME", "sa")
MSSQL_PASSWORD = os.getenv("MSSQL_PASSWORD", "your_password")
MSSQL_DRIVER = os.getenv("MSSQL_DRIVER", "{ODBC Driver 17 for SQL Server}")

# Table configuration
MSSQL_TABLE_SCHEMA = os.getenv("MSSQL_TABLE_SCHEMA", "dbo")
MSSQL_TABLE_NAME = os.getenv("MSSQL_TABLE_NAME", "your_table_name")
FULLY_QUALIFIED_TABLE_NAME = f"{MSSQL_TABLE_SCHEMA}.{MSSQL_TABLE_NAME}" if MSSQL_TABLE_SCHEMA else MSSQL_TABLE_NAME

# Building the connection string
connection_string = (
    f"DRIVER={MSSQL_DRIVER};"
    f"SERVER={MSSQL_SERVER};"
    f"DATABASE={MSSQL_DATABASE};"
    f"UID={MSSQL_USERNAME};"
    f"PWD={MSSQL_PASSWORD};"
    f"Authentication=SqlPassword;"
    f"Encrypt=yes;"
    f"TrustServerCertificate=yes;"
    f"Connection Timeout=30"
)

logger.debug(f"Connection string created (password masked): DRIVER={MSSQL_DRIVER};SERVER={MSSQL_SERVER};DATABASE={MSSQL_DATABASE};UID={MSSQL_USERNAME};PWD=******;Authentication=SqlPassword;Encrypt=yes;TrustServerCertificate=yes")
logger.info(f"Configured to work with table: {FULLY_QUALIFIED_TABLE_NAME}")

# Creating an MCP server instance
mcp = FastMCP("Demo")

@mcp.tool()
def get_table_schema() -> str:
    """Retrieve schema information for the specific table."""
    logger.info(f"Retrieving schema information for table {FULLY_QUALIFIED_TABLE_NAME}...")
    try:
        # Log connection attempt
        logger.debug(f"Attempting to connect to server: {MSSQL_SERVER}, database: {MSSQL_DATABASE}")
        
        conn = pyodbc.connect(connection_string)
        logger.debug("Database connection established successfully")
        cursor = conn.cursor()
        
        schema_info = []
        schema_info.append(f"Table: {FULLY_QUALIFIED_TABLE_NAME}")
        
        # Get columns for the table
        try:
            logger.debug(f"Querying columns for {FULLY_QUALIFIED_TABLE_NAME}")
            cursor.execute(f"""
                SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                ORDER BY ORDINAL_POSITION
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            columns = cursor.fetchall()
            logger.debug(f"Found {len(columns)} columns for table {FULLY_QUALIFIED_TABLE_NAME}")
            
            if not columns:
                logger.warning(f"No columns found for table {FULLY_QUALIFIED_TABLE_NAME}")
                return f"No columns found for table {FULLY_QUALIFIED_TABLE_NAME}. Please check if the table exists and you have access to it."
            
            column_details = []
            for col_name, data_type, max_length, is_nullable in columns:
                nullable_str = "NULL" if is_nullable == 'YES' else "NOT NULL"
                if max_length:
                    column_details.append(f"{col_name} {data_type}({max_length}) {nullable_str}")
                else:
                    column_details.append(f"{col_name} {data_type} {nullable_str}")
            
            schema_info.append("Columns: " + ", ".join(column_details))
        except Exception as e:
            error_msg = f"Error retrieving columns for {FULLY_QUALIFIED_TABLE_NAME}: {str(e)}"
            logger.error(error_msg)
            schema_info.append(f"Error: {error_msg}")
        
        # Get primary keys
        try:
            logger.debug(f"Querying primary keys for {FULLY_QUALIFIED_TABLE_NAME}")
            cursor.execute(f"""
                SELECT c.COLUMN_NAME
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE c 
                    ON c.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY' 
                    AND tc.TABLE_SCHEMA = ? 
                    AND tc.TABLE_NAME = ?
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            pk_columns = [row[0] for row in cursor.fetchall()]
            if pk_columns:
                logger.debug(f"Found primary keys for {FULLY_QUALIFIED_TABLE_NAME}: {', '.join(pk_columns)}")
                schema_info.append(f"Primary Key: {', '.join(pk_columns)}")
            else:
                logger.debug(f"No primary keys found for {FULLY_QUALIFIED_TABLE_NAME}")
        except Exception as e:
            error_msg = f"Error getting primary keys for {FULLY_QUALIFIED_TABLE_NAME}: {str(e)}"
            logger.error(error_msg)
        
        # Add a sample query
        schema_info.append(f"Sample Select Query: SELECT TOP 5 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
        schema_info.append(f"Sample Count Query: SELECT COUNT(*) FROM {FULLY_QUALIFIED_TABLE_NAME}")
        
        # Add sample data if available
        try:
            cursor.execute(f"SELECT TOP 5 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
            sample_rows = cursor.fetchall()
            
            if sample_rows and cursor.description:
                column_names = [column[0] for column in cursor.description]
                schema_info.append("\nSample Data:")
                schema_info.append(f"Columns: {', '.join(column_names)}")
                
                for i, row in enumerate(sample_rows):
                    schema_info.append(f"Row {i+1}: {str(row)}")
        except Exception as e:
            logger.warning(f"Could not retrieve sample data: {str(e)}")
        
        logger.info("Successfully retrieved table schema information")
        return "\n".join(schema_info)
    except pyodbc.Error as e:
        error_msg = f"ODBC Error retrieving schema: {str(e)}"
        logger.error(error_msg)
        error_details = f"""
Database connection error details:
- Error: {str(e)}
- Server: {MSSQL_SERVER}
- Database: {MSSQL_DATABASE}
- Table: {FULLY_QUALIFIED_TABLE_NAME}
- Username: {MSSQL_USERNAME}
- Driver: {MSSQL_DRIVER}

Please check your connection settings in the .env file and ensure you have access to the specified table.
"""
        return error_details
    except Exception as e:
        error_msg = f"Unexpected error retrieving schema: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return f"Error retrieving schema: {str(e)}\n\nCheck server logs for detailed stack trace."
    finally:
        if 'conn' in locals():
            logger.debug("Closing database connection")
            conn.close()

@mcp.tool()
def query_table(sql: str) -> str:
    """Execute SQL queries on the specific table."""
    logger.info(f"Processing query for table {FULLY_QUALIFIED_TABLE_NAME}...")
    
    # Security check: ensure query only accesses the allowed table
    sql_upper = sql.upper()
    table_reference = FULLY_QUALIFIED_TABLE_NAME.upper()
    table_name_only = MSSQL_TABLE_NAME.upper()
    
    # Check if the query references tables other than the allowed one
    if "FROM" in sql_upper or "JOIN" in sql_upper or "INTO" in sql_upper or "UPDATE" in sql_upper:
        if table_reference not in sql_upper and table_name_only not in sql_upper:
            error_msg = f"Security error: Query must reference only the allowed table: {FULLY_QUALIFIED_TABLE_NAME}"
            logger.warning(error_msg)
            return f"Error: {error_msg}"
    
    conn = None
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
        logger.error(f"Error executing query: {str(e)}", exc_info=True)
        return f"Error: {str(e)}"
    finally:
        if conn:
            conn.close()

@mcp.prompt()
def example_prompt(code: str) -> str:
    return f"Please review this code:\n\n{code}"

@mcp.tool()
def get_table_info() -> str:
    """Get basic table information when schema retrieval fails."""
    logger.info(f"Attempting to retrieve basic table information for {FULLY_QUALIFIED_TABLE_NAME}...")
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        info = []
        info.append(f"Server: {MSSQL_SERVER}")
        info.append(f"Database: {MSSQL_DATABASE}")
        info.append(f"Table: {FULLY_QUALIFIED_TABLE_NAME}")
        
        # Attempt to get row count 
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {FULLY_QUALIFIED_TABLE_NAME}")
            row_count = cursor.fetchone()[0]
            info.append(f"Row count: {row_count}")
        except Exception as e:
            logger.error(f"Error getting row count: {e}")
            info.append("Row count: Unable to retrieve")
            
        # Attempt to verify table existence
        try:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            table_exists = cursor.fetchone()[0] > 0
            if table_exists:
                info.append("Status: Table exists and is accessible")
            else:
                info.append("Status: Table does not exist or is not accessible to this user")
        except Exception as e:
            logger.error(f"Error verifying table existence: {e}")
            info.append("Status: Unable to verify table existence")
        
        return "\n".join(info)
    except Exception as e:
        logger.error(f"Error retrieving basic table info: {e}", exc_info=True)
        return f"Failed to retrieve basic table information: {str(e)}"
    finally:
        if 'conn' in locals():
            conn.close()

@mcp.tool()
def diagnose_table_access() -> str:
    """Run diagnostics on table access."""
    logger.info(f"Running table access diagnostics for {FULLY_QUALIFIED_TABLE_NAME}...")
    results = []
    results.append(f"=== SQL Server Table Access Diagnostics for {FULLY_QUALIFIED_TABLE_NAME} ===")
    
    # Test basic connectivity
    results.append("\n1. Testing database connectivity:")
    try:
        conn = pyodbc.connect(connection_string, timeout=5)
        results.append("✓ Successfully connected to the database server")
        conn.close()
    except Exception as e:
        results.append(f"✗ Connection failed: {str(e)}")
        logger.error(f"Connection diagnostic failed: {e}", exc_info=True)
        results.append("\nCannot proceed with further diagnostics due to connection failure.")
        return "\n".join(results)
    
    # Test table existence
    results.append("\n2. Testing table existence:")
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
        
        table_exists = cursor.fetchone()[0] > 0
        if table_exists:
            results.append(f"✓ Table {FULLY_QUALIFIED_TABLE_NAME} exists")
        else:
            results.append(f"✗ Table {FULLY_QUALIFIED_TABLE_NAME} does not exist")
            results.append("\nCannot proceed with further diagnostics as table does not exist.")
            conn.close()
            return "\n".join(results)
        
        conn.close()
    except Exception as e:
        results.append(f"✗ Table existence check failed: {str(e)}")
        logger.error(f"Table existence check failed: {e}", exc_info=True)
        results.append("\nCannot proceed with further diagnostics due to error.")
        return "\n".join(results)
    
    # Test table permissions
    results.append("\n3. Testing table permissions:")
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        # Test SELECT permission
        try:
            cursor.execute(f"SELECT TOP 1 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
            cursor.fetchone()
            results.append("✓ User has SELECT permission")
        except Exception as e:
            results.append(f"✗ User does not have SELECT permission: {str(e)}")
        
        # Test INSERT permission (using a transaction that we'll roll back)
        try:
            # Start a transaction
            cursor.execute("BEGIN TRANSACTION")
            
            # Get column info for INSERT test
            cursor.execute(f"""
                SELECT COLUMN_NAME, DATA_TYPE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                AND IS_NULLABLE = 'YES'
                ORDER BY ORDINAL_POSITION
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            nullable_columns = cursor.fetchall()
            if not nullable_columns:
                results.append("✗ Could not test INSERT permission: No nullable columns found")
            else:
                # Build a test INSERT with NULL values
                col_names = ", ".join([f"[{col[0]}]" for col in nullable_columns])
                null_values = ", ".join(["NULL" for _ in nullable_columns])
                
                try:
                    insert_sql = f"INSERT INTO {FULLY_QUALIFIED_TABLE_NAME} ({col_names}) VALUES ({null_values})"
                    cursor.execute(insert_sql)
                    results.append("✓ User has INSERT permission")
                except Exception as e:
                    results.append(f"✗ User does not have INSERT permission: {str(e)}")
                
            # Roll back the transaction
            cursor.execute("ROLLBACK TRANSACTION")
        except Exception as e:
            results.append(f"✗ INSERT permission test failed: {str(e)}")
            # Make sure we roll back
            try:
                cursor.execute("ROLLBACK TRANSACTION")
            except:
                pass
        
        # Test UPDATE permission (in a transaction)
        try:
            # Start a transaction
            cursor.execute("BEGIN TRANSACTION")
            
            # Get a column for UPDATE test
            cursor.execute(f"""
                SELECT TOP 1 COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                AND IS_NULLABLE = 'YES'
                ORDER BY ORDINAL_POSITION
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            result = cursor.fetchone()
            if not result:
                results.append("✗ Could not test UPDATE permission: No suitable columns found")
            else:
                column_name = result[0]
                try:
                    # Test UPDATE with a condition that won't match anything
                    update_sql = f"UPDATE {FULLY_QUALIFIED_TABLE_NAME} SET [{column_name}] = NULL WHERE 1 = 0"
                    cursor.execute(update_sql)
                    results.append("✓ User has UPDATE permission")
                except Exception as e:
                    results.append(f"✗ User does not have UPDATE permission: {str(e)}")
            
            # Roll back the transaction
            cursor.execute("ROLLBACK TRANSACTION")
        except Exception as e:
            results.append(f"✗ UPDATE permission test failed: {str(e)}")
            # Make sure we roll back
            try:
                cursor.execute("ROLLBACK TRANSACTION")
            except:
                pass
        
        # Test DELETE permission (in a transaction)
        try:
            # Start a transaction
            cursor.execute("BEGIN TRANSACTION")
            
            try:
                # Test DELETE with a condition that won't match anything
                delete_sql = f"DELETE FROM {FULLY_QUALIFIED_TABLE_NAME} WHERE 1 = 0"
                cursor.execute(delete_sql)
                results.append("✓ User has DELETE permission")
            except Exception as e:
                results.append(f"✗ User does not have DELETE permission: {str(e)}")
            
            # Roll back the transaction
            cursor.execute("ROLLBACK TRANSACTION")
        except Exception as e:
            results.append(f"✗ DELETE permission test failed: {str(e)}")
            # Make sure we roll back
            try:
                cursor.execute("ROLLBACK TRANSACTION")
            except:
                pass
        
        conn.close()
    except Exception as e:
        results.append(f"✗ Permission testing failed: {str(e)}")
    
    logger.info("Table access diagnostics completed")
    return "\n".join(results)

if __name__ == "__main__":
    print("Starting server...")
    mcp.run(transport="stdio")
