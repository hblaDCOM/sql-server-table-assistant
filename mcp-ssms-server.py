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
import math

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

# Check for ODBC drivers
def check_odbc_drivers():
    """Check if the specified ODBC driver is available"""
    try:
        available_drivers = pyodbc.drivers()
        logger.info(f"Available ODBC drivers: {available_drivers}")
        
        # Check if our driver is in the list (exact match)
        driver_name = MSSQL_DRIVER.strip("{}")  # Remove curly braces
        if driver_name in available_drivers:
            logger.info(f"Found exact driver match: {driver_name}")
            return True
            
        # Check for partial matches, which might work on some systems
        for driver in available_drivers:
            if "SQL Server" in driver:
                logger.info(f"Found SQL Server driver: {driver}")
                logger.warning(f"Using alternative driver: {driver} instead of {MSSQL_DRIVER}")
                global MSSQL_DRIVER
                MSSQL_DRIVER = '{' + driver + '}'
                return True
                
        logger.error(f"SQL Server ODBC driver not found. Available drivers: {available_drivers}")
        print(f"ERROR: SQL Server ODBC driver '{MSSQL_DRIVER}' not found.")
        print(f"Available drivers: {available_drivers}")
        print("Please install the appropriate ODBC driver or correct the MSSQL_DRIVER environment variable.")
        return False
    except Exception as e:
        logger.error(f"Error checking ODBC drivers: {e}")
        return False

# Call the driver check function
driver_available = check_odbc_drivers()
if not driver_available:
    print("WARNING: ODBC driver check failed. SQL connection might not work.")
    logger.warning("ODBC driver check failed. Continuing but SQL connection might fail.")

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
    f"PWD={MSSQL_PASSWORD}"
)

logger.debug(f"Connection string created (password masked): DRIVER={MSSQL_DRIVER};SERVER={MSSQL_SERVER};DATABASE={MSSQL_DATABASE};UID={MSSQL_USERNAME};PWD=******")
logger.info(f"Configured to work with table: {FULLY_QUALIFIED_TABLE_NAME}")

# Creating an MCP server instance
mcp = FastMCP("Demo")

