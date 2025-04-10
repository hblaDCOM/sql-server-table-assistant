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
* **Token Optimization**: Smart caching and context management to minimize API usage
* **Web Interface**: Access the Table Assistant through a browser with IP-based access control

## What is MCP?
MCP (Modal Context Protocol) is a methodology that standardizes how context is bound to LLMs, providing a standard way to connect AI models to different data sources and tools.

## Single Table Mode

This application runs in "Single Table Mode" which provides several advantages:

1. **Enhanced Security**: Access is limited to a single table rather than the entire database
2. **Simpler Permissions**: Users need minimal permissions (just for the specific table)
3. **Focused Experience**: The assistant is specialized for working with just one table
4. **Reduced Risk**: Prevents accidental access to sensitive data in other tables

## Token Optimization

This application implements several strategies to minimize token usage and prevent rate limiting:

1. **Smart Schema Summarization**: Instead of sending the entire table schema to the model, a concise summary is created
2. **Response Caching**: Similar queries and explanations are cached to avoid redundant API calls
3. **Minimal Prompt Design**: System prompts and user instructions are optimized for brevity
4. **Conversation Management**: Only recent and relevant messages are included in the context
5. **Dedicated System Prompts**: Different prompts for different tasks (schema, query generation, explanations)
6. **Selective Result Transmission**: Large result sets are trimmed before being sent to the model
7. **Token Parameter Tuning**: Request parameters like max_tokens are set conservatively

These optimizations allow the application to function smoothly even with large tables and complex queries, while staying within API rate limits.

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

# Web Interface Configuration (only needed if using web interface)
WEB_PORT=5000
ALLOWED_IPS=127.0.0.1,192.168.1.100,10.0.0.5
```

## Running the Table Assistant
Once you've set up your environment and dependencies, you're ready to interact with the Table Assistant.

### Command Line Interface
Execute the following command to start the assistant in command line mode:

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

### Web Interface
To start the web interface, allowing multiple users to access the Table Assistant remotely via browser:

```bash
python web_server.py
```

The web server will start on the port specified in your `.env` file (default: 5000). Users with IP addresses listed in the `ALLOWED_IPS` environment variable can access the Table Assistant by navigating to:

```
http://your-server-ip:5000
```

The web interface provides the same functionality as the command line, but in a more user-friendly format accessible from any device with a web browser.

#### Security Considerations for Web Deployment

When deploying the web interface, especially on a network accessible to multiple users, keep these security considerations in mind:

1. **IP Allowlist**: Strictly limit access to trusted IP addresses
2. **Port Forwarding**: Only forward the web server port to trusted networks
3. **HTTPS**: Consider setting up HTTPS for secure connections (using a reverse proxy like Nginx)
4. **Firewall Rules**: Configure firewall rules to restrict access to the application
5. **Regular Updates**: Keep all dependencies updated to patch security vulnerabilities

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
```

## Deployment Options

The SQL Server Table Assistant can be deployed in several ways depending on your needs:

### 1. Local Installation

The simplest deployment is to run the application locally on your machine. This works well for individual use or small teams sharing a computer.

### 2. Network Deployment

For team access, you can deploy the web interface on a server within your network:

1. Set up the application on a server accessible to your team
2. Configure the `ALLOWED_IPS` in the `.env` file to include the IP addresses of team members
3. Share the URL with authorized users

### 3. Production Deployment Recommendations

For a more robust production deployment:

1. **Use a reverse proxy** (like Nginx) to handle HTTPS and additional security layers
2. **Set up process management** with a tool like supervisord or systemd to ensure the application restarts if it crashes
3. **Implement proper logging** to a dedicated log directory or service
4. **Create a dedicated service account** with limited permissions to run the application
5. **Regular backups** of query logs and other important data

Example Nginx configuration for SSL termination:

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Example systemd service file (save as `/etc/systemd/system/sql-assistant.service`):

```ini
[Unit]
Description=SQL Server Table Assistant Web Interface
After=network.target

[Service]
User=your-service-user
WorkingDirectory=/path/to/application
ExecStart=/usr/bin/python3 web_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgements

- Original MCP SQL Server Natural Language implementation by [Aman Pachori](https://github.com/Amanp17)
- Built with [Modal Context Protocol (MCP)](https://github.com/microsoft/mcp)

