# MuJoCo tab (example)

Shows the project's MuJoCo model from mechanics/sim: what it contains, pictures of it, and how its heaviest bodies
moved. When the simulation harness recorded a run (mechanics/sim/_results/scenario_0.motion.json), the data script
replays those recorded positions. Otherwise it lets the model settle for two seconds with no motors driven. It never
runs the project's controller code. Pictures need OpenGL; when that fails, the numbers still come through.
