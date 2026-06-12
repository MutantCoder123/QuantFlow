
        const DEFAULT_SYSTEM_PROMPT = "You are an elite quantitative analyst AI. Analyze the following realtime market telemetry data and provide actionable trading insights based on the candlestick patterns, RSI, Option Chain IVR, and Order Book Imbalance (OBI). Give me absolute clarity on the probability of a reversal or continuation.";
        
        // Initialization
        if (!localStorage.getItem('llm_system_prompt')) {
            localStorage.setItem('llm_system_prompt', DEFAULT_SYSTEM_PROMPT);
        }

        const btnSettings = document.getElementById('btn-settings');
        const settingsModal = document.getElementById('settings-modal');
        const btnCloseSettings = document.getElementById('btn-close-settings');
        const promptEditor = document.getElementById('prompt-editor');
        const btnSaveSettings = document.getElementById('btn-save-settings');
        const btnResetSettings = document.getElementById('btn-reset-settings');

        // Open Modal
        btnSettings.addEventListener('click', () => {
            promptEditor.value = localStorage.getItem('llm_system_prompt') || DEFAULT_SYSTEM_PROMPT;
            settingsModal.classList.remove('hidden');
        });

        // Close Modal
        btnCloseSettings.addEventListener('click', () => {
            settingsModal.classList.add('hidden');
        });

        // Save Configuration
        btnSaveSettings.addEventListener('click', () => {
            const newValue = promptEditor.value.trim();
            if (newValue) {
                localStorage.setItem('llm_system_prompt', newValue);
                alert("Prompt Configuration Saved Successfully!");
                settingsModal.classList.add('hidden');
            } else {
                alert("Prompt cannot be empty!");
            }
        });

        // Reset Configuration
        btnResetSettings.addEventListener('click', () => {
            if(confirm("Are you sure you want to reset the prompt to its factory default?")) {
                promptEditor.value = DEFAULT_SYSTEM_PROMPT;
                localStorage.setItem('llm_system_prompt', DEFAULT_SYSTEM_PROMPT);
                alert("Prompt Reset to Default!");
            }
        });

        // --- Position Manager Logic ---
        if (!localStorage.getItem('user_positions')) {
            localStorage.setItem('user_positions', JSON.stringify({}));
        }

        const posModal = document.getElementById('position-modal');
        const btnClosePos = document.getElementById('btn-close-pos');
        const posTitle = document.getElementById('pos-modal-title');
        
        function openPositionModal(token) {
            const state = window.latestGlobalState && window.latestGlobalState[token] ? window.latestGlobalState[token] : null;
            const symbol = state ? state.symbol : `Token ${token}`;
            
            posTitle.innerHTML = `📝 Edit Position: ${symbol}`;
            document.getElementById('pos-token').value = token;
            
            // Load existing data if available
            const allPos = JSON.parse(localStorage.getItem('user_positions'));
            const myPos = allPos[token];
            
            if (myPos) {
                document.getElementById('pos-mode').value = myPos.mode || 'Delivery';
                document.getElementById('pos-direction').value = myPos.direction || 'Long';
                document.getElementById('pos-qty').value = myPos.quantity || '';
                document.getElementById('pos-price').value = myPos.entry_price || '';
            } else {
                document.getElementById('pos-mode').value = 'Delivery';
                document.getElementById('pos-direction').value = 'Long';
                document.getElementById('pos-qty').value = '';
                document.getElementById('pos-price').value = '';
            }
            
            posModal.classList.remove('hidden');
        }
        
        btnClosePos.addEventListener('click', () => {
            posModal.classList.add('hidden');
        });
        
        document.getElementById('btn-save-pos').addEventListener('click', () => {
            const token = document.getElementById('pos-token').value;
            const qty = parseFloat(document.getElementById('pos-qty').value);
            const price = parseFloat(document.getElementById('pos-price').value);
            
            if (isNaN(qty) || isNaN(price) || qty <= 0 || price <= 0) {
                alert("Please enter valid positive numbers for Quantity and Price.");
                return;
            }
            
            const posData = {
                mode: document.getElementById('pos-mode').value,
                direction: document.getElementById('pos-direction').value,
                quantity: qty,
                entry_price: price
            };
            
            const allPos = JSON.parse(localStorage.getItem('user_positions'));
            allPos[token] = posData;
            localStorage.setItem('user_positions', JSON.stringify(allPos));
            
            posModal.classList.add('hidden');
            // Flash success notification (simple console log or silent for UX speed)
            console.log(`Position for Token ${token} saved.`);
        });
        
        document.getElementById('btn-clear-pos').addEventListener('click', () => {
            const token = document.getElementById('pos-token').value;
            const allPos = JSON.parse(localStorage.getItem('user_positions'));
            if (allPos[token]) {
                delete allPos[token];
                localStorage.setItem('user_positions', JSON.stringify(allPos));
            }
            posModal.classList.add('hidden');
        });

        // Phase 5.5: Map Option Tokens Logic
        const btnMapTokens = document.getElementById('btn-map-tokens');
        if (btnMapTokens) {
            btnMapTokens.addEventListener('click', () => {
                const originalText = btnMapTokens.innerText;
                btnMapTokens.innerText = "Mapping Tokens (Please Wait...)";
                btnMapTokens.disabled = true;
                btnMapTokens.classList.add('opacity-50', 'cursor-not-allowed');

                fetch(`${apiBase}/api/map-option-tokens`, { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'success') {
                            // Flash brief success notification
                            const alertBox = document.createElement('div');
                            alertBox.className = 'fixed top-4 right-4 bg-purple-600 text-white px-4 py-2 rounded shadow-lg z-50 font-bold text-sm transition-opacity duration-500';
                            alertBox.innerText = "Live Option Tokens Injected into Data Stream.";
                            document.body.appendChild(alertBox);
                            setTimeout(() => {
                                alertBox.style.opacity = '0';
                                setTimeout(() => alertBox.remove(), 500);
                            }, 3000);
                        } else {
                            alert("Mapping failed: " + data.message);
                        }
                    })
                    .catch(error => {
                        console.error('Error mapping tokens:', error);
                        alert('An error occurred while mapping option tokens.');
                    })
                    .finally(() => {
                        btnMapTokens.innerText = originalText;
                        btnMapTokens.disabled = false;
                        btnMapTokens.classList.remove('opacity-50', 'cursor-not-allowed');
                    });
            });
        }
        
        // --- Phase 10: Watchlist Manager Logic ---
        const watchlistModal = document.getElementById('watchlist-modal');
        let currentWatchlist = [];

        async function openWatchlistModal() {
            document.getElementById('watchlist-modal').classList.remove('hidden');
            try {
                const res = await fetch(`${apiBase}/api/watchlist`);
                const data = await res.json();
                currentWatchlist = data.data || [];
                renderWatchlist();
            } catch (e) {
                console.error("Failed to fetch watchlist", e);
            }
        }

        function closeWatchlistModal() {
            document.getElementById('watchlist-modal').classList.add('hidden');
        }

        function renderWatchlist() {
            const list = document.getElementById('watchlist-items');
            list.innerHTML = '';
            currentWatchlist.forEach((item, idx) => {
                const li = document.createElement('li');
                li.className = "flex justify-between items-center bg-slate-800 p-2 rounded text-sm mb-1 border border-slate-700";
                li.innerHTML = `<span><span class="font-bold text-slate-200">${item.symbol}</span> <span class="text-xs text-slate-500 ml-1">(${item.exchange}:${item.token})</span></span>
                                <button onclick="removeWatchlistItem(${idx})" class="text-rose-400 hover:text-rose-300">🗑️</button>`;
                list.appendChild(li);
            });
        }

        function removeWatchlistItem(idx) {
            currentWatchlist.splice(idx, 1);
            renderWatchlist();
        }

        let activeWatchlistStream = null;

        function switchTab(tabId) {
            try {
                const tabDashboard = document.getElementById('tab-dashboard');
                const tabDiscovery = document.getElementById('tab-screener');
                const btnDashboard = document.getElementById('tab-btn-dashboard');
                const btnDiscovery = document.getElementById('tab-btn-screener');
                
                if (tabDashboard) {
                    tabDashboard.classList.add('hidden');
                    tabDashboard.classList.remove('block');
                }
                if (tabDiscovery) {
                    tabDiscovery.classList.add('hidden');
                    tabDiscovery.classList.remove('block');
                }
                if (btnDashboard) {
                    btnDashboard.className = "px-4 py-1.5 text-xs font-bold rounded-md text-slate-400 hover:text-slate-200 transition-all border border-transparent";
                }
                if (btnDiscovery) {
                    btnDiscovery.className = "px-4 py-1.5 text-xs font-bold rounded-md text-slate-400 hover:text-slate-200 transition-all border border-transparent";
                }
                
                const targetTab = document.getElementById(`tab-${tabId}`);
                if (targetTab) {
                    targetTab.classList.remove('hidden');
                    targetTab.classList.add('block');
                }
                
                const targetBtn = document.getElementById(`tab-btn-${tabId}`);
                if (targetBtn) {
                    targetBtn.className = "px-4 py-1.5 text-xs font-bold rounded-md bg-slate-800 text-white shadow transition-all border border-slate-700/50";
                }
            } catch (err) {
                console.error("Error in switchTab: ", err);
            }
        }

        let lastPlaybookStr = '';
        function renderDiscoveryCards(watchlist) {
              try {
                  const grid = document.getElementById('screener-cards');
                  if (!grid) return;
                  grid.innerHTML = '';
                  
                  if (!watchlist || !Array.isArray(watchlist)) {
                      grid.innerHTML = `<div class="col-span-full p-4 bg-amber-900/50 text-amber-300 rounded border border-amber-700">Invalid watchlist data</div>`;
                      return;
                  }
                  
                  if (watchlist.length === 0) {
                     grid.innerHTML = `<div class="col-span-full p-4 bg-amber-900/50 text-amber-300 rounded border border-amber-700">Watchlist is an empty array []</div>`;
                     return;
                }
                
                watchlist.forEach((item, idx) => {
                    try {
                        const card = document.createElement('div');
                        // ADDED display:flex and explicit min-height to FORCE it to show up even if Tailwind is broken
                        card.style.minHeight = "200px";
                        card.style.display = "flex";
                        card.className = "bg-slate-900 border border-slate-700/80 rounded-xl p-5 shadow-lg flex-col justify-between hover:border-indigo-500/50 transition-colors group relative overflow-hidden";
                        
                        card.innerHTML = `
                            <div class="absolute top-0 right-0 w-24 h-24 bg-indigo-500/10 rounded-bl-full -mr-4 -mt-4 transition-transform group-hover:scale-110"></div>
                            <div class="relative z-10">
                                <div class="flex justify-between items-start mb-3">
                                    <h3 class="text-xl font-bold text-white tracking-tight">${item.symbol || 'N/A'}</h3>
                                    <span class="px-2 py-0.5 text-[10px] font-bold rounded bg-indigo-900/40 text-indigo-300 border border-indigo-700/50 uppercase">Top Pick</span>
                                </div>
                                <p class="text-xs text-slate-300 mb-3 leading-relaxed"><strong>Rationale:</strong> ${item.rationale || 'N/A'}</p>
                                <p class="text-xs text-emerald-400 mb-4 bg-emerald-950/30 p-2 rounded border border-emerald-900/50"><strong>Strategy:</strong> ${item.strategy || 'N/A'}</p>
                            </div>
                            <button onclick="addToWatchlistDynamic('${item.token || ''}', '${item.symbol || ''}', '${item.exchange || ''}')" class="mt-auto w-full bg-slate-800 hover:bg-indigo-600 text-white text-xs font-bold py-2 rounded transition-colors border border-slate-700 hover:border-indigo-500 flex justify-center items-center gap-2 z-10 relative">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>
                                Add to Live Watchlist
                            </button>
                        `;
                        grid.appendChild(card);
                    } catch (err) {
                        const errDiv = document.createElement('div');
                        errDiv.className = "text-rose-400 text-xs";
                        errDiv.innerText = "Error rendering " + (item.symbol || 'card') + ": " + err.message;
                        grid.appendChild(errDiv);
                    }
                });
            } catch (err) {
                const grid = document.getElementById('screener-cards');
                if (grid) grid.innerHTML = `<div class="col-span-full text-rose-500 font-bold">FATAL ERROR in renderDiscoveryCards: ${err.message}</div>`;
            }
        }

        async function addToWatchlistDynamic(token, symbol, exchange) {
            try {
                const res = await fetch(`${apiBase}/api/watchlist/add`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ token, symbol, exchange })
                });
                const data = await res.json();
                if(res.ok) {
                    alert(`Successfully added ${symbol} to Live Watchlist! The websocket stream will hot-load it.`);
                } else {
                    alert("Error: " + data.message);
                }
            } catch(e) {
                console.error(e);
                alert("Failed to add to watchlist.");
            }
        }

        async function saveWatchlist() {
            const btn = document.getElementById('btn-save-watchlist');
            btn.innerText = "Saving...";
            try {
                const res = await fetch(`${apiBase}/api/watchlist`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ items: currentWatchlist })
                });
                if(res.ok) {
                    alert("Watchlist updated successfully! Data stream reinitialized.");
                    closeWatchlistModal();
                } else {
                    alert("Failed to save watchlist.");
                }
            } catch (e) {
                alert("Error saving watchlist.");
            } finally {
                btn.innerText = "Save Changes";
            }
        }

        let searchTimeout;
        async function onWatchlistSearch() {
            const query = document.getElementById('watchlist-search').value;
            const resDiv = document.getElementById('watchlist-search-results');
            if (query.length < 2) { resDiv.innerHTML = ''; return; }
            
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(async () => {
                try {
                    const res = await fetch(`${apiBase}/api/search-token?q=${query}`);
                    const data = await res.json();
                    resDiv.innerHTML = '';
                    (data.data || []).forEach(item => {
                        const div = document.createElement('div');
                        div.className = "p-2 hover:bg-slate-700 cursor-pointer text-sm flex justify-between border-b border-slate-700";
                        div.innerHTML = `<span><span class="font-bold text-slate-200">${item.symbol}</span></span> <span class="text-xs text-slate-400">${item.exchange}:${item.token}</span>`;
                        div.onclick = () => {
                            if(!currentWatchlist.find(x => x.token === item.token)) {
                                currentWatchlist.push(item);
                                renderWatchlist();
                            }
                            resDiv.innerHTML = '';
                            document.getElementById('watchlist-search').value = '';
                        };
                        resDiv.appendChild(div);
                    });
                } catch(e) {
                    console.error("Search failed", e);
                }
            }, 300);
        }

        // --- News Engine Controls ---
        let newsStateInterval = null;
        let newsRequestInFlight = false;

        function fetchNewsState() {
            if (newsRequestInFlight) return;
            fetch(`${apiBase}/api/news/state`)
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        document.getElementById('toggle-news-auto').checked = data.is_active;
                        if (document.activeElement !== document.getElementById('news-interval')) {
                            document.getElementById('news-interval').value = data.interval;
                        }
                        if (document.activeElement !== document.getElementById('news-model-select')) {
                            document.getElementById('news-model-select').value = data.model;
                        }
                        
                        const statusElem = document.getElementById('news-status-text');
                        if (data.is_active) {
                            const lastFetch = data.last_fetch_time > 0 ? new Date(data.last_fetch_time * 1000).toLocaleTimeString() : 'Never';
                            statusElem.innerHTML = `<span class="text-emerald-400">Running (Last: ${lastFetch})</span>`;
                        } else {
                            statusElem.innerHTML = `<span class="text-slate-500">Stopped</span>`;
                        }
                    }
                })
                .catch(err => console.error("Error fetching news state:", err));
        }

        function triggerNewsInstant() {
            const model = document.getElementById('news-model-select').value;
            const btn = document.getElementById('btn-news-instant');
            const origHtml = btn.innerHTML;
            btn.innerHTML = '⏳ Fetching...';
            btn.disabled = true;

            fetch(`${apiBase}/api/news/instant`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: model })
            })
            .then(r => r.json())
            .then(data => {
                fetchNewsState();
                setTimeout(() => { btn.innerHTML = origHtml; btn.disabled = false; }, 1000);
            })
            .catch(err => {
                console.error(err);
                btn.innerHTML = origHtml;
                btn.disabled = false;
            });
        }

        function toggleNewsAuto() {
            const isChecked = document.getElementById('toggle-news-auto').checked;
            newsRequestInFlight = true;
            if (isChecked) {
                const model = document.getElementById('news-model-select').value;
                const interval = parseInt(document.getElementById('news-interval').value) || 120;
                fetch(`${apiBase}/api/news/loop/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interval: interval, model: model })
                }).then(() => {
                    newsRequestInFlight = false;
                    fetchNewsState();
                }).catch(() => newsRequestInFlight = false);
            } else {
                fetch(`${apiBase}/api/news/loop/stop`, { method: 'POST' }).then(() => {
                    newsRequestInFlight = false;
                    fetchNewsState();
                }).catch(() => newsRequestInFlight = false);
            }
        }

        async function generateIntradayPlaybook() {
            const btn = document.getElementById('btn-run-discovery');
            const status = document.getElementById('screener-status');
            const empty = document.getElementById('screener-empty');
            const grid = document.getElementById('screener-cards');
            if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }
            if (empty) empty.classList.add('hidden');
            if (grid) grid.innerHTML = '';
            if (status) {
                status.classList.remove('hidden');
                status.classList.add('block');
            }
            try {
                const modelSelect = document.getElementById('news-model-select');
                const model = modelSelect ? modelSelect.value : 'gemini-2.5-flash';
                const res = await fetch(`${apiBase}/api/reasoning/playbook/generate`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({model: model})
                });
                if (res.ok) {
                    showToast("Playbook Generation Triggered", "The AI is compiling the multi-factor matrix in the background. Please wait a few seconds...");
                }
            } catch (e) {
                console.error(e);
                showToast("Error", "Failed to contact API server.", "error");
                if (status) { status.classList.add('hidden'); status.classList.remove('block'); }
                if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
            }
        }

        // --- Database Management Controls (Phase 14.3) ---
        function loadParquetSymbols() {
            fetch(`${apiBase}/api/watchlist`)
                .then(r => r.json())
                .then(data => {
                    const grid = document.getElementById('parquet-symbols-grid');
                    if (!grid) return;
                    const items = data.data || [];
                    if (items.length === 0) {
                        grid.innerHTML = '<span class="text-xs text-slate-500 italic">No symbols in watchlist.</span>';
                        return;
                    }
                    grid.innerHTML = '';
                    const uniqueSymbols = [...new Set(items.map(i => i.symbol.split('-')[0]))];
                    uniqueSymbols.forEach(sym => {
                        if (!sym || sym.toLowerCase() === 'nifty 50') return; // Skip invalid
                        const label = document.createElement('label');
                        label.className = "flex items-center gap-2 cursor-pointer p-1 hover:bg-slate-800 rounded transition-colors";
                        label.innerHTML = `
                            <input type="checkbox" value="${sym}" class="parquet-sym-checkbox w-3 h-3 bg-slate-900 border-slate-700 rounded text-orange-600 focus:ring-orange-500">
                            <span class="text-xs text-slate-300 font-mono-custom">${sym}</span>
                        `;
                        grid.appendChild(label);
                    });
                })
                .catch(err => console.error("Failed to load symbols for parquet:", err));
        }

        function selectAllParquetSymbols() {
            const checkboxes = document.querySelectorAll('.parquet-sym-checkbox');
            const allChecked = Array.from(checkboxes).every(cb => cb.checked);
            checkboxes.forEach(cb => cb.checked = !allChecked);
        }

        function syncEODParquet() {
            const checkboxes = document.querySelectorAll('.parquet-sym-checkbox:checked');
            const symbols = Array.from(checkboxes).map(cb => cb.value);
            
            const btn = document.getElementById('btn-sync-parquet');
            const status = document.getElementById('parquet-sync-status');
            
            if (symbols.length === 0) {
                status.innerHTML = `<span class="text-rose-400">Select at least one symbol.</span>`;
                status.classList.remove('hidden');
                setTimeout(() => status.classList.add('hidden'), 3000);
                return;
            }

            btn.disabled = true;
            btn.innerHTML = '⏳ Syncing...';
            status.innerHTML = `<span class="text-orange-400">Syncing Database...</span>`;
            status.classList.remove('hidden');

            fetch(`${apiBase}/api/admin/sync-parquet`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbols: symbols })
            })
            .then(r => r.json())
            .then(data => {
                btn.innerHTML = '💾 Force Sync EOD Parquet';
                btn.disabled = false;
                status.innerHTML = `<span class="text-emerald-400">Sync Complete. Check terminal logs.</span>`;
                setTimeout(() => status.classList.add('hidden'), 5000);
            })
            .catch(err => {
                console.error(err);
                btn.innerHTML = '💾 Force Sync EOD Parquet';
                btn.disabled = false;
                status.innerHTML = `<span class="text-rose-400">Sync Failed.</span>`;
            });
        }

        // Initialize News Engine
        document.addEventListener('DOMContentLoaded', () => {
            fetchNewsState();
            loadParquetSymbols();
            newsStateInterval = setInterval(fetchNewsState, 5000);
        });
    