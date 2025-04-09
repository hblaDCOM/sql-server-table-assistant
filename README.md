# SQL Server Table Assistant - Modal Context Protocol

This application lets you interact with a specific SQL Server table using natural language, leveraging the Modal Context Protocol as a communication layer between LLMs and your data source.

*This project is based on the [mcp-sql-server-natural-lang](https://github.com/Amanp17/mcp-sql-server-natural-lang) repository by [Aman Pachori](https://github.com/Amanp17), with modifications to focus on single table access.*

## Key Features:

* **Talk to Your Table**: Chat with a specific SQL Server table using plain English
* **No-Code Table Operations**: Query, insert, update, and delete data through natural conversations
* **Secure, Limited Access**: Connect to only one table with restricted credentials for enhanced security
* **MCP-Enhanced Accuracy**: Achieve precise table interactions through Modal Context Protocol
* **Context-Aware Conversations**: Maintain context across multiple queries

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
Special commands: /diagnose - Run diagnostics, /refresh_schema - Refresh table schema

Enter your Query: Show me all employees with a salary over $50,000
```

The assistant will:
1. Translate your natural language to a SQL query
2. Show you the query for approval
3. Execute it after your confirmation
4. Return and explain the results

## Diagnostics and Troubleshooting

The application includes built-in diagnostic tools:

- Use `/diagnose` to run comprehensive table access diagnostics
- Use `/refresh_schema` to refresh the table schema
- Check the `logs` directory for detailed log files
- Review permissions if you encounter access issues

## Security Considerations

This application implements several security features:

1. **Single table access**: Queries are restricted to the configured table
2. **Query validation**: All SQL queries are shown for user approval before execution
3. **Transaction safety**: INSERT/UPDATE/DELETE tests use transactions with rollback
4. **Error tracing**: Detailed error logs help diagnose issues without exposing sensitive information

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
