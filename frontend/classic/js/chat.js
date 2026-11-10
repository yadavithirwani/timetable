// Chat interaction handler
let chatHistory = [];

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('btn-send-chat').addEventListener('click', sendChatMessage);
    document.getElementById('input-chat').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });
});

async function sendChatMessage() {
    const inputEl = document.getElementById('input-chat');
    const msg = inputEl.value.trim();
    if (!msg) return;

    // Clear input
    inputEl.value = '';

    // Append user message to view
    appendMessage(msg, 'user-msg');

    // Retrieve active API key
    const apiKey = localStorage.getItem('gemini_api_key') || null;

    // Show AI loading bubble
    const loadingId = appendMessage('Thinking and evaluating constraints...', 'ai-msg', true);

    try {
        const res = await fetch(`${API_BASE}/ai/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: msg,
                history: chatHistory,
                api_key: apiKey
            })
        });

        // Remove loading bubble
        removeMessage(loadingId);

        if (res.ok) {
            const result = await res.json();
            
            // Append response text beautifully
            appendMessage(result.response, 'ai-msg');

            // Save basic history context
            chatHistory.push({ role: 'user', text: msg });
            chatHistory.push({ role: 'model', text: result.response });

            // If the intent successfully resulted in a schedule update, re-sync application globals
            if (result.updated_timetable && result.updated_timetable.success) {
                currentTimetable = result.updated_timetable;
                updateStatsBanner();
                renderTimetableGrid();
            }
        } else {
            appendMessage('⚠️ Error communication with API service.', 'ai-msg');
        }
    } catch (err) {
        console.error('Chat processing failed:', err);
        removeMessage(loadingId);
        appendMessage('⚠️ Could not connect to API Server endpoint.', 'ai-msg');
    }
}

function appendMessage(text, className, isTemp = false) {
    const container = document.getElementById('chat-messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${className}`;
    
    // Parse simplified markdown-like bold text for visual pop
    let formattedText = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                            .replace(/\*(.*?)\*/g, '<em>$1</em>')
                            .replace(/\n\n/g, '<br><br>');
                            
    msgDiv.innerHTML = `<p>${formattedText}</p>`;
    
    const id = `msg-${Date.now()}`;
    if (isTemp) msgDiv.id = id;

    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;
    return id;
}

function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}
