document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const chatMessages = document.getElementById('chat-messages');
    const userInput = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    const statusIndicator = document.getElementById('status-indicator');
    const statusText = document.getElementById('status-text');
    
    // Connect to Socket.IO server
    const socket = io();
    
    // Connection events
    socket.on('connect', () => {
        statusIndicator.classList.add('connected');
        statusIndicator.classList.remove('disconnected');
        statusText.textContent = 'Connected';
        
        // Add welcome message
        addSystemMessage('Connected to SQL Server Table Assistant. You can now ask questions about your data.');
    });
    
    socket.on('disconnect', () => {
        statusIndicator.classList.remove('connected');
        statusIndicator.classList.add('disconnected');
        statusText.textContent = 'Disconnected';
        
        addSystemMessage('Disconnected from server. Please refresh the page to reconnect.');
    });
    
    // Handle receiving messages from the server
    socket.on('response', (data) => {
        addAssistantMessage(data.text);
    });
    
    // Send button click handler
    sendButton.addEventListener('click', sendMessage);
    
    // Enter key handler for input field
    userInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });
    
    // Function to send a message
    function sendMessage() {
        const message = userInput.value.trim();
        if (message === '') return;
        
        // Add user message to chat
        addUserMessage(message);
        
        // Clear input
        userInput.value = '';
        
        // Send message to server
        socket.emit('query', { query: message });
    }
    
    // Helper functions to add messages to the chat
    function addUserMessage(text) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', 'user');
        messageElement.innerHTML = `<p>${escapeHtml(text)}</p>`;
        chatMessages.appendChild(messageElement);
        scrollToBottom();
    }
    
    function addAssistantMessage(text) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', 'assistant');
        
        // Check if the message contains SQL code
        if (text.includes('===== GENERATED SQL QUERY =====')) {
            // Extract SQL from message
            const sqlMatch = text.match(/===== GENERATED SQL QUERY =====\n([\s\S]*?)\n={29}/);
            if (sqlMatch && sqlMatch[1]) {
                const sqlCode = sqlMatch[1].trim();
                
                // Format message with SQL code highlighting
                const beforeSql = text.split('===== GENERATED SQL QUERY =====')[0];
                const afterSql = text.split('===============================\n')[1] || '';
                
                messageElement.innerHTML = `
                    <p>${formatMessageText(beforeSql)}</p>
                    <p>Generated SQL Query:</p>
                    <div class="sql-code">${escapeHtml(sqlCode)}</div>
                    <p>${formatMessageText(afterSql)}</p>
                `;
            } else {
                messageElement.innerHTML = `<p>${formatMessageText(text)}</p>`;
            }
        } else if (text.includes('```sql')) {
            // Handle markdown SQL code blocks
            const parts = text.split(/(```sql[\s\S]*?```)/g);
            let formattedText = '';
            
            for (const part of parts) {
                if (part.startsWith('```sql')) {
                    const sqlCode = part.replace(/```sql\n?/, '').replace(/\n?```$/, '');
                    formattedText += `<div class="sql-code">${escapeHtml(sqlCode)}</div>`;
                } else if (part.trim()) {
                    formattedText += `<p>${formatMessageText(part)}</p>`;
                }
            }
            
            messageElement.innerHTML = formattedText;
        } else {
            // Regular message
            messageElement.innerHTML = `<p>${formatMessageText(text)}</p>`;
        }
        
        chatMessages.appendChild(messageElement);
        scrollToBottom();
    }
    
    function addSystemMessage(text) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', 'system');
        messageElement.innerHTML = `<p>${escapeHtml(text)}</p>`;
        chatMessages.appendChild(messageElement);
        scrollToBottom();
    }
    
    // Format message text with line breaks
    function formatMessageText(text) {
        return escapeHtml(text)
            .replace(/\n/g, '<br>')
            .replace(/\t/g, '&nbsp;&nbsp;&nbsp;&nbsp;');
    }
    
    // Escape HTML to prevent XSS
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    // Scroll chat to bottom
    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
}); 