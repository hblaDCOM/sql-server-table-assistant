import pyodbc

SQL_DRIVER = "{ODBC Driver 17 for SQL Server}"  # Adjust if necessary
SQL_SERVER = "DEZWTDW02"
SQL_DATABASE = "Insights"
SQL_USERNAME = "AnalyticsBot"
SQL_PASSWORD = "bloodedRb5Dg)kCfTn$U9J"

connection_string = f"DRIVER={SQL_DRIVER};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};UID={SQL_USERNAME};PWD={SQL_PASSWORD}"

try:
    conn = pyodbc.connect(connection_string)
    print("Successfully connected to SQL Server.")
    conn.close()
except Exception as e:
    print("Connection failed:", e)