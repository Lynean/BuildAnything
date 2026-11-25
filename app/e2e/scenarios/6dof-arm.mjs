// A first-year student who only has the idea. Words stay vague on purpose: they don't know the jargon.
export default {
  id: "6dof-arm",
  project: {
    name: "6DOF Arm",
    aim: "I want to build a robot arm with 6 joints that moves using servos, and control it from an app on my computer that I make myself.",
    scope: "I'm a first year uni student and this is my first robot, so I'm not really sure what's included. I can spend about S$150 on parts.",
  },
  // Said to the Concept Partner in order. Anything it asks beyond this gets the fallback.
  concept: [
    "I want it to pick up small light things like a pen or an eraser and move them around my desk. The S$150 is only for the arm parts, I already have a laptop. I also have an ESP32-C6 board plugged in, can I use it? I don't really know how servos work yet.",
    "um maybe like 30 cm? idk what's normal. 6DOF means 6 real joints right? I want 6 real joints and the gripper is extra. Cheaper is more important than 30 cm, it can be shorter.",
    "my uni has 3D printers I can use, so I'll make it myself I guess. I've never done CAD properly though. ok I think that's everything, is it ok to go ahead?",
  ],
  fallback: "I don't really know sorry, please pick whatever is simplest and safest for a beginner.",
  firstMessages: {
    Mechanical: "my uni printer is a normal one I think 200mm ish. a pen is like 10g and an eraser 20g, about 2cm wide. please make all the printable parts in fusion, I don't know CAD.",
    Software: "the board is an ESP32-C6 on COM9, treat it as a devkitc. I want the real app in python so the sliders actually send to the board over usb.",
    Electrical: "the board is an ESP32-C6, treat it as the devkitc. please pick everything I need including power and safety stuff and tell me what goes where.",
  },
  // Where a student would find a datasheet. Anything else, they ask Electrical for a link.
  datasheets: [
    [/esp32/i, "https://www.espressif.com/sites/default/files/documentation/esp32-c6_datasheet_en.pdf"],
    [/mg996/i, "https://www.electronicoscaldas.com/datasheet/MG996R_Tower-Pro.pdf"],
    [/mg90/i, "https://www.electronicoscaldas.com/datasheet/MG90S_Tower-Pro.pdf"],
  ],
  noDatasheet: /cable|cord|wire|lead|jumper|socket|distribution block/i,
  accept: "um if it reaches at least 15 cm that's fine, and I can't spend more than S$170 in total",
  budget: 150,
  budgetMax: 170,
  currency: "SGD",
  ownedBoard: /esp32/i,
  maxSteps: 90,
  maxSpendUsd: 12,
};
