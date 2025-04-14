import os
import json
import pyodbc
import tabulate
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from flask_cors import CORS
from openai import AzureOpenAI
from dotenv import load_dotenv
import re

# Load environment variables
load_dotenv()

# Create Flask app
app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.urandom(24).hex()
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Get SQL connection parameters from environment
SQL_SERVER = os.getenv("MSSQL_SERVER")
SQL_DATABASE = os.getenv("MSSQL_DATABASE")
SQL_USERNAME = os.getenv("MSSQL_USERNAME") 
SQL_PASSWORD = os.getenv("MSSQL_PASSWORD")
SQL_DRIVER = os.getenv("MSSQL_DRIVER", "{ODBC Driver 17 for SQL Server}")
TABLE_SCHEMA = os.getenv("MSSQL_TABLE_SCHEMA", "dbo")
TABLE_NAME = os.getenv("MSSQL_TABLE_NAME")
FULLY_QUALIFIED_TABLE_NAME = f"{TABLE_SCHEMA}.{TABLE_NAME}" if TABLE_SCHEMA else TABLE_NAME

# Set up OpenAI client
openai_client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),  
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)
OPENAI_DEPLOYMENT_ID = os.getenv("AZURE_OPENAI_DEPLOYMENT_ID")

# Build the connection string
connection_string = f"DRIVER={SQL_DRIVER};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};UID={SQL_USERNAME};PWD={SQL_PASSWORD}"

# Store table schema for LLM context
table_schema = ""

def get_connection():
    """Create and return a connection to the SQL database"""
    try:
        conn = pyodbc.connect(connection_string)
        return conn
    except Exception as e:
        print(f"Database connection error: {str(e)}")
        return None

def fetch_table_schema():
    """Get the schema information for the configured table"""
    global table_schema
    
    conn = get_connection()
    if not conn:
        return "Failed to connect to the database"
    
    try:
        cursor = conn.cursor()
        
        # Get column information
        cursor.execute(f"""
            SELECT 
                COLUMN_NAME, 
                DATA_TYPE, 
                CHARACTER_MAXIMUM_LENGTH, 
                IS_NULLABLE,
                COLUMN_DEFAULT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
        """, (TABLE_SCHEMA, TABLE_NAME))
        
        columns = cursor.fetchall()
        
        # Format schema information
        schema_info = [f"Table: {FULLY_QUALIFIED_TABLE_NAME}"]
        schema_info.append("\nColumns:")
        
        for col_name, data_type, max_length, is_nullable, default_val in columns:
            nullable_str = "NULL" if is_nullable == 'YES' else "NOT NULL"
            default_str = f" DEFAULT {default_val}" if default_val else ""
            
            if max_length and max_length != -1:
                schema_info.append(f"- {col_name}: {data_type}({max_length}) {nullable_str}{default_str}")
            elif data_type in ('varchar', 'nvarchar', 'char', 'nchar') and max_length == -1:
                schema_info.append(f"- {col_name}: {data_type}(MAX) {nullable_str}{default_str}")
            else:
                schema_info.append(f"- {col_name}: {data_type} {nullable_str}{default_str}")
        
        # Get row count
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {FULLY_QUALIFIED_TABLE_NAME}")
            row_count = cursor.fetchone()[0]
            schema_info.append(f"\nApproximate Row Count: {row_count}")
        except:
            schema_info.append("\nRow Count: Unable to retrieve")
        
        # Store the schema as a single string
        table_schema = "\n".join(schema_info)
        
        conn.close()
        return table_schema
        
    except Exception as e:
        if conn:
            conn.close()
        return f"Error retrieving schema: {str(e)}"

def get_table_preview():
    """Get first 5 rows from the table as preview"""
    conn = get_connection()
    if not conn:
        return None, "Failed to connect to the database"
    
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT TOP 5 * FROM {FULLY_QUALIFIED_TABLE_NAME}")
        
        # Get column names
        columns = [column[0] for column in cursor.description]
        
        # Fetch rows
        rows = cursor.fetchall()
        
        # Convert to list of lists for tabulate
        data = []
        for row in rows:
            data.append([str(cell) if cell is not None else 'NULL' for cell in row])
            
        # Format as table
        table = tabulate.tabulate(data, headers=columns, tablefmt="html")
        
        conn.close()
        return table, None
    except Exception as e:
        if conn:
            conn.close()
        return None, f"Error retrieving table data: {str(e)}"

def natural_language_to_sql(query):
    """Use Azure OpenAI to convert natural language to SQL"""
    system_prompt = f"""You are an AI assistant that helps users query a SQL Server database table.
The table is: {FULLY_QUALIFIED_TABLE_NAME}

SCHEMA INFORMATION:
{table_schema}

TASK: Convert the user's natural language question into a valid SQL query.
- Generate ONLY standard SQL Server T-SQL syntax
- Reference columns EXACTLY as they appear in the schema
- Include the fully qualified table name in your queries
- Return ONLY the SQL query without any explanation or markdown formatting
"""
    
    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_DEPLOYMENT_ID,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            temperature=0.1,
            max_tokens=500
        )
        
        sql_query = response.choices[0].message.content.strip()
        
        # Remove any markdown code block formatting if present
        sql_query = re.sub(r'```sql\s*|\s*```', '', sql_query)
        
        return sql_query, None
    except Exception as e:
        return None, f"Error generating SQL: {str(e)}"

