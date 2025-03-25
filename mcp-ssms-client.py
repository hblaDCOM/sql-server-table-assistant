import asyncio
import os
import re
import json
from dataclasses import dataclass, field
from typing import cast
import sys
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

from openai import OpenAI
client = OpenAI()

# Create server parameters for stdio connection
server_params = StdioServerParameters(
    command="python",          # Executable
    args=["./mcp-ssms-server.py"],  # Command line arguments to run the server script
    env=None,                  # Optional environment variables
)

if os.name == 'nt':
    import msvcrt

    def get_input(prompt: str) -> str:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        buf = []
        while True:
            ch = msvcrt.getwch()
            if ch == '\r':
                sys.stdout.write('\n')
                return ''.join(buf)
            elif ch == '\x1b':
                raise KeyboardInterrupt
            elif ch == '\b':
                if buf:
                    buf.pop()
                    sys.stdout.write('\b \b')
            else:
                buf.append(ch)
                sys.stdout.write(ch)
                sys.stdout.flush()
    
@dataclass
class Chat:
    messages: list[dict] = field(default_factory=list)
    system_prompt: str = (
        "You are a master MS SQL Server assistant. "
        "Your job is to use the tools at your disposal to execute SQL queries "
        "and provide the results to the user. "
        "When you need to execute a SQL query, respond with the following format exactly:\n"
        "TOOL: query_data, ARGS: {\"sql\": \"<YOUR_SQL_QUERY>\"}"
    )

    async def process_query(self, session: ClientSession, query: str) -> None:
        # 1) Gather available tools (for reference only)
        response = await session.list_tools()
        available_tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in response.tools
        ]

        # 2) Build the conversation for OpenAI
        openai_messages = [
            {"role": "system", "content": self.system_prompt},
        ]
        openai_messages.extend(self.messages)
        openai_messages.append({"role": "user", "content": query})

        # 3) Send to OpenAI
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=openai_messages,
            max_tokens=2000,
            temperature=0.0,
        )

        assistant_reply = completion.choices[0].message.content

        self.messages.append({"role": "user", "content": query})
        self.messages.append({"role": "assistant", "content": assistant_reply})

        # 4) Look for a tool call in the assistant reply
        if "TOOL:" in assistant_reply:
            try:
                pattern = r"TOOL:\s*(\w+),\s*ARGS:\s*(\{.*\})"
                match = re.search(pattern, assistant_reply)
                if match:
                    tool_name = match.group(1)
                    tool_args_str = match.group(2)
                    tool_args = json.loads(tool_args_str)

                    # Now call the tool on the server
                    result = await session.call_tool(tool_name, cast(dict, tool_args))
                    tool_text = getattr(result.content[0], "text", "")

                    tool_result_msg = f"Tool '{tool_name}' result:\n{tool_text}"
                    self.messages.append({"role": "system", "content": tool_result_msg})

                    completion_2 = client.chat.completions.create(
                        model="gpt-4",
                        messages=[{"role": "system", "content": self.system_prompt}] + self.messages,
                        max_tokens=1000,
                        temperature=0.0,
                    )
                    final_reply = completion_2.choices[0].message.content
                    print("\nAssistant:", final_reply)
                    self.messages.append({"role": "assistant", "content": final_reply})
                else:
                    print("No valid tool command found in assistant response.")
            except Exception as e:
                print(f"Failed to parse tool usage: {e}")

    async def chat_loop(self, session: ClientSession):
        while True:
            try:
                query = get_input("Enter your Query (Press ESC to Quit): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nExiting...")
                break
            if not query:
                break
            await self.process_query(session, query)

    async def run(self):
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await self.chat_loop(session)

if __name__ == "__main__":
    chat = Chat()
    asyncio.run(chat.run())
