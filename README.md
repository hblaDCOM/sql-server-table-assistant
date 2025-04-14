# SQL Server Table Assistant

This application lets you interact with a specific SQL Server table using natural language. It provides a clean web interface to:

1. View a preview of the selected table
2. Ask questions in plain English
3. Review, refine, and execute SQL queries 
4. View results with AI-generated explanations

## Key Features

* **Natural Language to SQL**: Ask questions about your data in plain English
* **SQL Query Review**: See the generated SQL and refine it before execution
* **Query Refinement**: Provide feedback to improve generated SQL 
* **Beautiful Tabular Results**: View query results in well-formatted tables
* **AI Explanations**: Get plain English explanations of query results
* **Table Preview**: Automatically see the first 5 rows upon startup

## System Architecture

The application uses a simple two-file architecture:

1. **server.py** - Python backend that:
   - Handles SQL database connections
   - Connects to Azure OpenAI for natural language processing
   - Hosts the Flask web server
   - Processes queries and returns results

2. **index.html** - Clean frontend interface that:
   - Displays the table preview
   - Accepts natural language questions
   - Shows generated SQL with options to execute or refine
   - Displays query results with explanations

## Prerequisites

Before you get started, make sure you have the following:

- Python 3.8+ installed on your machine
- A valid Azure OpenAI API key with access to GPT models
- SQL Server with a table that you want to interact with
- ODBC Driver for SQL Server installed

## Getting Started

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

Create a `.env` file in the root of the project with the following:

```dotenv
# Azure OpenAI Configuration
AZURE_OPENAI_API_KEY=your_azure_openai_api_key
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com
AZURE_OPENAI_API_VERSION=2023-05-15
AZURE_OPENAI_DEPLOYMENT_ID=your-deployment-name

# SQL Server Configuration
MSSQL_SERVER=your_server_name
MSSQL_DATABASE=your_database_name
MSSQL_USERNAME=your_username
MSSQL_PASSWORD=your_password
MSSQL_DRIVER={ODBC Driver 17 for SQL Server}

# Table Configuration
MSSQL_TABLE_SCHEMA=dbo
MSSQL_TABLE_NAME=your_table_name
```

## Running the Application

Start the application with a simple command:

```bash
python server.py
```

The server will:
1. Connect to your SQL database
2. Fetch the table schema and a data preview
3. Start the web server on http://localhost:5000

Open your browser and navigate to:
```
http://localhost:5000
```

## Using the Table Assistant

1. **View Table Preview** - When you first load the page, you'll see a preview of the first 5 rows from your table
2. **Ask a Question** - Type your question in natural language in the input box
3. **Review SQL** - The assistant generates SQL based on your question; review it before proceeding
4. **Execute or Refine** - You can execute the query, refine it with feedback, or cancel
5. **View Results** - See your query results with an AI-generated explanation

## Troubleshooting

If you encounter connection issues:

1. Check your SQL Server connection details in the `.env` file
2. Verify that the ODBC driver specified in the `.env` file is installed on your system
3. Ensure your SQL Server is accessible from your machine and that the user has appropriate permissions
4. Check that the table name and schema are correct

For issues with the Azure OpenAI integration:

1. Verify your API key and endpoint in the `.env` file
2. Ensure your deployment ID corresponds to a valid GPT model
3. Check your API key has sufficient quota remaining

