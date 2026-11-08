on run argv
  set arpPid to (item 1 of argv) as integer
  set targetText to item 2 of argv
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
        if role of itemRef is "AXTextField" and name of itemRef is "设置电流 (A)" then
          set frontmost of arpProcess to true
          set focused of itemRef to true
          repeat 40 times
            if focused of itemRef then exit repeat
            delay 0.025
          end repeat
          if not (focused of itemRef) then error "Current field did not receive focus"
          keystroke "a" using command down
          key up command
          keystroke targetText using {}
          repeat 40 times
            if (value of itemRef as text) is targetText then exit repeat
            delay 0.025
          end repeat
          set enteredText to value of itemRef as text
          key code 48
          if enteredText is not targetText then error "Current entry mismatch: " & enteredText
          return "entered " & enteredText
        end if
      end try
    end repeat
    error "Manual current field not found"
  end tell
end run
