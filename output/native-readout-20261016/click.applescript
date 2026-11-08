on run argv
  set arpPid to (item 1 of argv) as integer
  set buttonName to item 2 of argv
  tell application "System Events"
    set arpProcess to first process whose unix id is arpPid
    set allItems to entire contents of window "Power Test · 读数验证" of arpProcess
    set demoFound to false
    repeat with itemRef in allItems
      try
        if role of itemRef is "AXStaticText" and name of itemRef is "虚拟数据演示 · 不连接仪器，不写入正式数据库" then set demoFound to true
      end try
    end repeat
    if not demoFound then error "Expected the ARP virtual-demo banner before interacting"
    repeat with itemRef in allItems
      try
        if role of itemRef is "AXButton" and name of itemRef is buttonName then
          click itemRef
          return "clicked " & buttonName
        end if
      end try
    end repeat
    error "Button not found: " & buttonName
  end tell
end run
