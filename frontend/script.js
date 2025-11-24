document.addEventListener('DOMContentLoaded', () => {
    // --- Ingestion Modal Logic (Unchanged) ---
    const ingestTrigger = document.getElementById('ingest-trigger');
    const ingestModal = document.getElementById('ingest-modal');
    const closeModal = document.getElementById('close-modal');
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const fileNameDisplay = document.getElementById('file-name-display');
    const ingestForm = document.getElementById('ingest-form');
    const ingestStatus = document.getElementById('ingest-status');
    const statusText = document.getElementById('status-text');

    ingestTrigger.addEventListener('click', () => {
        ingestModal.classList.remove('hidden');
        requestAnimationFrame(() => ingestModal.classList.add('visible'));
    });

    closeModal.addEventListener('click', hideModal);

    function hideModal() {
        ingestModal.classList.remove('visible');
        setTimeout(() => ingestModal.classList.add('hidden'), 300);
        resetIngestForm();
    }

    dropZone.addEventListener('click', () => fileInput.click());
    
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            fileInput.files = e.dataTransfer.files;
            updateFileName();
        }
    });

    fileInput.addEventListener('change', updateFileName);

    function updateFileName() {
        if (fileInput.files.length > 0) fileNameDisplay.textContent = fileInput.files[0].name;
        else fileNameDisplay.textContent = "";
    }

    function resetIngestForm() {
        ingestForm.reset();
        fileNameDisplay.textContent = "";
        ingestStatus.classList.add('hidden');
        ingestForm.style.display = 'block';
    }

    ingestForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(ingestForm);
        ingestForm.style.display = 'none';
        ingestStatus.classList.remove('hidden');
        statusText.textContent = "Uploading & Queuing...";

        try {
            const response = await fetch('/ingest', { method: 'POST', body: formData });
            if (!response.ok) throw new Error('Upload failed');
            const data = await response.json();
            
            if (data.status === 'queued') {
                statusText.textContent = "Processing...";
                pollStatus(data.job_id);
            } else if (data.status === 'duplicate') {
                statusText.textContent = "Document already exists.";
                setTimeout(hideModal, 2000);
            }
        } catch (error) {
            statusText.textContent = "Error: " + error.message;
            setTimeout(() => {
                ingestForm.style.display = 'block';
                ingestStatus.classList.add('hidden');
            }, 3000);
        }
    });

    async function pollStatus(jobId) {
        const interval = setInterval(async () => {
            try {
                const res = await fetch(`/ingest/${jobId}/status`);
                const job = await res.json();
                if (job.status === 'completed') {
                    clearInterval(interval);
                    statusText.textContent = "Ingestion Complete.";
                    setTimeout(hideModal, 1500);
                } else if (job.status === 'failed') {
                    clearInterval(interval);
                    statusText.textContent = "Ingestion Failed.";
                    setTimeout(resetIngestForm, 2000);
                }
            } catch (e) {
                clearInterval(interval);
                statusText.textContent = "Polling Error.";
            }
        }, 2000);
    }


    // --- CHAT & SEARCH LOGIC ---
    const queryInput = document.getElementById('query-input');
    const chatHistory = document.getElementById('chat-history');
    let isChatMode = false;

    queryInput.addEventListener('keydown', async (e) => {
        if (e.key === 'Enter') {
            const query = queryInput.value.trim();
            if (!query) return;

            // 1. Switch UI Mode
            if (!isChatMode) {
                document.body.classList.add('chat-mode');
                isChatMode = true;
            }

            // 2. Add User Message
            appendMessage('user', query);
            queryInput.value = ''; 

            // 3. Add AI Placeholder (returns references to text & sources divs)
            const { textDiv, sourcesDiv } = appendAIPlaceholder();
            scrollToBottom();

            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query })
                });

                const data = await response.json();
                
                // 4. Typewriter effect (clear 'Thinking...' first)
                textDiv.textContent = "";
                await typeWriter(data.answer, textDiv);
                
                // --- NEW: Render the System Thoughts ---
                // We pass the sourcesDiv parent, because we want it near the sources
                if (data.thoughts && data.thoughts.length > 0) {
                     renderThoughts(data.thoughts, sourcesDiv);
                }
                
                // 5. Render Sources specifically for this message
                renderSources(data.sources, sourcesDiv);
                scrollToBottom();

            } catch (error) {
                textDiv.textContent = "System Error: " + error.message;
            }
        }
    });

    function appendMessage(role, text) {
        const row = document.createElement('div');
        row.className = `chat-row ${role}`;
        
        const bubble = document.createElement('div');
        bubble.className = `chat-bubble ${role}`;
        bubble.textContent = text;

        row.appendChild(bubble);
        chatHistory.appendChild(row);
    }

    function appendAIPlaceholder() {
        const row = document.createElement('div');
        row.className = 'chat-row ai';

        // Text Area
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble ai';
        bubble.textContent = "Thinking...";

        // Sources Area (Hidden initially)
        const sourcesContainer = document.createElement('div');
        sourcesContainer.className = 'sources-grid';

        row.appendChild(bubble);
        row.appendChild(sourcesContainer); // Sources attached to the AI row
        chatHistory.appendChild(row);

        return { textDiv: bubble, sourcesDiv: sourcesContainer };
    }

    function scrollToBottom() {
        setTimeout(() => {
            chatHistory.scrollTop = chatHistory.scrollHeight;
        }, 50);
    }

    function typeWriter(text, element) {
        return new Promise(resolve => {
            let i = 0;
            const speed = 10; 

            function type() {
                if (i < text.length) {
                    element.textContent += text.charAt(i);
                    i++;
                    // Auto scroll every few chars
                    if (i % 20 === 0) scrollToBottom();
                    setTimeout(type, speed);
                } else {
                    resolve();
                }
            }
            type();
        });
    }

    function renderSources(sources, container) {
        if (!sources || sources.length === 0) return;

        sources.forEach(source => {
            const card = document.createElement('div');
            card.className = 'citation-card';
            
            // Truncate
            const snippet = source.content.length > 100 ? source.content.substring(0, 100) + "..." : source.content;
            
            let metaHtml = '';
            if (source.metadata.name) metaHtml += `${source.metadata.name}`;

            card.innerHTML = `
                <span class="citation-title">${metaHtml}</span>
                <div class="citation-meta">Score: ${source.score.toFixed(2)}</div>
                <div style="margin-top:5px; font-style:italic;">"${snippet}"</div>
            `;
            container.appendChild(card);
        });

        container.style.opacity = "1";
    }
});

