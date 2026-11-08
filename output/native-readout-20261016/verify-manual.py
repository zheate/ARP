import json, subprocess
from pathlib import Path
base=Path('/Users/zh/Documents/test/ARP/output/native-readout-20261016'); pid='49404'
def script(name,*args): return subprocess.check_output(['osascript',str(base/name),pid,*args],text=True)
def read():
    candidates={}
    for line in script('read-layout.applescript').splitlines():
        parts=line.split('\t')
        if len(parts)==6:
            candidates.setdefault(parts[0],[]).append({'text':parts[1],'x':float(parts[2]),'y':float(parts[3]),'width':float(parts[4]),'height':float(parts[5])})
    w=candidates['W'][0]
    rows={'W':w}
    for key in ['A','CURRENT','POWER']:
        choices=[r for r in candidates[key] if abs(r['y']-w['y'])<40]
        rows[key]=min(choices,key=lambda r:abs(r['y']-w['y']))
    return rows
samples=[]
for current in ['9','10','99','14']:
    script('set-current.applescript',current); script('click.applescript','加电')
    rows=read(); samples.append(rows)
    assert rows['CURRENT']['text']==f'{float(current):.2f}',rows
    subprocess.run(['osascript','-e',f'do shell script "/usr/sbin/screencapture -x -o -l 6436 {base}/manual-{current}.png"'],check=True,capture_output=True)
shift=lambda key:max(s[key]['x'] for s in samples)-min(s[key]['x'] for s in samples)
result={'runtime':'Tauri macOS WKWebView','pid':int(pid),'windowId':6436,'samples':samples,'unitWShift':shift('W'),'unitAShift':shift('A'),'currentFrameXShift':shift('CURRENT')}
(base/'manual-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
assert result['unitWShift']==0 and result['unitAShift']==0 and result['currentFrameXShift']==0,result
print(json.dumps({k:v for k,v in result.items() if k!='samples'},ensure_ascii=False))
