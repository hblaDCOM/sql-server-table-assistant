import os
import pyodbc
from loguru import logger
import sys
import json
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
from datetime import datetime
import tabulate
import re

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
MSSQL_DRIVER = os.getenv("MSSQL_DRIVER", "{ODBC Driver 18 for SQL Server}")

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
    """Retrieve detailed schema information for the specific table."""
    logger.info(f"Retrieving schema information for table {FULLY_QUALIFIED_TABLE_NAME}...")
    try:
        # Log connection attempt
        logger.debug(f"Attempting to connect to server: {MSSQL_SERVER}, database: {MSSQL_DATABASE}")
        
        conn = pyodbc.connect(connection_string)
        logger.debug("Database connection established successfully")
        cursor = conn.cursor()
        
        schema_info = []
        schema_info.append(f"Table: {FULLY_QUALIFIED_TABLE_NAME}")
        
        # Get columns for the table with comprehensive details
        try:
            logger.debug(f"Querying columns for {FULLY_QUALIFIED_TABLE_NAME}")
            cursor.execute(f"""
                SELECT 
                    COLUMN_NAME, 
                    DATA_TYPE, 
                    CHARACTER_MAXIMUM_LENGTH, 
                    IS_NULLABLE,
                    COLUMNPROPERTY(OBJECT_ID(CONCAT(TABLE_SCHEMA, '.', TABLE_NAME)), COLUMN_NAME, 'IsIdentity') AS IS_IDENTITY,
                    COLUMN_DEFAULT
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                ORDER BY ORDINAL_POSITION
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            columns = cursor.fetchall()
            logger.debug(f"Found {len(columns)} columns for table {FULLY_QUALIFIED_TABLE_NAME}")
            
            if not columns:
                logger.warning(f"No columns found for table {FULLY_QUALIFIED_TABLE_NAME}")
                return f"No columns found for table {FULLY_QUALIFIED_TABLE_NAME}. Please check if the table exists and you have access to it."
            
            schema_info.append("\nColumn Details:")
            column_details = []
            for col_name, data_type, max_length, is_nullable, is_identity, default_val in columns:
                nullable_str = "NULL" if is_nullable == 'YES' else "NOT NULL"
                identity_str = " IDENTITY" if is_identity == 1 else ""
                default_str = f" DEFAULT {default_val}" if default_val else ""
                
                if max_length and max_length != -1:
                    column_details.append(f"- {col_name}: {data_type}({max_length}) {nullable_str}{identity_str}{default_str}")
                elif data_type in ('varchar', 'nvarchar', 'char', 'nchar') and max_length == -1:
                    column_details.append(f"- {col_name}: {data_type}(MAX) {nullable_str}{identity_str}{default_str}")
                else:
                    column_details.append(f"- {col_name}: {data_type} {nullable_str}{identity_str}{default_str}")
            
            schema_info.extend(column_details)
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
                schema_info.append(f"\nPrimary Key: {', '.join(pk_columns)}")
            else:
                logger.debug(f"No primary keys found for {FULLY_QUALIFIED_TABLE_NAME}")
                schema_info.append("\nPrimary Key: None defined")
        except Exception as e:
            error_msg = f"Error getting primary keys for {FULLY_QUALIFIED_TABLE_NAME}: {str(e)}"
            logger.error(error_msg)
        
        # Get foreign keys
        try:
            logger.debug(f"Querying foreign keys for {FULLY_QUALIFIED_TABLE_NAME}")
            cursor.execute(f"""
                SELECT 
                    fk.name AS FK_NAME,
                    OBJECT_NAME(fk.parent_object_id) AS TABLE_NAME,
                    COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS COLUMN_NAME,
                    OBJECT_NAME(fk.referenced_object_id) AS REFERENCED_TABLE_NAME,
                    COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS REFERENCED_COLUMN_NAME
                FROM 
                    sys.foreign_keys AS fk
                INNER JOIN 
                    sys.foreign_key_columns AS fkc ON fk.OBJECT_ID = fkc.constraint_object_id
                INNER JOIN 
                    sys.tables AS t ON t.OBJECT_ID = fk.parent_object_id
                INNER JOIN 
                    sys.schemas AS s ON s.schema_id = t.schema_id
                WHERE 
                    s.name = ? AND t.name = ?
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            fk_results = cursor.fetchall()
            if fk_results:
                schema_info.append("\nForeign Keys:")
                for fk_name, _, column, ref_table, ref_column in fk_results:
                    schema_info.append(f"- {column} -> {ref_table}.{ref_column} (FK: {fk_name})")
            else:
                schema_info.append("\nForeign Keys: None defined")
        except Exception as e:
            error_msg = f"Error getting foreign keys: {str(e)}"
            logger.error(error_msg)
        
        # Get indexes
        try:
            logger.debug(f"Querying indexes for {FULLY_QUALIFIED_TABLE_NAME}")
            cursor.execute(f"""
                SELECT 
                    i.name AS INDEX_NAME,
                    i.type_desc AS INDEX_TYPE,
                    STRING_AGG(c.name, ', ') WITHIN GROUP (ORDER BY ic.key_ordinal) AS COLUMN_NAMES,
                    i.is_unique
                FROM 
                    sys.indexes i
                INNER JOIN 
                    sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
                INNER JOIN 
                    sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
                INNER JOIN 
                    sys.tables t ON i.object_id = t.object_id
                INNER JOIN 
                    sys.schemas s ON t.schema_id = s.schema_id
                WHERE 
                    s.name = ? AND t.name = ? AND i.name IS NOT NULL
                GROUP BY 
                    i.name, i.type_desc, i.is_unique
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            idx_results = cursor.fetchall()
            if idx_results:
                schema_info.append("\nIndexes:")
                for idx_name, idx_type, columns, is_unique in idx_results:
                    unique_str = "UNIQUE " if is_unique else ""
                    schema_info.append(f"- {idx_name}: {unique_str}{idx_type} on ({columns})")
            else:
                schema_info.append("\nIndexes: None defined (except for primary key)")
        except Exception as e:
            error_msg = f"Error getting indexes: {str(e)}"
            logger.error(error_msg)
        
        # Get table statistics
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {FULLY_QUALIFIED_TABLE_NAME}")
            row_count = cursor.fetchone()[0]
            schema_info.append(f"\nApproximate Row Count: {row_count}")
        except Exception as e:
            logger.warning(f"Could not retrieve row count: {str(e)}")
            schema_info.append("\nRow Count: Unable to retrieve")
            
        # Add sample queries
        schema_info.append(f"\nSample Queries:")
        schema_info.append(f"- SELECT TOP 5 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
        schema_info.append(f"- SELECT COUNT(*) FROM {FULLY_QUALIFIED_TABLE_NAME}")
        
        # If primary key exists, add a sample query using it
        if pk_columns:
            pk_conditions = " AND ".join([f"{pk} = @value" for pk in pk_columns])
            schema_info.append(f"- SELECT * FROM {FULLY_QUALIFIED_TABLE_NAME} WHERE {pk_conditions}")
        
        # Add sample data if available
        try:
            cursor.execute(f"SELECT TOP 3 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
            sample_rows = cursor.fetchall()
            
            if sample_rows and cursor.description:
                column_names = [column[0] for column in cursor.description]
                
                schema_info.append("\nSample Data Preview:")
                headers = column_names
                table_data = []
                
                for row in sample_rows:
                    # Convert row to list for tabulate
                    processed_row = []
                    for item in row:
                        if isinstance(item, (datetime, bytes, bytearray)):
                            processed_row.append(str(item))
                        else:
                            processed_row.append(item)
                    table_data.append(processed_row)
                
                table_str = tabulate.tabulate(table_data, headers=headers, tablefmt="grid")
                schema_info.append(table_str)
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

def is_select_query(sql):
    """Check if a query is a SELECT statement (read-only)"""
    # Remove comments and normalize whitespace
    sql = re.sub(r'--.*?(\n|$)|/\*.*?\*/', ' ', sql, flags=re.DOTALL)
    sql = ' '.join(sql.split()).strip().upper()
    
    # Check if it starts with SELECT
    return sql.startswith('SELECT')

@mcp.tool()
def query_table(sql: str) -> str:
    """Execute SQL queries on the specific table and return results in tabular format."""
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
        
        # For SELECT queries, format results as tabular data
        if cursor.description is not None:
            results = cursor.fetchall()
            
            if not results:
                if is_select_query(sql):
                    return "Query executed successfully, but no rows were returned."
                else:
                    return "SQL executed successfully, no results to display."
            
            # Get column names from cursor description
            headers = [column[0] for column in cursor.description]
            
            # Process row data (handle datetime, bytes, etc.)
            rows = []
            for row in results:
                processed_row = []
                for item in row:
                    if isinstance(item, (datetime, bytes, bytearray)):
                        processed_row.append(str(item))
                    else:
                        processed_row.append(item)
                rows.append(processed_row)
            
            # Create tabular output using tabulate
            table = tabulate.tabulate(rows, headers=headers, tablefmt="grid")
            
            # Also prepare JSON for possible programmatic use
            json_data = []
            for row in rows:
                json_row = {}
                for i, header in enumerate(headers):
                    json_row[header] = row[i]
                json_data.append(json_row)
            
            # Return both formats
            result = {
                "tabular": table,
                "json": json_data,
                "row_count": len(rows),
                "columns": headers
            }
            
            # Return combined output that's both human-readable and machine-parseable
            output = f"Query executed successfully. {len(rows)} rows returned.\n\n{table}\n\n"
            output += "JSON_DATA:" + json.dumps(json_data)
            
            return output
        else:
            # For non-SELECT queries
            row_count = cursor.rowcount
            if row_count >= 0:
                output = f"SQL executed successfully. {row_count} rows affected."
            else:
                output = "SQL executed successfully."
            
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
                info.append("Table exists: Yes")
            else:
                info.append("Table exists: No - Table not found in INFORMATION_SCHEMA.TABLES")
        except Exception as e:
            logger.error(f"Error verifying table existence: {e}")
            info.append("Table exists: Unable to verify")
        
        return "\n".join(info)
    except Exception as e:
        logger.error(f"Error getting basic table info: {str(e)}", exc_info=True)
        return f"Error retrieving basic table information: {str(e)}"
    finally:
        if 'conn' in locals():
            conn.close()

@mcp.tool()
def diagnose_table_access() -> str:
    """Run diagnostics to test connection and permissions on the table."""
    logger.info(f"Running diagnostics for table {FULLY_QUALIFIED_TABLE_NAME}...")
    results = []
    
    # Test database connection
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        results.append("✅ Database connection: Success")
        
        # Test table existence
        try:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            table_exists = cursor.fetchone()[0] > 0
            if table_exists:
                results.append(f"✅ Table exists: {FULLY_QUALIFIED_TABLE_NAME} found")
            else:
                results.append(f"❌ Table missing: {FULLY_QUALIFIED_TABLE_NAME} not found in INFORMATION_SCHEMA.TABLES")
                results.append("   ↳ Check if table name and schema are correct")
                results.append("   ↳ Verify user has permission to see the table metadata")
                return "\n".join(results)
        except Exception as e:
            results.append(f"❌ Table check failed: {str(e)}")
            return "\n".join(results)
        
        # Test SELECT permission
        try:
            cursor.execute(f"SELECT TOP 1 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
            cursor.fetchone()  # Just to test if it works
            results.append("✅ SELECT permission: Granted")
        except Exception as e:
            results.append(f"❌ SELECT permission: Denied - {str(e)}")
        
        # Test other permissions (with transaction to avoid actual changes)
        cursor.execute("BEGIN TRANSACTION")
        
        try:
            # Detect primary key for WHERE clause
            cursor.execute(f"""
                SELECT c.COLUMN_NAME, DATA_TYPE
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE c ON c.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
                JOIN INFORMATION_SCHEMA.COLUMNS col ON c.COLUMN_NAME = col.COLUMN_NAME 
                    AND col.TABLE_SCHEMA = tc.TABLE_SCHEMA AND col.TABLE_NAME = tc.TABLE_NAME
                WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY' 
                    AND tc.TABLE_SCHEMA = ? 
                    AND tc.TABLE_NAME = ?
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            pk_info = cursor.fetchone()
            
            # If no PK, get first column for tests
            if not pk_info:
                cursor.execute(f"""
                    SELECT TOP 1 COLUMN_NAME, DATA_TYPE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                    ORDER BY ORDINAL_POSITION
                """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
                pk_info = cursor.fetchone()
            
            if pk_info:
                column_name, data_type = pk_info
                
                # Use appropriate literal format based on data type
                safe_where = f"1=0"  # Never true condition to avoid affecting data
                
                # Test UPDATE permission (will rollback)
                try:
                    if data_type in ('varchar', 'nvarchar', 'char', 'nchar'):
                        test_sql = f"UPDATE {FULLY_QUALIFIED_TABLE_NAME} SET {column_name} = {column_name} WHERE {safe_where}"
                    else:
                        test_sql = f"UPDATE {FULLY_QUALIFIED_TABLE_NAME} SET {column_name} = {column_name} WHERE {safe_where}"
                    
                    cursor.execute(test_sql)
                    results.append("✅ UPDATE permission: Granted")
                except Exception as e:
                    results.append(f"❌ UPDATE permission: Denied - {str(e)}")
                
                # Test DELETE permission (will rollback)
                try:
                    cursor.execute(f"DELETE FROM {FULLY_QUALIFIED_TABLE_NAME} WHERE {safe_where}")
                    results.append("✅ DELETE permission: Granted")
                except Exception as e:
                    results.append(f"❌ DELETE permission: Denied - {str(e)}")
                
                # Test INSERT permission (will rollback)
                try:
                    columns = []
                    cursor.execute(f"""
                        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                        FROM INFORMATION_SCHEMA.COLUMNS 
                        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                        ORDER BY ORDINAL_POSITION
                    """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
                    
                    # Build a safe INSERT that will fail on constraints but test permissions
                    column_list = []
                    value_list = []
                    
                    for col_name, data_type, is_nullable in cursor.fetchall():
                        if is_nullable == 'YES':
                            column_list.append(col_name)
                            value_list.append('NULL')
                            
                    if column_list:
                        insert_sql = f"INSERT INTO {FULLY_QUALIFIED_TABLE_NAME} ({', '.join(column_list)}) VALUES ({', '.join(value_list)})"
                        try:
                            cursor.execute(insert_sql)
                            results.append("✅ INSERT permission: Granted")
                        except Exception as e:
                            # Check if it's a constraint error (which means permission was granted)
                            if "constraint" in str(e).lower() or "null" in str(e).lower():
                                results.append("✅ INSERT permission: Granted (failed due to constraints)")
                            else:
                                results.append(f"❌ INSERT permission: Denied - {str(e)}")
                    else:
                        results.append("⚠️ INSERT permission: Could not test (no nullable columns found)")
                except Exception as e:
                    results.append(f"❌ INSERT permission: Test error - {str(e)}")
            else:
                results.append("⚠️ Permissions tests: Limited (no suitable columns found)")
        except Exception as e:
            results.append(f"❌ Permissions tests error: {str(e)}")
        finally:
            # Always rollback the transaction
            cursor.execute("ROLLBACK TRANSACTION")
            results.append("ℹ️ All test changes were rolled back")
        
        return "\n".join(results)
    except Exception as e:
        results.append(f"❌ Database connection failed: {str(e)}")
        return "\n".join(results)
    finally:
        if 'conn' in locals():
            conn.close()

@mcp.tool()
def save_query_log(natural_language_query: str, sql_query: str, result_summary: str, iterations: list) -> str:
    """Save the query details, iterations, and results to a log file."""
    logger.info(f"Saving query log for: {natural_language_query[:50]}...")
    
    try:
        log_dir = "logs/queries"
        os.makedirs(log_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"query_{timestamp}.json")
        
        # Extract row count and first few rows from result summary
        result_info = {
            "success": not result_summary.startswith("Error"),
            "summary": result_summary.split("\n\nJSON_DATA:")[0] if "JSON_DATA:" in result_summary else result_summary
        }
        
        # Extract JSON data if available
        if "JSON_DATA:" in result_summary:
            json_str = result_summary.split("JSON_DATA:")[1]
            try:
                result_info["data"] = json.loads(json_str)
            except:
                result_info["data"] = "JSON parsing failed"
        
        # Create log entry
        log_entry = {
            "timestamp": timestamp,
            "natural_language_query": natural_language_query,
            "final_sql_query": sql_query,
            "result": result_info,
            "iterations": iterations
        }
        
        # Write to file
        with open(log_file, 'w') as f:
            json.dump(log_entry, f, indent=2, default=str)
        
        logger.info(f"Query log saved to {log_file}")
        return f"Query log saved successfully to {log_file}"
    except Exception as e:
        logger.error(f"Error saving query log: {str(e)}", exc_info=True)
        return f"Error saving query log: {str(e)}"

# Run our server
if __name__ == "__main__":
    logger.info("MCP SQL Server is starting up")
    mcp.run()