function renderThoughts(thoughts, container) {
    // Create main container
    const wrapper = document.createElement('div');
    wrapper.className = 'system-thoughts';

    // Header (Clickable)
    const header = document.createElement('div');
    header.className = 'thoughts-header';
    header.innerHTML = `<span>View chain of thought</span><span class="toggle-icon">▶</span>`;
    
    // Content (Hidden by default)
    const content = document.createElement('div');
    content.className = 'thoughts-content';
    
    const logList = document.createElement('ul');
    thoughts.forEach(line => {
        const li = document.createElement('li');
        li.textContent = line;
        
        // Semantic classes based on keywords
        if (line.includes("Rejected") || line.includes("Pruned")) li.classList.add('log-rejected');
        if (line.includes("Selected") || line.includes("Kept")) li.classList.add('log-kept');
        if (line.includes("Re-rank") || line.includes("Vector") || line.includes("Filter")) li.classList.add('log-info');
        
        logList.appendChild(li);
    });
    
    content.appendChild(logList);
    wrapper.appendChild(header);
    wrapper.appendChild(content);
    
    // Toggle Logic with Smooth Animation
    header.addEventListener('click', () => {
        const isExpanded = wrapper.classList.toggle('expanded');
        const icon = header.querySelector('.toggle-icon');
        icon.textContent = isExpanded ? '▼' : '▶';
        
        // Dynamic max-height for smooth animation
        if (isExpanded) {
            content.style.maxHeight = content.scrollHeight + "px";
        } else {
            content.style.maxHeight = "0px";
        }
    });
    
    // Insert before the sources cards start
    container.insertAdjacentElement('beforebegin', wrapper);
}
