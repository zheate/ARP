on run argv
  set arpPid to (item 1 of argv) as integer
  set optionName to item 2 of argv
  tell application "System Events"
    set arpProcess to first process whose unix id is arpPid
    set allItems to entire contents of window "Power Test · 读数验证" of arpProcess
    repeat with itemRef in allItems
      if role of itemRef is "AXCheckBox" and name of itemRef is optionName then
        set beforeValue to value of itemRef
        if beforeValue is 0 then click itemRef
        return optionName & ": before=" & beforeValue & ", after=" & (value of itemRef)
      end if
    end repeat
    error "Option not found: " & optionName
  end tell
end run
