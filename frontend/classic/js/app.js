// Application Globals
let schoolData = null;
let currentTimetable = null;
let currentViewMode = 'class-view'; // 'class-view' or 'teacher-view'
let selectedEntityId = null;

const API_BASE = location.protocol.startsWith('http') ? '/api' : 'http://localhost:8000/api';

// Drawer Toggles
function toggleSetupDrawer() {
    const drawer = document.getElementById('setup-drawer');
    drawer.classList.toggle('open');
}

function toggleChatDrawer() {
    const drawer = document.getElementById('chat-drawer');
    drawer.classList.toggle('open');
}

// App Initialization
document.addEventListener('DOMContentLoaded', async () => {
    // Restore local API key if present
    const savedKey = localStorage.getItem('gemini_api_key');
    if (savedKey) {
        document.getElementById('input-api-key').value = savedKey;
    }

    // Set up listeners
    document.getElementById('btn-toggle-setup').addEventListener('click', toggleSetupDrawer);
    document.getElementById('btn-toggle-chat').addEventListener('click', toggleChatDrawer);
    
    document.getElementById('btn-generate').addEventListener('click', async () => {
        await triggerGenerateSchedule();
    });

    document.getElementById('btn-save-config').addEventListener('click', () => {
        const key = document.getElementById('input-api-key').value.trim();
        if (key) {
            localStorage.setItem('gemini_api_key', key);
        } else {
            localStorage.removeItem('gemini_api_key');
        }
        toggleSetupDrawer();
        alert('Configuration saved successfully!');
    });

    // View tabs listeners
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            currentViewMode = e.currentTarget.dataset.view;
            populateEntityDropdown();
        });
    });

    document.getElementById('entity-select').addEventListener('change', (e) => {
        selectedEntityId = e.target.value;
        renderTimetableGrid();
    });

    // Initial Fetch
    await fetchSchoolData();
    await fetchSavedTimetable();
});

async function fetchSchoolData() {
    try {
        const res = await fetch(`${API_BASE}/school-data`);
        if (res.ok) {
            schoolData = await res.json();
            updateSetupSummary();
            populateEntityDropdown();
        }
    } catch (err) {
        console.error('Failed to load school configuration:', err);
    }
}

async function fetchSavedTimetable() {
    try {
        const res = await fetch(`${API_BASE}/timetable`);
        if (res.ok) {
            const data = await res.json();
            if (data && data.assignments && data.assignments.length > 0) {
                currentTimetable = data;
                updateStatsBanner();
                renderTimetableGrid();
            }
        }
    } catch (err) {
        console.error('Failed to load saved timetable:', err);
    }
}

function updateSetupSummary() {
    if (!schoolData) return;
    const list = document.getElementById('setup-summary-list');
    list.innerHTML = `
        <li>👨‍🏫 <strong>Teachers:</strong> ${schoolData.teachers?.length || 0} registered</li>
        <li>👨‍🎓 <strong>Classes:</strong> ${schoolData.classes?.length || 0} groups</li>
        <li>📚 <strong>Subjects:</strong> ${schoolData.subjects?.length || 0} defined</li>
        <li>🏫 <strong>Rooms:</strong> ${schoolData.rooms?.length || 0} available</li>
        <li>⏱️ <strong>Periods/Day:</strong> ${schoolData.periods_per_day}</li>
    `;
}

function populateEntityDropdown() {
    if (!schoolData) return;
    const select = document.getElementById('entity-select');
    select.innerHTML = '';

    const list = currentViewMode === 'class-view' ? schoolData.classes : schoolData.teachers;
    if (!list || list.length === 0) return;

    list.forEach((item, idx) => {
        const opt = document.createElement('option');
        opt.value = item.id;
        opt.textContent = item.name;
        select.appendChild(opt);
    });

    // Set selection
    selectedEntityId = select.value;
    renderTimetableGrid();
}

function updateStatsBanner() {
    if (!currentTimetable) return;
    
    const statusEl = document.getElementById('stat-status');
    statusEl.textContent = currentTimetable.success ? 'Optimized' : 'Infeasible';
    statusEl.className = `stat-val ${currentTimetable.success ? 'status-ok' : ''}`;
    statusEl.style.color = currentTimetable.success ? 'var(--success)' : 'var(--danger)';

    document.getElementById('stat-score').textContent = currentTimetable.score?.toFixed(1) || '0.0';
    document.getElementById('stat-count').textContent = currentTimetable.assignments?.length || 0;
    document.getElementById('stat-duration').textContent = `${currentTimetable.metadata?.solve_duration_seconds || 0.0}s`;
}

async function triggerGenerateSchedule() {
    const overlay = document.getElementById('loading-overlay');
    overlay.classList.remove('hidden');

    try {
        const res = await fetch(`${API_BASE}/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ locked_assignments: [] })
        });

        if (res.ok) {
            currentTimetable = await res.json();
            updateStatsBanner();
            renderTimetableGrid();
            if (!currentTimetable.success) {
                const reasons = (currentTimetable.violations || []).slice(0, 12).map(v => '• ' + v).join('\n');
                alert('Could not build a clash-free timetable.\n\n' + (reasons || 'No details available.'));
            }
        } else {
            alert('Generation API failed.');
        }
    } catch (err) {
        console.error('Generation execution failed:', err);
        alert('Server unreachable.');
    } finally {
        overlay.classList.add('hidden');
    }
}
