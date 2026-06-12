with open('trading_copilot/templates/index.html', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Change JSON icon to Analyze
text = text.replace('>JSON</button>', '>Analyze</button>')
text = text.replace('>🚀 JSON</button>', '>🚀 Analyze</button>')
text = text.replace('JSON</button>', 'Analyze</button>')
text = text.replace('openJsonModal', 'openJsonModal') # internal name doesn't matter

# 2. Make raw JSON payload bigger
text = text.replace('h-48 overflow-y-auto', 'h-96 overflow-y-auto')

# 3. Move options to header
modal_header_old = '''            <!-- Modal Header -->
            <div class="flex items-center justify-between px-5 py-3 border-b border-slate-800 bg-slate-800/40">
                <h3 id="modal-title" class="text-sm font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
                    <span class="text-xl">🤖</span> Raw LLM Payload
                </h3>
                <div class="flex gap-2">
                    <button id="modal-copy-btn" onclick="copyToClipboard()" class="px-4 py-1.5 text-xs font-semibold rounded bg-indigo-600 text-white hover:bg-indigo-500 transition-colors shadow-lg">
                        Copy
                    </button>
                    <button onclick="closeJsonModal()" class="px-4 py-1.5 text-xs font-semibold rounded bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors shadow-lg">
                        Close
                    </button>
                </div>
            </div>'''

modal_header_new = '''            <!-- Modal Header -->
            <div class="flex flex-col gap-3 px-5 py-3 border-b border-slate-800 bg-slate-800/40">
                <div class="flex items-center justify-between">
                    <h3 id="modal-title" class="text-sm font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
                        <span class="text-xl">🤖</span> AI Reasoning & Telemetry
                    </h3>
                    <div class="flex gap-2">
                        <button id="modal-copy-btn" onclick="copyToClipboard()" class="px-4 py-1.5 text-xs font-semibold rounded bg-indigo-600 text-white hover:bg-indigo-500 transition-colors shadow-lg">
                            Copy
                        </button>
                        <button onclick="closeJsonModal()" class="px-4 py-1.5 text-xs font-semibold rounded bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors shadow-lg">
                            Close
                        </button>
                    </div>
                </div>
                
                <!-- Controls moved to header -->
                <div class="flex flex-wrap items-center gap-4 bg-slate-900/50 p-2 rounded border border-slate-700">
                    <button id="btn-instant-analyze" onclick="triggerInstantAnalysis()" class="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-[10px] uppercase font-bold rounded shadow transition-colors flex items-center gap-2">
                        ⚡ Instant Analyze
                    </button>
                    
                    <div class="flex items-center gap-2 border-l border-slate-700 pl-4">
                        <span class="text-[10px] text-slate-400 font-semibold uppercase">Model:</span>
                        <select id="reasoning-model-select" class="bg-slate-800 text-[10px] text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                            <option value="gemini-3-flash" selected>Gemini 3 Flash</option>
                            <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
                            <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                            <option value="gemini-3.1-pro">Gemini 3.1 Pro</option>
                        </select>
                    </div>
                    
                    <div class="flex items-center gap-2 border-l border-slate-700 pl-4">
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" id="toggle-auto-analyze" class="sr-only peer" onchange="toggleAutoAnalyze()">
                            <div class="w-7 h-4 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-indigo-600"></div>
                            <span class="ml-2 text-[10px] font-semibold text-slate-300 uppercase">Live Auto-Analyze</span>
                        </label>
                    </div>
                    
                    <div class="flex items-center gap-2 border-l border-slate-700 pl-4">
                        <span class="text-[10px] text-slate-400 uppercase">Freq (s):</span>
                        <input type="number" id="auto-analyze-interval" value="90" min="10" class="w-12 bg-slate-800 text-[10px] text-slate-200 border border-slate-700 rounded px-1 py-1 text-center focus:outline-none focus:border-indigo-500">
                    </div>
                </div>
            </div>'''

text = text.replace(modal_header_old, modal_header_new)

# Remove the old controls from the body
old_controls_block = '''                <!-- AI Reasoning Engine Control Panel -->
                <div class="bg-slate-900 border border-slate-700 rounded-lg p-4 shadow-lg">
                    <h4 class="text-sm font-bold text-slate-300 mb-3 uppercase tracking-wider flex items-center gap-2">
                        <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                        AI Reasoning Engine
                    </h4>
                    
                    <div class="flex flex-wrap items-center gap-4 mb-4">
                        <button id="btn-instant-analyze" onclick="triggerInstantAnalysis()" class="px-6 py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold rounded shadow-lg transition-colors flex items-center gap-2">
                            ⚡ Instant Analyze
                        </button>
                        
                        <div class="flex items-center gap-2 border-l border-slate-700 pl-4">
                            <span class="text-xs text-slate-400 font-semibold uppercase">Model:</span>
                            <select id="reasoning-model-select" class="bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 focus:outline-none focus:border-indigo-500">
                                <option value="gemini-3-flash" selected>Gemini 3 Flash</option>
                                <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
                                <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                                <option value="gemini-3.1-pro">Gemini 3.1 Pro</option>
                            </select>
                        </div>
                        
                        <div class="flex items-center gap-2 border-l border-slate-700 pl-4">
                            <label class="relative inline-flex items-center cursor-pointer">
                                <input type="checkbox" id="toggle-auto-analyze" class="sr-only peer" onchange="toggleAutoAnalyze()">
                                <div class="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600"></div>
                                <span class="ml-2 text-xs font-semibold text-slate-300">Live Auto-Analyze</span>
                            </label>
                        </div>
                        
                        <div class="flex items-center gap-2 border-l border-slate-700 pl-4">
                            <span class="text-xs text-slate-400">Freq (s):</span>
                            <input type="number" id="auto-analyze-interval" value="90" min="10" class="w-16 bg-slate-800 text-xs text-slate-200 border border-slate-700 rounded px-2 py-1 text-center focus:outline-none focus:border-indigo-500">
                        </div>
                    </div>
                    
                    <div class="bg-[#0d1117] rounded-md p-4 border border-slate-800 h-64 overflow-y-auto">
                        <pre><code id="reasoning-report-block" class="text-xs font-mono-custom text-slate-300 whitespace-pre-wrap">Awaiting trigger...</code></pre>
                    </div>
                </div>'''

new_controls_block = '''                <!-- AI Reasoning Output -->
                <div class="bg-[#0d1117] rounded-lg p-4 border border-slate-700 h-64 overflow-y-auto shadow-inner">
                    <pre><code id="reasoning-report-block" class="text-xs font-mono-custom text-slate-300 whitespace-pre-wrap">Awaiting trigger...</code></pre>
                </div>'''

text = text.replace(old_controls_block, new_controls_block)

with open('trading_copilot/templates/index.html', 'w', encoding='utf-8') as f:
    f.write(text)

print('Patch applied successfully.')
