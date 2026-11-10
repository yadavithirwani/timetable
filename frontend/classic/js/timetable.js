// Renders the main CSS Grid timetable visualizer dynamically
function renderTimetableGrid() {
    const container = document.getElementById('grid-container');
    const titleEl = document.getElementById('current-entity-title');
    
    if (!schoolData || !selectedEntityId) {
        container.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 2rem; color: var(--text-muted);">Please configure school data first.</div>';
        return;
    }

    // Determine target entity metadata
    let entityObj = null;
    if (currentViewMode === 'class-view') {
        entityObj = schoolData.classes.find(c => c.id === selectedEntityId);
        titleEl.textContent = `Class ${entityObj?.name || ''} Schedule`;
    } else {
        entityObj = schoolData.teachers.find(t => t.id === selectedEntityId);
        titleEl.textContent = `Teacher ${entityObj?.name || ''} Schedule`;
    }

    // Clear and build headers (column count follows the school's periods per day)
    container.innerHTML = '';
    container.style.gridTemplateColumns = `120px repeat(${schoolData.periods_per_day}, 1fr)`;
    
    // Top-left blank cell
    const cornerCell = document.createElement('div');
    cornerCell.className = 'grid-header';
    cornerCell.textContent = 'Day / Period';
    container.appendChild(cornerCell);

    // Period headers (1 to N)
    for (let p = 1; p <= schoolData.periods_per_day; p++) {
        const header = document.createElement('div');
        header.className = 'grid-header';
        header.textContent = `Period ${p}`;
        container.appendChild(header);
    }

    // Map dictionaries for clean constant-time lookups
    const subDict = {};
    schoolData.subjects.forEach(s => subDict[s.id] = s);
    const teachDict = {};
    schoolData.teachers.forEach(t => teachDict[t.id] = t);
    const classDict = {};
    schoolData.classes.forEach(c => classDict[c.id] = c);
    const roomDict = {};
    schoolData.rooms.forEach(r => roomDict[r.id] = r);

    // Curated soft background colors based on Subject IDs to ensure strong visual excellence
    const colorMap = {
        'sub_math': 'rgba(59, 130, 246, 0.15)',
        'sub_phy': 'rgba(16, 185, 129, 0.15)',
        'sub_chem': 'rgba(245, 158, 11, 0.15)',
        'sub_bio': 'rgba(239, 68, 68, 0.15)',
        'sub_eng': 'rgba(139, 92, 246, 0.15)',
        'sub_hin': 'rgba(236, 72, 153, 0.15)',
        'sub_sst': 'rgba(6, 182, 212, 0.15)',
        'sub_cs': 'rgba(99, 102, 241, 0.15)',
        'sub_pt': 'rgba(107, 114, 128, 0.15)'
    };

    const borderMap = {
        'sub_math': '#3b82f6',
        'sub_phy': '#10b981',
        'sub_chem': '#f59e0b',
        'sub_bio': '#ef4444',
        'sub_eng': '#8b5cf6',
        'sub_hin': '#ec4899',
        'sub_sst': '#06b6d4',
        'sub_cs': '#6366f1',
        'sub_pt': '#9ca3af'
    };

    // Filter relevant assignments for the target entity
    const allAssignments = currentTimetable?.assignments || [];
    const relAssignments = allAssignments.filter(a => {
        if (currentViewMode === 'class-view') return a.class_id === selectedEntityId;
        return a.teacher_id === selectedEntityId;
    });

    // Generate grid rows for each day
    schoolData.days.forEach(dayStr => {
        // Day Label Cell
        const dayCell = document.createElement('div');
        dayCell.className = 'grid-cell grid-header day-label';
        dayCell.textContent = dayStr;
        container.appendChild(dayCell);

        // Period Cells for this day
        for (let p = 1; p <= schoolData.periods_per_day; p++) {
            const cell = document.createElement('div');
            cell.className = 'grid-cell';

            // Find matching slot
            // A slot can hold several rows: co-teachers of one lesson, or several
            // sections of a joint elective. Show all of them in the one cell.
            const matches = relAssignments.filter(a => a.day === dayStr && a.period === p);
            const match = matches[0];

            if (match) {
                const sub = subDict[match.subject_id];
                const rObj = roomDict[match.room_id];
                const sameLesson = allAssignments.filter(a => a.lesson_id && a.lesson_id === match.lesson_id
                    && a.day === dayStr && a.period === p);
                const teacherNames = [...new Set((sameLesson.length ? sameLesson : matches)
                    .map(a => teachDict[a.teacher_id]?.name).filter(Boolean))];
                const classNames = [...new Set((sameLesson.length ? sameLesson : matches)
                    .map(a => classDict[a.class_id]?.name).filter(Boolean))];

                // Set beautiful dynamic border and background styles
                if (colorMap[match.subject_id]) {
                    cell.style.backgroundColor = colorMap[match.subject_id];
                    cell.style.borderColor = borderMap[match.subject_id];
                }

                const contentDiv = document.createElement('div');
                contentDiv.className = 'cell-content';

                const subName = document.createElement('div');
                subName.className = 'subject-name';
                subName.textContent = sub?.name || match.subject_id;
                contentDiv.appendChild(subName);

                if (currentViewMode === 'class-view') {
                    const teachName = document.createElement('div');
                    teachName.className = 'teacher-name';
                    teachName.textContent = teacherNames.join(', ');
                    contentDiv.appendChild(teachName);
                } else {
                    const classNameEl = document.createElement('div');
                    classNameEl.className = 'teacher-name';
                    classNameEl.textContent = `Class ${classNames.join(' + ')}`;
                    contentDiv.appendChild(classNameEl);
                }

                const roomName = document.createElement('div');
                roomName.className = 'room-name';
                roomName.textContent = rObj?.name || '';
                contentDiv.appendChild(roomName);

                cell.appendChild(contentDiv);

                if (sub?.requires_lab) {
                    const badge = document.createElement('span');
                    badge.className = 'cell-badge';
                    badge.textContent = '🔬 Lab Slot';
                    cell.appendChild(badge);
                }
            } else {
                cell.classList.add('empty-cell');
                cell.textContent = 'Free Slot';
            }

            container.appendChild(cell);
        }
    });
}
