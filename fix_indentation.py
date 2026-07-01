with open("backend/services/unified_chat_engine.py", "r") as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if i >= 991 and i <= 1241: # check exact line numbers
        new_lines.append("    " + line)
    elif i >= 1295 and i <= 1593:
        new_lines.append("    " + line)
    else:
        new_lines.append(line)

# Let's dynamically find the boundaries instead of hardcoding indices
new_lines = []
in_first_else = False
in_second_else = False

for i, line in enumerate(lines):
    # First else
    if i == 990 and line.strip() == "else:":
        new_lines.append(line)
        in_first_else = True
        continue
    
    # End of first else
    if in_first_else and i == 1242 and "wrap_up_nudge_pushed = False" in line:
        in_first_else = False
        new_lines.append(line)
        continue

    # Second else
    if i == 1294 and line.strip() == "else:":
        new_lines.append(line)
        in_second_else = True
        continue
        
    # End of second else
    if in_second_else and i == 1595 and "--- 3+4. Execute and emit results" in line:
        in_second_else = False
        new_lines.append(line)
        continue
        
    if in_first_else or in_second_else:
        if line.strip() == "":
            new_lines.append("\n")
        else:
            new_lines.append("    " + line)
    else:
        new_lines.append(line)

with open("backend/services/unified_chat_engine.py", "w") as f:
    f.writelines(new_lines)
