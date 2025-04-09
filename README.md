# SQL Server Table Assistant - Modal Context Protocol

This application lets you interact with a specific SQL Server table using natural language, leveraging the Modal Context Protocol as a communication layer between LLMs and your data source.

*This project is based on the [mcp-sql-server-natural-lang](https://github.com/Amanp17/mcp-sql-server-natural-lang) repository by [Aman Pachori](https://github.com/Amanp17), with modifications to focus on single table access.*

## Key Features:

* **Talk to Your Table**: Chat with a specific SQL Server table using plain English
* **SQL Query Iteration**: Provide feedback to refine SQL queries until they meet your needs
* **Beautiful Tabular Results**: View query results in well-formatted tables for better readability
* **Query History Logging**: Automatically save queries, iterations, and results for future reference
* **No-Code Table Operations**: Query, insert, update, and delete data through natural conversations
* **Secure, Limited Access**: Connect to only one table with restricted credentials for enhanced security
* **MCP-Enhanced Accuracy**: Achieve precise table interactions through Modal Context Protocol
* **Context-Aware Conversations**: Maintain context across multiple queries
* **Natural Language Explanations**: Get plain English explanations of query results

## What is MCP?
MCP (Modal Context Protocol) is a methodology that standardizes how context is bound to LLMs, providing a standard way to connect AI models to different data sources and tools.

## Single Table Mode

This application runs in "Single Table Mode" which provides several advantages:

1. **Enhanced Security**: Access is limited to a single table rather than the entire database
2. **Simpler Permissions**: Users need minimal permissions (just for the specific table)
3. **Focused Experience**: The assistant is specialized for working with just one table
4. **Reduced Risk**: Prevents accidental access to sensitive data in other tables

## Prerequisites
Before you get started, make sure you have the following:

- **Python 3.12+** installed on your machine  
- A valid **Azure OpenAI deployment** with API access
- **SQL Server** with a table that you want to interact with
- **Limited user credentials** with access to only that table

## Getting Started
Follow these steps to get the project up and running:
### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/sql-server-table-assistant.git
cd sql-server-table-assistant
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```
### 3. Setup Environment Variables

Create a `.env` file in the root of the project and add the following:

```dotenv
# Azure OpenAI Configuration (required)
AZURE_OPENAI_API_KEY=your_azure_openai_api_key
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com
AZURE_OPENAI_API_VERSION=2023-05-15
AZURE_OPENAI_DEPLOYMENT_ID=your-deployment-name

# SQL Server Configuration
MSSQL_SERVER=localhost
MSSQL_DATABASE=your_database_name
MSSQL_USERNAME=your_username
MSSQL_PASSWORD=your_password
MSSQL_DRIVER={ODBC Driver 18 for SQL Server}

# Table Configuration
MSSQL_TABLE_SCHEMA=dbo
MSSQL_TABLE_NAME=your_table_name
```

## Running the Table Assistant
Once you've set up your environment and dependencies, you're ready to interact with the Table Assistant.

### Run the Client Script
Execute the following command to start the assistant:

```bash
python mcp-ssms-client.py
```

Once the script starts, it will prompt you with the table name and available commands. You can then type your requests in plain English. For example:

```
Table Assistant is ready. You are working with table: dbo.Employees
Type your questions about the table in natural language, and I'll translate them to SQL.
Special commands: 
  /diagnose - Run diagnostics
  /refresh_schema - Refresh table schema
  /history - View query history

Enter your Query: Show me all employees with a salary over $50,000
```

## Interactive Query Workflow

The assistant follows an interactive workflow:

1. You ask a question in natural language
2. The assistant generates a SQL query
3. You can:
   - Execute the query (e)
   - Provide feedback to refine it (f)
   - Cancel (c)
4. If you provide feedback, the assistant generates an improved query
5. Once executed, results are displayed in a formatted table
6. The assistant provides a plain English explanation of the results
7. A complete log of the query, iterations, and results is automatically saved

### Example Conversation

```
Enter your Query: Show me the highest paid employee in each department

===== GENERATED SQL QUERY =====
SELECT 
    Department,
    EmployeeName,
    Salary
FROM (
    SELECT 
        Department,
        EmployeeName,
        Salary,
        ROW_NUMBER() OVER (PARTITION BY Department ORDER BY Salary DESC) as RankBySalary
    FROM dbo.Employees
) RankedEmployees
WHERE RankBySalary = 1
ORDER BY Department
===============================

Do you want to (e)xecute this query, provide (f)eedback to refine it, or (c)ancel? (e/f/c): f
Enter your feedback for improving the SQL query: Include the employee's hire date as well

SQL query generated (iteration 2).

===== GENERATED SQL QUERY =====
SELECT 
    Department,
    EmployeeName,
    Salary,
    HireDate
FROM (
    SELECT 
        Department,
        EmployeeName,
        Salary,
        HireDate,
        ROW_NUMBER() OVER (PARTITION BY Department ORDER BY Salary DESC) as RankBySalary
    FROM dbo.Employees
) RankedEmployees
WHERE RankBySalary = 1
ORDER BY Department
===============================

Do you want to (e)xecute this query, provide (f)eedback to refine it, or (c)ancel? (e/f/c): e

===== QUERY RESULTS =====
Query executed successfully. 5 rows returned.

+------------+---------------+----------+------------+
| Department | EmployeeName  |   Salary | HireDate   |
+============+===============+==========+============+
| Finance    | Jane Smith    | 95000.00 | 2018-03-15 |
| HR         | Tim Johnson   | 75000.00 | 2020-01-10 |
| IT         | Mary Williams | 98000.00 | 2017-05-22 |
| Marketing  | Bob Miller    | 82000.00 | 2019-07-08 |
| Sales      | John Davis    | 92000.00 | 2016-11-14 |
+------------+---------------+----------+------------+
==========================

Query log saved successfully to logs/queries/query_20230901_152412.json

===== RESULT EXPLANATION =====
The results show the highest paid employee in each department along with their hire date. There are 5 departments in total:

- In Finance, Jane Smith has the highest salary at $95,000 and was hired on March 15, 2018.
- In HR, Tim Johnson earns the most at $75,000 and joined on January 10, 2020.
- In IT, Mary Williams is the top earner with $98,000 and has been with the company since May 22, 2017.
- In Marketing, Bob Miller makes $82,000 and started on July 8, 2019.
- In Sales, John Davis has the highest salary at $92,000 and was hired on November 14, 2016.

Mary Williams from IT has the highest overall salary among all the top-earning employees across departments.
==============================
```

## Diagnostics and Special Commands

The application includes several special commands:

- `/diagnose` - Run comprehensive table access diagnostics
- `/refresh_schema` - Refresh the table schema
- `/history` - View a list of all queries executed in the current session

### Query History

The query history feature helps you keep track of all queries executed during the session:

```
===== QUERY HISTORY =====
1. [2023-09-01 15:24:12] Show me all employees with a salary over $50,000
   SQL: SELECT EmployeeName, Department, Salary FROM dbo.Employees WHERE Salary > 50000 ORDER...
   Iterations: 1, Success: True

2. [2023-09-01 15:32:45] Show me the highest paid employee in each department
   SQL: SELECT Department, EmployeeName, Salary, HireDate FROM (SELECT Department, EmployeeName...
   Iterations: 2, Success: True
=======================
```

## Query Logging

All queries and results are automatically saved to `logs/queries/` as JSON files for future reference. Each log includes:

- The original natural language query
- All SQL iterations and feedback
- The final SQL query that was executed
- Query results
- Timestamps

This allows you to track how queries evolve over time and maintain a record of all database interactions.

## Security Considerations

This application implements several security features:

1. **Single table access**: Queries are restricted to the configured table
2. **Query validation**: All SQL queries are shown for user approval before execution
3. **Transaction safety**: INSERT/UPDATE/DELETE tests use transactions with rollback
4. **Error tracing**: Detailed error logs help diagnose issues without exposing sensitive information
5. **SQL injection prevention**: Structured query generation reduces the risk of SQL injection

## Connection Issues

If you encounter connection issues:

1. Verify your server name or IP address is correct
2. Ensure the SQL Server is running and accepts remote connections
3. Check firewall settings to allow SQL Server traffic
4. Verify that the ODBC driver specified in your .env file is installed
5. Test connectivity with other tools like SSMS or sqlcmd

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgements

- Original MCP SQL Server Natural Language implementation by [Aman Pachori](https://github.com/Amanp17)
- Built with [Modal Context Protocol (MCP)](https://github.com/microsoft/mcp)
