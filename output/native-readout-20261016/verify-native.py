import json, subprocess, time
from pathlib import Path
base = Path('/Users/zh/Documents/test/ARP/output/native-readout-20261016')
pid = '49404'
def script(name, *args):
    return subprocess.check_output(['osascript', str(base/name), pid, *args], text=True)
def read():
    rows={}
    for line in script('read-layout.applescript').splitlines():
        parts=line.split('\t')
        if len(parts)==6:
            rows[parts[0]]={'text':parts[1], 'x':float(parts[2]), 'y':float(parts[3]), 'width':float(parts[4]), 'height':float(parts[5])}
    return rows
script('click.applescript','开始')
samples=[]
seen=set()
started=time.monotonic()
while time.monotonic()-started < 28:
    rows=read()
    samples.append(rows)
    current=rows.get('CURRENT',{}).get('text')
    if current and current not in seen:
        seen.add(current)
        target=base/('automatic-'+current.replace('.','_')+'.png')
        subprocess.run(['osascript','-e',f'do shell script "/usr/sbin/screencapture -x -o -l 6436 {target}"'],check=True,capture_output=True)
    if {'2.00','6.00','10.00'} <= seen: break
    time.sleep(.2)
valid=[s for s in samples if 'W' in s and 'A' in s and 'CURRENT' in s]
shift=lambda key: max(s[key]['x'] for s in valid)-min(s[key]['x'] for s in valid)
result={'runtime':'Tauri macOS WKWebView', 'pid':int(pid),'windowId':6436,'samples':samples,'currents':sorted(seen), 'unitWShift':shift('W'),'unitAShift':shift('A'),'currentFrameXShift':shift('CURRENT')}
(base/'automatic-checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
assert {'2.00','6.00','10.00'} <= seen, result
assert result['unitWShift']==0 and result['unitAShift']==0 and result['currentFrameXShift']==0, result
print(json.dumps({k:v for k,v in result.items() if k!='samples'},ensure_ascii=False))