def execute_sql_query(sql):
    """Execute the SQL query and return formatted results"""
    conn = get_connection()
    if not conn:
        return None, "Failed to connect to the database"
    
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        
        # For SELECT queries that return data
        if cursor.description:
            # Get column names
            columns = [column[0] for column in cursor.description]
            
            # Fetch all rows
            rows = cursor.fetchall()
            
            # Format for display
            data = []
            for row in rows:
                data.append([str(cell) if cell is not None else 'NULL' for cell in row])
            
            # Format as HTML table
            table = tabulate.tabulate(data, headers=columns, tablefmt="html")
            
            result = {
                "status": "success",
                "message": f"Query executed successfully. {len(rows)} rows returned.",
                "data": table,
                "row_count": len(rows)
            }
        else:
            # For non-SELECT queries (INSERT, UPDATE, DELETE)
            row_count = cursor.rowcount
            conn.commit()
            
            result = {
                "status": "success",
                "message": f"Query executed successfully. {row_count} rows affected.",
                "data": None,
                "row_count": row_count
            }
        
        conn.close()
        return result, None
    except Exception as e:
        if conn:
            conn.close()
        return None, f"Error executing query: {str(e)}"

def explain_results(query, sql, results):
    """Generate a natural language explanation of query results"""
    system_prompt = """You are an AI assistant that explains SQL query results in plain language.
Keep your explanations concise and focused on the key insights from the data.
"""
    
    try:
        # If there are no results, keep it simple
        if results["row_count"] == 0:
            return "The query returned no results."
        
        # Build prompt with query, SQL, and results
        user_prompt = f"""
Natural language query: {query}
SQL query used: {sql}
Result: {results["message"]}

Please provide a brief explanation of these results.
"""
        
        response = openai_client.chat.completions.create(
            model=OPENAI_DEPLOYMENT_ID,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=300
        )
        
        explanation = response.choices[0].message.content.strip()
        return explanation
    except Exception as e:
        return f"Could not generate explanation: {str(e)}"

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print("Client connected")
    # Send table schema and preview on connection
    schema = fetch_table_schema()
    preview, error = get_table_preview()
    
    if error:
        socketio.emit('initialization_error', {'error': error})
    else:
        socketio.emit('initial_data', {
            'table_name': FULLY_QUALIFIED_TABLE_NAME,
            'preview': preview
        })

@socketio.on('disconnect')
def handle_disconnect():
    print("Client disconnected")

@socketio.on('query')
def handle_query(data):
    print(f"Received query: {data['query']}")
    
    # Convert natural language to SQL
    sql, error = natural_language_to_sql(data['query'])
    if error:
        socketio.emit('error', {'error': error})
        return
    
    socketio.emit('sql_generated', {'sql': sql})

@socketio.on('execute')
def handle_execute(data):
    print(f"Executing SQL: {data['sql']}")
    
    # Execute the SQL query
    results, error = execute_sql_query(data['sql'])
    if error:
        socketio.emit('error', {'error': error})
        return
    
    # Generate explanation
    explanation = explain_results(data['original_query'], data['sql'], results)
    
    # Send results and explanation back to client
    socketio.emit('query_results', {
        'results': results,
        'explanation': explanation
    })

@socketio.on('refine_sql')
def handle_refine_sql(data):
    print(f"Refining SQL with feedback: {data['feedback']}")
    
    system_prompt = f"""You are an AI assistant that helps refine SQL queries based on user feedback.
The table is: {FULLY_QUALIFIED_TABLE_NAME}

SCHEMA INFORMATION:
{table_schema}

CURRENT SQL QUERY: {data['current_sql']}

USER FEEDBACK: {data['feedback']}

TASK: Refine the SQL query based on the user's feedback.
- Generate ONLY standard SQL Server T-SQL syntax
- Reference columns EXACTLY as they appear in the schema
- Include the fully qualified table name in your queries
- Return ONLY the SQL query without any explanation or markdown formatting
"""
    
    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_DEPLOYMENT_ID,
            messages=[
                {"role": "system", "content": system_prompt}
            ],
            temperature=0.1,
            max_tokens=500
        )
        
        sql_query = response.choices[0].message.content.strip()
        
        # Remove any markdown code block formatting if present
        sql_query = re.sub(r'```sql\s*|\s*```', '', sql_query)
        
        socketio.emit('sql_generated', {'sql': sql_query})
    except Exception as e:
        socketio.emit('error', {'error': f"Error refining SQL: {str(e)}"})

# Main entry point
if __name__ == '__main__':
    print(f"Starting SQL Table Assistant for table: {FULLY_QUALIFIED_TABLE_NAME}")
    
    # Initialize schema on startup
    fetch_table_schema()
    
    # Verify SQL connection
    preview, error = get_table_preview()
    if error:
        print(f"Warning: {error}")
        print("Starting server anyway, but SQL connectivity issues may persist.")
    else:
        print("Successfully connected to SQL Server and retrieved data preview.")
        
    socketio.run(app, host='0.0.0.0', port=5000, debug=True) 