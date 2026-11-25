# KiCad checks tab (example)

Runs KiCad's own electrical rule check (ERC) on each schematic and design rule check (DRC) on each board in the
project, and lists the problems. Proposed with exe "kicad-cli", so the person approves exactly that program.
It reads the design files but never changes them; KiCad writes its reports into the tab's own folder.
