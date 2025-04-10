document.addEventListener('DOMContentLoaded', () => {
    // DOM elements
    const chatMessages = document.getElementById('chat-messages');
    const userInput = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');
    const statusIndicator = document.getElementById('status-indicator');
    const statusText = document.getElementById('status-text');
    
    // Track if we're waiting for a response
    let waitingForResponse = false;
    let pendingMessage = null;
    
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
        // Check if this is an update to a pending message or a new message
        if (pendingMessage && 
            (data.text.includes('Processing query') || 
             data.text.includes('Waiting for response'))) {
            // Update the pending message with a progress indicator
            pendingMessage.innerHTML = `<p>${escapeHtml(data.text)} <span class="loading-dots"><span>.</span><span>.</span><span>.</span></span></p>`;
        } else {
            // For more substantial responses, create a new message
            addAssistantMessage(data.text);
            // If this was a full response, clear the pending state
            if (!data.text.includes('Processing query') && 
                !data.text.includes('Waiting for response')) {
                waitingForResponse = false;
                pendingMessage = null;
                
                // Re-enable input
                userInput.disabled = false;
                sendButton.disabled = false;
            }
        }
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
        if (message === '' || waitingForResponse) return;
        
        // Add user message to chat
        addUserMessage(message);
        
        // Disable input while waiting for response
        waitingForResponse = true;
        userInput.disabled = true;
        sendButton.disabled = true;
        
        // Create a pending message for the assistant
        pendingMessage = document.createElement('div');
        pendingMessage.classList.add('message', 'assistant', 'pending');
        pendingMessage.innerHTML = `<p>Processing your query <span class="loading-dots"><span>.</span><span>.</span><span>.</span></span></p>`;
        chatMessages.appendChild(pendingMessage);
        scrollToBottom();
        
        // Clear input
        userInput.value = '';
        
        // Send message to server
        socket.emit('query', { query: message });
        
        // Safety timeout - re-enable input after 30 seconds if no response
        setTimeout(() => {
            if (waitingForResponse) {
                waitingForResponse = false;
                userInput.disabled = false;
                sendButton.disabled = false;
                
                // Add error message
                if (pendingMessage) {
                    pendingMessage.innerHTML = `<p>The request took too long to process. Please try again.</p>`;
                    pendingMessage = null;
                } else {
                    addSystemMessage('The request took too long to process. Please try again.');
                }
            }
        }, 30000);
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
        // Replace existing pending message if it exists
        if (pendingMessage) {
            chatMessages.removeChild(pendingMessage);
            pendingMessage = null;
        }
        
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
        } else if (text.includes('===== QUERY RESULTS =====')) {
            // Handle tabular results
            const parts = text.split(/(===== QUERY RESULTS =====[\s\S]*?==========================)/g);
            let formattedText = '';
            
            for (const part of parts) {
                if (part.startsWith('===== QUERY RESULTS =====')) {
                    formattedText += `<div class="query-results">${formatMessageText(part)}</div>`;
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