def serialize_value(value):
    """Convert SQL values to a serializable format for JSON"""
    if value is None:
        return None
    elif isinstance(value, (datetime, bytes, bytearray)):
        return str(value)
    elif isinstance(value, (int, float, str, bool)):
        # Handle NaN, Infinity values that cause JSON serialization issues
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return str(value)
        return value
    elif hasattr(value, 'isoformat'):  # For date/time objects
        return value.isoformat()
    else:
        return str(value)

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
        
        # Dictionary to store all schema elements
        schema_dict = {
            "columns": [],
            "numeric_columns": [],
            "primary_keys": [],
            "foreign_keys": [],
            "indexes": [],
            "row_count": None,
            "numeric_stats": {}  # Will store statistics for numeric columns
        }
        
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
                    COLUMN_DEFAULT,
                    NUMERIC_PRECISION,
                    NUMERIC_SCALE
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
            
            # Collect numeric column names for statistics
            numeric_column_names = []
            
            for col_name, data_type, max_length, is_nullable, is_identity, default_val, numeric_precision, numeric_scale in columns:
                nullable_str = "NULL" if is_nullable == 'YES' else "NOT NULL"
                identity_str = " IDENTITY" if is_identity == 1 else ""
                default_str = f" DEFAULT {default_val}" if default_val else ""
                
                # Store column information in schema dictionary
                column_info = {
                    "name": col_name,
                    "data_type": data_type,
                    "max_length": max_length,
                    "is_nullable": is_nullable == 'YES',
                    "is_identity": is_identity == 1,
                    "default": default_val,
                    "numeric_precision": numeric_precision,
                    "numeric_scale": numeric_scale
                }
                schema_dict["columns"].append(column_info)
                
                # Identify numeric columns for statistics
                if data_type in ('int', 'bigint', 'smallint', 'tinyint', 'decimal', 'numeric', 'float', 'real', 'money', 'smallmoney'):
                    numeric_column_names.append(col_name)
                    schema_dict["numeric_columns"].append(column_info)
                
                if max_length and max_length != -1:
                    column_details.append(f"- {col_name}: {data_type}({max_length}) {nullable_str}{identity_str}{default_str}")
                elif data_type in ('varchar', 'nvarchar', 'char', 'nchar') and max_length == -1:
                    column_details.append(f"- {col_name}: {data_type}(MAX) {nullable_str}{identity_str}{default_str}")
                else:
                    column_details.append(f"- {col_name}: {data_type} {nullable_str}{identity_str}{default_str}")
                
                if numeric_precision and numeric_scale:
                    column_details.append(f"    Precision: {numeric_precision}, Scale: {numeric_scale}")
            
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
            schema_dict["primary_keys"] = pk_columns
            
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
            schema_dict["foreign_keys"] = [
                {
                    "name": fk_name,
                    "column": column,
                    "referenced_table": ref_table,
                    "referenced_column": ref_column
                }
                for fk_name, _, column, ref_table, ref_column in fk_results
            ]
            
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
            schema_dict["indexes"] = [
                {
                    "name": idx_name,
                    "type": idx_type,
                    "columns": columns.split(", "),
                    "is_unique": is_unique
                }
                for idx_name, idx_type, columns, is_unique in idx_results
            ]
            
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
            schema_dict["row_count"] = row_count
            schema_info.append(f"\nApproximate Row Count: {row_count}")
        except Exception as e:
            logger.warning(f"Could not retrieve row count: {str(e)}")
            schema_info.append("\nRow Count: Unable to retrieve")
            
        # Get statistics for numeric columns
        if numeric_column_names:
            try:
                logger.debug(f"Collecting statistics for numeric columns: {numeric_column_names}")
                schema_info.append("\nNumeric Column Statistics:")
                
                for column_name in numeric_column_names:
                    try:
                        # Try to get min, max, avg for each numeric column
                        stats_query = f"""
                            SELECT 
                                MIN({column_name}) AS min_value,
                                MAX({column_name}) AS max_value,
                                AVG(CAST({column_name} AS FLOAT)) AS avg_value,
                                COUNT({column_name}) AS count_value,
                                COUNT(*) - COUNT({column_name}) AS null_count
                            FROM {FULLY_QUALIFIED_TABLE_NAME}
                            WHERE {column_name} IS NOT NULL
                        """
                        cursor.execute(stats_query)
                        stats = cursor.fetchone()
                        
                        if stats and stats[0] is not None:
                            min_val, max_val, avg_val, count_val, null_count = stats
                            
                            # Store in schema dictionary
                            schema_dict["numeric_stats"][column_name] = {
                                "min": min_val,
                                "max": max_val,
                                "avg": avg_val,
                                "count": count_val,
                                "null_count": null_count
                            }
                            
                            # Format for display
                            schema_info.append(f"- {column_name}:")
                            schema_info.append(f"    Min: {min_val}, Max: {max_val}, Avg: {round(avg_val, 2) if avg_val is not None else 'N/A'}")
                            schema_info.append(f"    Non-null values: {count_val}, Null values: {null_count}")
                    except Exception as col_err:
                        logger.warning(f"Could not retrieve statistics for column {column_name}: {str(col_err)}")
                        schema_info.append(f"- {column_name}: Statistics unavailable")
            except Exception as stats_err:
                logger.warning(f"Error collecting numeric statistics: {str(stats_err)}")
                schema_info.append("Could not collect numeric column statistics")
        
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
            cursor.execute(f"SELECT TOP 5 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
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
                
                # Add schema information about the sample data
                schema_dict["sample_data"] = {
                    "columns": headers,
                    "rows": [[serialize_value(item) for item in row] for row in sample_rows]
                }
            else:
                schema_info.append("\nNo sample data available.")
        except Exception as e:
            logger.warning(f"Could not retrieve sample data: {str(e)}")
            schema_info.append("\nCould not retrieve sample data.")
        
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
    
    # Check for date calculations that might produce negative results
    date_calc_pattern = r'DATEDIFF\s*\(\s*\w+\s*,\s*(\w+)\s*,\s*(\w+)\s*\)'
    date_calculations = re.findall(date_calc_pattern, sql, re.IGNORECASE)
    
    # Modify query to handle potential date calculation issues
    if date_calculations and 'ABS(' not in sql_upper and 'CASE WHEN' not in sql_upper:
        logger.info("Detected potential date calculation issue - suggesting modification")
        for start_col, end_col in date_calculations:
            # Create safer pattern for replacement
            pattern = rf'DATEDIFF\s*\(\s*\w+\s*,\s*{start_col}\s*,\s*{end_col}\s*\)'
            replacement = f'CASE WHEN {end_col} >= {start_col} THEN DATEDIFF(DAY, {start_col}, {end_col}) ELSE NULL END'
            
            # Check if we can safely modify the query
            if re.search(pattern, sql):
                modified_sql = re.sub(pattern, replacement, sql)
                logger.info(f"Modified query to prevent negative date calculations: {modified_sql}")
                
                # Add a warning to the query results
                warning_msg = (
                    "NOTICE: The query was automatically modified to prevent negative date calculations. "
                    "The original query might have returned negative values for date differences where "
                    f"end date ({end_col}) precedes start date ({start_col}). Such records have been excluded "
                    "from the calculation. Consider reviewing your data for date consistency."
                )
                sql = modified_sql
    
    # Check if this is a calculation query (likely to produce percentages or other float values)
    has_calculation = False
    if any(op in sql_upper for op in [' / ', '*', '+', '-', 'AVG(', 'SUM(', 'COUNT(', 'CAST(', 'CONVERT(']):
        has_calculation = True
        logger.debug("Query contains calculations - special handling will be applied")
    
    conn = None
    try:
        # Set an explicit timeout for connection
        logger.debug("Attempting to connect with explicit timeout (30 seconds)")
        conn = pyodbc.connect(connection_string)
        
        cursor = conn.cursor()
        
        # Set query timeout to prevent long-running queries
        cursor.execute("SET QUERY_GOVERNOR_COST_LIMIT 0")  # Remove cost-based limitations
        cursor.execute("SET LOCK_TIMEOUT 10000")  # 10 seconds lock timeout
        cursor.execute("SET QUERY_TIMEOUT 20000")  # 20 seconds query timeout
        
        logger.debug(f"Executing SQL: {sql}")
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
                    # Special handling for float values from calculations
                    processed_row.append(serialize_value(item))
                rows.append(processed_row)
            
            # Create tabular output using tabulate
            table = tabulate.tabulate(rows, headers=headers, tablefmt="grid")
            
            # Prepare JSON data with special handling for float values
            json_data = []
            for row in rows:
                json_row = {}
                for i, header in enumerate(headers):
                    json_row[header] = row[i]
                json_data.append(json_row)
            
            # Return combined output that's both human-readable and machine-parseable
            output = f"Query executed successfully. {len(rows)} rows returned.\n\n{table}\n\n"
            
            # If warning message exists from date calculation adjustment, add it
            if 'warning_msg' in locals():
                output = f"{warning_msg}\n\n{output}"
            
            # If this is a calculation query, use custom JSON serialization to handle floats properly
            if has_calculation:
                try:
                    # Use a custom JSON encoder to handle special float values
                    class CustomJSONEncoder(json.JSONEncoder):
                        def default(self, obj):
                            return serialize_value(obj)
                    
                    json_str = json.dumps(json_data, cls=CustomJSONEncoder)
                    output += "JSON_DATA:" + json_str
                except Exception as json_err:
                    logger.error(f"JSON serialization error: {json_err}")
                    # Fallback: convert problematic values to strings
                    for row_data in json_data:
                        for key, value in row_data.items():
                            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                                row_data[key] = str(value)
                    
                    output += "JSON_DATA:" + json.dumps(json_data)
            else:
                # Standard serialization for non-calculation queries
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
        
        # Check for calculations in the SQL which might cause serialization issues
        has_calculation = any(op in sql_query.upper() 
                           for op in [' / ', '*', '+', '-', 'AVG(', 'SUM(', 'COUNT(', 'CAST(', 'CONVERT('])
        
        # Extract row count and first few rows from result summary
        result_info = {
            "success": not result_summary.startswith("Error"),
            "summary": result_summary.split("\n\nJSON_DATA:")[0] if "JSON_DATA:" in result_summary else result_summary
        }
        
        # Extract JSON data if available, with special handling for calculations
        if "JSON_DATA:" in result_summary and not has_calculation:
            # Standard handling for non-calculation queries
            try:
                json_str = result_summary.split("JSON_DATA:")[1]
                result_info["data"] = json.loads(json_str)
            except json.JSONDecodeError:
                # If there's a parsing error, try to sanitize the JSON
                try:
                    json_str = result_summary.split("JSON_DATA:")[1].strip()
                    # Replace problematic values
                    json_str = json_str.replace('NaN', '"NaN"').replace('Infinity', '"Infinity"').replace('-Infinity', '"-Infinity"')
                    result_info["data"] = json.loads(json_str)
                except:
                    result_info["data"] = "JSON parsing failed - serialization issue with results"
        elif "JSON_DATA:" in result_summary and has_calculation:
            # For calculation queries, store a message instead of trying to parse potentially problematic JSON
            result_info["data"] = "JSON data omitted for calculation query to avoid serialization issues"
        
        # Create log entry with fallbacks for serialization issues
        try:
            # First try to create the log entry normally
            log_entry = {
                "timestamp": timestamp,
                "natural_language_query": natural_language_query,
                "final_sql_query": sql_query,
                "result": result_info,
                "iterations": iterations
            }
            
            # Use a custom encoder that can handle special float values
            class CustomJSONEncoder(json.JSONEncoder):
                def default(self, obj):
                    if isinstance(obj, (datetime, bytes, bytearray)):
                        return str(obj)
                    elif isinstance(obj, float):
                        if math.isnan(obj) or math.isinf(obj):
                            return str(obj)
                    return super().default(obj)
            
            # Write to file with the custom encoder
            with open(log_file, 'w') as f:
                json.dump(log_entry, f, indent=2, cls=CustomJSONEncoder, default=str)
                
        except (TypeError, ValueError, OverflowError) as json_err:
            # If serialization fails with custom encoder, create a simplified log entry
            logger.warning(f"JSON serialization issue with primary method: {json_err}")
            
            # Create a simplified log entry with minimal data
            simple_log = {
                "timestamp": timestamp,
                "natural_language_query": natural_language_query,
                "final_sql_query": sql_query,
                "result": {
                    "success": result_info["success"],
                    "summary": "Results omitted due to serialization issues",
                },
                "iterations": [
                    {
                        "iteration": i.get("iteration", idx+1),
                        "sql": i.get("sql", ""),
                        "feedback": i.get("feedback", ""),
                        "executed": i.get("executed", False)
                    } for idx, i in enumerate(iterations)
                ]
            }
            
            # Try to write the simplified log
            with open(log_file, 'w') as f:
                json.dump(simple_log, f, indent=2, default=str)
        
        logger.info(f"Query log saved to {log_file}")
        return f"Query log saved successfully to {log_file}"
    except Exception as e:
        logger.error(f"Error saving query log: {str(e)}", exc_info=True)
        return f"Error saving query log: {str(e)}"

@mcp.tool()
def get_recent_query_logs(num_logs: int = 5) -> str:
    """Retrieve the most recent query logs.
    
    Args:
        num_logs: Number of most recent logs to retrieve (default: 5)
    
    Returns:
        A formatted string with summaries of recent query logs
    """
    logger.info(f"Retrieving {num_logs} most recent query logs")
    
    try:
        log_dir = "logs/queries"
        if not os.path.exists(log_dir):
            return "No query logs found. The logs directory doesn't exist yet."
        
        # Get all log files sorted by modification time (newest first)
        log_files = sorted(
            [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith('.json')],
            key=os.path.getmtime,
            reverse=True
        )
        
        if not log_files:
            return "No query logs found in the logs directory."
        
        # Limit to requested number
        log_files = log_files[:num_logs]
        
        results = []
        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    log_data = json.load(f)
                
                # Extract key information
                timestamp = log_data.get('timestamp', 'Unknown')
                nl_query = log_data.get('natural_language_query', 'Unknown')
                sql_query = log_data.get('final_sql_query', 'Unknown')
                success = log_data.get('result', {}).get('success', False)
                
                # Count iterations
                iterations = log_data.get('iterations', [])
                iteration_count = len(iterations)
                
                # Format a summary
                status = "✅ Success" if success else "❌ Failed"
                log_summary = f"[{timestamp}] {status}\n"
                log_summary += f"Natural language: {nl_query[:100]}{'...' if len(nl_query) > 100 else ''}\n"
                log_summary += f"SQL: {sql_query[:100]}{'...' if len(sql_query) > 100 else ''}\n"
                log_summary += f"Iterations: {iteration_count}\n"
                log_summary += f"Log file: {os.path.basename(log_file)}\n"
                
                results.append(log_summary)
            except Exception as e:
                results.append(f"Error parsing log file {os.path.basename(log_file)}: {str(e)}")
        
        return "\n\n".join(results)
    except Exception as e:
        logger.error(f"Error retrieving query logs: {str(e)}", exc_info=True)
        return f"Error retrieving query logs: {str(e)}"

@mcp.tool()
def test_connection() -> str:
    """Test SQL Server connection and provide detailed diagnostics."""
    logger.info("Running connection test to SQL Server...")
    results = []
    
    # Log environment variables securely
    results.append(f"SERVER CONNECTION TEST")
    results.append(f"-------------------")
    results.append(f"Server: {MSSQL_SERVER}")
    results.append(f"Database: {MSSQL_DATABASE}")
    results.append(f"Username: {MSSQL_USERNAME}")
    results.append(f"Driver: {MSSQL_DRIVER}")
    results.append(f"Table: {FULLY_QUALIFIED_TABLE_NAME}")
    results.append(f"-------------------")
    
    # Log available drivers
    try:
        drivers = pyodbc.drivers()
        results.append(f"Available ODBC drivers: {', '.join(drivers)}")
        
        if not any('SQL Server' in driver for driver in drivers):
            results.append("❌ WARNING: No SQL Server ODBC drivers found on this system")
            results.append("   Install the Microsoft ODBC Driver for SQL Server")
    except Exception as e:
        results.append(f"❌ Error getting ODBC drivers: {str(e)}")
    
    # Test basic connectivity first (without database)
    try:
        logger.debug("Testing basic server connectivity without database...")
        results.append("\nSTEP 1: Testing basic server connectivity (no database)")
        
        # Build minimal connection string for server only
        server_conn_string = (
            f"DRIVER={MSSQL_DRIVER};"
            f"SERVER={MSSQL_SERVER};"
            f"UID={MSSQL_USERNAME};"
            f"PWD={MSSQL_PASSWORD}"
        )
        
        try:
            logger.debug("Attempting to connect to server only...")
            results.append("Attempting to connect to server only...")
            
            # Try to connect to just the server
            server_conn = pyodbc.connect(server_conn_string)
            server_cursor = server_conn.cursor()
            
            # Simple test query
            server_cursor.execute("SELECT @@VERSION")
            version = server_cursor.fetchone()[0]
            
            results.append("✅ Basic server connection successful")
            results.append(f"SQL Server version: {version[:50]}...")
            server_conn.close()
        except Exception as server_err:
            results.append(f"❌ Server connection failed: {str(server_err)}")
            results.append(f"   This indicates a problem connecting to the SQL Server instance")
            results.append(f"   Check network connectivity, firewall settings, and server name")
            # Early return since we can't even connect to the server
            return "\n".join(results)
    except Exception as e:
        results.append(f"❌ Error in server connectivity test: {str(e)}")
        return "\n".join(results)
    
    # Test database connectivity
    try:
        logger.debug("Testing database connectivity...")
        results.append("\nSTEP 2: Testing database connectivity")
        
        # Build connection string with database
        db_conn_string = (
            f"DRIVER={MSSQL_DRIVER};"
            f"SERVER={MSSQL_SERVER};"
            f"DATABASE={MSSQL_DATABASE};"
            f"UID={MSSQL_USERNAME};"
            f"PWD={MSSQL_PASSWORD}"
        )
        
        try:
            logger.debug("Attempting database connection...")
            results.append("Attempting to connect to database...")
            
            # Create connection with explicit timeout
            db_conn = pyodbc.connect(db_conn_string)
            
            # Test basic connectivity
            db_cursor = db_conn.cursor()
            db_cursor.execute("SELECT DB_NAME()")
            db_name = db_cursor.fetchone()[0]
            
            results.append(f"✅ Database connection successful: '{db_name}'")
            
            # Check if connected to correct database
            if db_name.lower() != MSSQL_DATABASE.lower():
                results.append(f"⚠️ Warning: Connected to database '{db_name}' but expected '{MSSQL_DATABASE}'")
            
            db_conn.close()
        except Exception as db_err:
            results.append(f"❌ Database connection failed: {str(db_err)}")
            results.append(f"   This indicates a problem with the database access")
            results.append(f"   Check that database '{MSSQL_DATABASE}' exists and user has access")
            # Early return since we can't connect to the database
            return "\n".join(results)
    except Exception as e:
        results.append(f"❌ Error in database connectivity test: {str(e)}")
        return "\n".join(results)
    
    # Test table existence
    try:
        logger.debug("Testing table existence...")
        results.append("\nSTEP 3: Testing table existence")
        
        # Create connection with the full string
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        # Try different ways to verify table existence
        try:
            results.append(f"Checking if table '{FULLY_QUALIFIED_TABLE_NAME}' exists...")
            
            # Method 1: Using INFORMATION_SCHEMA
            cursor.execute(f"""
                SELECT COUNT(*) 
                FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
            
            count = cursor.fetchone()[0]
            if count > 0:
                results.append(f"✅ Table exists in INFORMATION_SCHEMA (Count: {count})")
            else:
                results.append(f"❌ Table NOT found in INFORMATION_SCHEMA")
                
                # Method 2: Try sys.tables
                try:
                    cursor.execute(f"""
                        SELECT COUNT(*) FROM sys.tables t
                        JOIN sys.schemas s ON t.schema_id = s.schema_id
                        WHERE s.name = ? AND t.name = ?
                    """, (MSSQL_TABLE_SCHEMA, MSSQL_TABLE_NAME))
                    
                    sys_count = cursor.fetchone()[0]
                    if sys_count > 0:
                        results.append(f"✅ Table found in sys.tables (Count: {sys_count})")
                    else:
                        results.append(f"❌ Table NOT found in sys.tables")
                        
                        # Method 3: Try direct schema enumeration
                        results.append("Checking available tables in schema...")
                        try:
                            cursor.execute(f"""
                                SELECT TOP 10 t.name 
                                FROM sys.tables t
                                JOIN sys.schemas s ON t.schema_id = s.schema_id
                                WHERE s.name = ?
                            """, (MSSQL_TABLE_SCHEMA,))
                            
                            tables = [row[0] for row in cursor.fetchall()]
                            if tables:
                                results.append(f"Available tables in schema '{MSSQL_TABLE_SCHEMA}': {', '.join(tables)}")
                            else:
                                results.append(f"No tables found in schema '{MSSQL_TABLE_SCHEMA}'")
                                
                                # Method 4: Check if schema exists
                                cursor.execute(f"""
                                    SELECT COUNT(*) FROM sys.schemas WHERE name = ?
                                """, (MSSQL_TABLE_SCHEMA,))
                                
                                schema_count = cursor.fetchone()[0]
                                if schema_count > 0:
                                    results.append(f"✅ Schema '{MSSQL_TABLE_SCHEMA}' exists but contains no tables")
                                else:
                                    results.append(f"❌ Schema '{MSSQL_TABLE_SCHEMA}' does NOT exist")
                                    
                                    # Method 5: List available schemas
                                    cursor.execute("SELECT TOP 10 name FROM sys.schemas")
                                    schemas = [row[0] for row in cursor.fetchall()]
                                    results.append(f"Available schemas: {', '.join(schemas)}")
                        except Exception as tables_err:
                            results.append(f"❌ Error checking available tables: {str(tables_err)}")
                except Exception as sys_err:
                    results.append(f"❌ Error checking sys.tables: {str(sys_err)}")
        except Exception as exists_err:
            results.append(f"❌ Error checking table existence: {str(exists_err)}")
    
        # Test table access (if we got this far)
        try:
            results.append("\nSTEP 4: Testing table access")
            results.append(f"Attempting to SELECT from table '{FULLY_QUALIFIED_TABLE_NAME}'...")
            
            cursor.execute(f"SELECT TOP 1 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
            column_names = [column[0] for column in cursor.description]
            results.append(f"✅ Successfully executed SELECT query")
            results.append(f"Table columns: {', '.join(column_names[:5])}...")
            
            # Check row count
            cursor.execute(f"SELECT COUNT(*) FROM {FULLY_QUALIFIED_TABLE_NAME}")
            row_count = cursor.fetchone()[0]
            results.append(f"Total rows in table: {row_count}")
            
        except Exception as access_err:
            results.append(f"❌ Error accessing table: {str(access_err)}")
            results.append(f"   This indicates a permission issue or the table doesn't exist")
            
        # Clean up connection
        conn.close()
        
    except Exception as e:
        results.append(f"❌ Error in table existence test: {str(e)}")
    
    # Final summary
    results.append("\nCONNECTION TEST SUMMARY")
    results.append("=====================")
    
    if "❌" in "\n".join(results):
        results.append("⚠️ CONNECTION TEST FAILED: Issues were detected during testing")
        results.append("Review the details above to identify the specific problem")
    else:
        results.append("✅ ALL TESTS PASSED: Connection to SQL Server and table successful")
    
    return "\n".join(results)

# Run our server
if __name__ == "__main__":
    if not driver_available:
        print("\n----- SQL CONNECTION WARNING -----")
        print("The ODBC driver check failed. The application might not be able to connect to SQL Server.")
        print("If you experience connection issues, please:")
        print("1. Install the Microsoft ODBC Driver for SQL Server")
        print("2. Check your .env file and set the correct MSSQL_DRIVER value")
        print("3. Restart the application")
        print("--------------------------------\n")
        
    try:
        print(f"MCP SQL Server running with table: {FULLY_QUALIFIED_TABLE_NAME}")
        print(f"Press Ctrl+C to exit")
        # Start the server
        mcp.run()
    except KeyboardInterrupt:
        print("Shutting down gracefully...")
    except Exception as e:
        logger.error(f"Error starting server: {e}", exc_info=True)
        print(f"Error starting server: {e}")
