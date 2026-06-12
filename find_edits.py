import json
log_file = r'C:\Users\niltk\.gemini\antigravity-ide\brain\ba8bfbcf-3751-4794-8c89-838aaf59d130\.system_generated\logs\transcript.jsonl'
with open(log_file, 'r', encoding='utf-8') as f:
    for line in f:
        try:
            data = json.loads(line)
            if data.get('source') == 'MODEL' and 'tool_calls' in data:
                for tc in data['tool_calls']:
                    if tc['name'] in ['replace_file_content', 'multi_replace_file_content', 'write_to_file']:
                        args = tc.get('args', {})
                        target = args.get('TargetFile', '')
                        if 'index.html' in target:
                            print(f"Step: {data['step_index']}, Tool: {tc['name']}")
                            if 'Instruction' in args:
                                print('  Inst:', args['Instruction'][:100])
        except Exception as e:
            pass
