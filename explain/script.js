// Requires data.js (codebaseData) and tutorial.js (tutorialData)

let currentMode = 'tutorial'; // 'tutorial', 'kids', or 'explorer'
let activeIds = {
    tutorial: null,
    kids: null,
    explorer: null
};

function init() {
    if (tutorialData && tutorialData.length > 0) activeIds.tutorial = tutorialData[0].id;
    if (codebaseData && codebaseData.length > 0) {
        activeIds.kids = codebaseData[0].id; // Kids mode now shares codebaseData
        activeIds.explorer = codebaseData[0].id;
    }
    
    setMode('tutorial');
}

function setMode(mode) {
    currentMode = mode;
    
    // Update Toggle Buttons
    document.getElementById('btn-tutorial').className = `mode-btn ${mode === 'tutorial' ? 'active' : ''}`;
    document.getElementById('btn-kids').className = `mode-btn ${mode === 'kids' ? 'active' : ''}`;
    document.getElementById('btn-explorer').className = `mode-btn ${mode === 'explorer' ? 'active' : ''}`;
    
    renderNavigation();
    
    if (mode === 'tutorial') {
        renderTutorialContent(activeIds.tutorial, tutorialData, "Learn how the AutoSys clone operates.");
    } else if (mode === 'kids') {
        renderExplorer(activeIds.kids, true);
    } else {
        renderExplorer(activeIds.explorer, false);
    }
}

function renderNavigation() {
    const navList = document.getElementById('nav-list');
    navList.innerHTML = '';
    
    let dataSource, iconDefault;
    if (currentMode === 'tutorial') {
        dataSource = tutorialData;
        iconDefault = 'ph-book-open';
    } else {
        dataSource = codebaseData; // Both kids and explorer use codebaseData
        iconDefault = currentMode === 'kids' ? 'ph-smiley' : 'ph-folder';
    }
    
    const activeId = activeIds[currentMode];
    
    dataSource.forEach(section => {
        const li = document.createElement('li');
        li.className = 'nav-item';
        
        const button = document.createElement('button');
        button.className = `nav-button ${section.id === activeId ? 'active' : ''}`;
        button.onclick = () => {
            activeIds[currentMode] = section.id;
            renderNavigation();
            
            if (currentMode === 'tutorial') {
                renderTutorialContent(section.id, dataSource, "Learn how the AutoSys clone operates.");
            } else if (currentMode === 'kids') {
                renderExplorer(section.id, true);
            } else {
                renderExplorer(section.id, false);
            }
        };
        
        const iconClass = section.icon || iconDefault;
        const displayTitle = (currentMode === 'kids' && section.kid_title) ? section.kid_title : section.title;
        
        button.innerHTML = `
            <i class="${iconClass} ph-lg"></i>
            ${displayTitle}
        `;
        
        li.appendChild(button);
        navList.appendChild(li);
    });
}

function renderTutorialContent(sectionId, dataSource, subtitle) {
    const section = dataSource.find(s => s.id === sectionId);
    if (!section) return;

    // Header
    const headerEl = document.getElementById('content-header');
    headerEl.style.animation = 'none';
    headerEl.offsetHeight; 
    headerEl.style.animation = 'fadeInDown 0.6s ease forwards';
    headerEl.innerHTML = `
        <h2>${section.title}</h2>
        <p>${subtitle}</p>
    `;

    // Content
    const container = document.getElementById('main-container');
    container.style.display = 'block';
    container.innerHTML = `
        <div class="tutorial-content">
            ${section.content}
        </div>
    `;

    // Re-initialize mermaid if applicable
    if (window.mermaid) {
        try {
            mermaid.init(undefined, document.querySelectorAll('.mermaid'));
        } catch (e) {
            console.error("Mermaid initialization failed", e);
        }
    }
}

function renderExplorer(sectionId, isKidsMode) {
    const section = codebaseData.find(s => s.id === sectionId);
    if (!section) return;

    // Header
    const headerEl = document.getElementById('content-header');
    headerEl.style.animation = 'none';
    headerEl.offsetHeight; 
    headerEl.style.animation = 'fadeInDown 0.6s ease forwards';
    
    const secTitle = isKidsMode ? section.kid_title : section.title;
    const secDesc = isKidsMode ? section.kid_description : section.description;
    
    headerEl.innerHTML = `
        <h2>${secTitle}</h2>
        <p>${secDesc}</p>
    `;

    // Content
    const container = document.getElementById('main-container');
    container.style.display = 'grid';
    container.innerHTML = '';
    
    section.files.forEach((file, index) => {
        const card = document.createElement('div');
        card.className = 'file-card';
        card.style.animationDelay = `${index * 0.05}s`;
        
        let fileIcon = "ph-file-code";
        if (file.name.endsWith(".md") || file.name.endsWith(".txt")) fileIcon = "ph-file-text";
        if (file.name.endsWith(".html") || file.name.endsWith(".css")) fileIcon = "ph-browser";
        if (file.name.endsWith(".toml") || file.name.endsWith(".conf")) fileIcon = "ph-gear";

        let metaHtml = `<span class="tag">${file.type}</span>`;
        if (file.lines) metaHtml += `<span class="tag">${file.lines} Lines</span>`;

        let detailsHtml = '';

        if (file.classes && file.classes.length > 0) {
            let classesList = file.classes.map(cls => {
                let methodsHtml = '';
                if (cls.methods && cls.methods.length > 0) {
                    methodsHtml = `<div style="margin-top: 8px;">` + 
                        cls.methods.map(m => `<span class="method-tag">${m}()</span>`).join('') + 
                        `</div>`;
                }
                return `
                <div class="item-entry">
                    <div class="item-name">class ${cls.name}</div>
                    <div class="item-doc">${escapeHtml(cls.doc)}</div>
                    ${methodsHtml}
                </div>`;
            }).join('');
            
            detailsHtml += `
                <div class="details-section">
                    <h4><i class="ph-cube"></i> Classes</h4>
                    <div class="item-list">${classesList}</div>
                </div>
            `;
        }

        if (file.functions && file.functions.length > 0) {
            let funcsList = file.functions.map(fn => `
                <div class="item-entry">
                    <div class="item-name">def ${fn.name}()</div>
                    <div class="item-doc">${escapeHtml(fn.doc)}</div>
                </div>
            `).join('');
            
            detailsHtml += `
                <div class="details-section">
                    <h4><i class="ph-function"></i> Functions</h4>
                    <div class="item-list">${funcsList}</div>
                </div>
            `;
        }

        const fileDesc = isKidsMode && file.kid_desc ? file.kid_desc : file.description;

        card.innerHTML = `
            <div class="file-header">
                <span class="file-name" title="${file.path}">${file.name}</span>
                <i class="${fileIcon} file-type-icon"></i>
            </div>
            <p class="file-desc">${escapeHtml(fileDesc || '')}</p>
            <div class="file-meta">
                ${metaHtml}
            </div>
            ${detailsHtml}
        `;
        
        container.appendChild(card);
    });
}

function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}

document.addEventListener('DOMContentLoaded', init);
