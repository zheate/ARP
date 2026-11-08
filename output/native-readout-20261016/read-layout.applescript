on run argv
  set arpPid to (item 1 of argv) as integer
  tell application "System Events"
    set arpProcess to first process whose unix id is arpPid
    set allItems to entire contents of window "Power Test · 读数验证" of arpProcess
    set outputText to ""
    repeat with itemRef in allItems
      try
        set itemRole to role of itemRef
        set itemName to name of itemRef as text
        set rowKind to ""
        if itemRole is "AXStaticText" and itemName is "W @" then set rowKind to "W"
        if itemRole is "AXStaticText" and itemName is "A" then set rowKind to "A"
        if itemRole is "AXGroup" and itemName contains "." then
          set numericName to true
          repeat with c in characters of itemName
            if (c as text) is not in "0123456789.-" then set numericName to false
          end repeat
          if numericName then
            set oldDelimiters to AppleScript's text item delimiters
            set AppleScript's text item delimiters to "."
            set decimals to last text item of itemName
            set AppleScript's text item delimiters to oldDelimiters
            if length of decimals is 3 then set rowKind to "POWER"
            if length of decimals is 2 then set rowKind to "CURRENT"
          end if
        end if
        if rowKind is not "" then
          set {itemX, itemY} to position of itemRef
          set {itemW, itemH} to size of itemRef
          set outputText to outputText & rowKind & tab & itemName & tab & itemX & tab & itemY & tab & itemW & tab & itemH & linefeed
        end if
      end try
    end repeat
    return outputText
  end tell
end run
