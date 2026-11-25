# MATLAB tab (example)

Runs the project's own analysis script (analysis/*.m) with `matlab -batch` and shows the figures it makes and what it
printed. Proposed with exe "matlab", so the person approves exactly that program. For Octave, the same pattern works
with exe "octave" and `["--no-gui", "--eval", ...]`, saving figures with `print`.
