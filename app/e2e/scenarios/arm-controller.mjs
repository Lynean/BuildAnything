// The owner's MA-6 arm: a 3D laptop controller where you drag the end of the arm and the joints follow.
// Answers are an informed owner's, written by Claude Code for the owner. The run stops wherever a real judgement is needed.
const JOINTS = `- Base: base_link -> link_Base, origin (-0.0063, -0.0115, 0.0285) m from the parent's frame, axis (0, 0, 1), limits -90..90 deg
- Shoulder: link_Base -> link_Shoulder, origin (0.0048, -0.0245, 0.0245) m, axis (-0.205, -0.979, 0), limits -90..90 deg
- Elbow: link_Shoulder -> link_Elbow, origin (0.0243, -0.0051, 0.0917) m, axis (-0.205, -0.979, 0), limits -90..90 deg
- Wrist pitch: link_Elbow -> link_Wrist_pitch, origin (-0.0716, 0.0396, 0.0372) m, axis (-0.872, 0.182, 0.455), limits -90..90 deg
- Wrist roll: link_Wrist_pitch -> link_Wrist_roll, origin (-0.0488, -0.0101, 0.0260) m, axis (-0.151, -0.983, 0.105), limits -90..90 deg
- Gripper roll: link_Wrist_roll -> link_Gripper_roll, origin (-0.0659, 0.0260, -0.0581) m, axis (-0.888, 0.088, -0.450), limits -90..90 deg`;

export default {
  id: "arm-controller",
  honest: true, // never record a test as passed on someone's word; stop and let Claude Code handle it
  cadZip: "../../assets/ma6-arm/ma6-arm-export.zip",
  simulationFirst: "No hardware yet: the ESP32-C6 comes later",
  project: {
    name: "MA-6 Arm Controller",
    aim: "A 3D control app on my laptop for my MA-6 six-joint metal robot arm. It shows the real arm from its CAD model; I drag the end of the arm and the six joints follow (inverse kinematics within the joint limits). It sends the joint angles to an ESP32-C6 that drives the six MG996R servos.",
    scope: "The arm is already built and mine: MA-6 aluminium brackets, six MG996R servos, a 3D-printed base and a 30 g pen holder on the end. I own an ESP32-C6 DevKitC. The jointed CAD model (URDF and STL meshes) is already imported on the Mechanical screen. No hardware is connected for now, so everything has to be tested on the laptop first. Up to S$60 for anything else needed, such as servo power.",
  },
  concept: [
    "The app's main job is dragging the arm's tip in 3D and seeing the real model follow, then sending those angles to the arm. Must-haves: joint limits respected, smooth moves with a speed limit, a rest pose button, an emergency stop that stops sending and lets the servos go limp, sliders for each joint as a backup, and saving a few named poses. Important: a MuJoCo check of this exact arm found the shoulder MG996R needs up to 84% of its stall torque when the arm is stretched out, so the app must not let the arm reach that far. Keep the shoulder load under about 50% of stall, and show it in the app.",
    "Desktop only, Windows, USB serial to the ESP32-C6. A browser-based 3D view served by a small local Python program is fine with me. That's everything, please go ahead.",
  ],
  fallback: "Use your best engineering judgement and keep it simple and safe. Fixed facts: ESP32-C6 DevKitC over USB serial; six MG996R servos; the arm's URDF and meshes are in this project under mechanics/cad; no hardware is connected yet, so everything must be testable on the laptop. Between options, pick the more reliable one and say why.",
  firstMessages: {
    Mechanical: "The arm is the bought MA-6 kit and is already built. Its jointed CAD export (URDF + 82 STL meshes) is imported on the Mechanical screen. Please match the six MG996R servos to its joints and check them in MuJoCo. No new parts need making or printing. The real end carries a 30 g pen holder.",
    Electrical: "Board: ESP32-C6 DevKitC, which I own. Six MG996R servos, also owned. Please pick how to drive and power them safely: a separate 5–6 V supply with enough current for six MG996R, and a common ground with the ESP32-C6. Tell me what goes where. Nothing is connected yet.",
    Software: `Two programs please. (1) ESP32-C6 firmware (Arduino framework, esp32-c6-devkitc-1) that takes the six joint angles over USB serial and drives the servos, with a speed limit, and stops driving them if the laptop stops talking. (2) The laptop app: a small Python program that opens the serial port and serves a local web page. The page is a 3D view written in JavaScript with three.js from a CDN, showing the real arm loaded from its URDF and meshes. Use the urdf-loader library, or load the STL meshes with their visual origins yourself. The files are at ../../mechanics/cad/src/robot/robot.urdf and ../../mechanics/cad/src/robot/meshes/ relative to the app folder; the meshes are in mm and the URDF scales them by 0.001. Dragging a handle on the end of the arm moves the joints by inverse kinematics within the limits. Also: sliders per joint, a rest pose, an emergency stop and saved poses. Keep the shoulder under 50% of stall: MuJoCo found it reaches 84% (0.77 N·m of 0.92 N·m at 4.8 V) when stretched out. Its gravity load is roughly proportional to how far the arm's centre of mass reaches out horizontally from the shoulder, so refuse poses past that. The joints, all in the URDF:\n${JOINTS}\nEverything must be testable on the laptop with no board connected: a simulated port or a dry-run mode.`,
  },
  datasheets: [
    [/esp32/i, "https://www.espressif.com/sites/default/files/documentation/esp32-c6_datasheet_en.pdf"],
    [/mg996/i, "https://www.electronicoscaldas.com/datasheet/MG996R_Tower-Pro.pdf"],
    [/pca9685/i, "https://cdn-shop.adafruit.com/datasheets/PCA9685.pdf"],
    [/lm2596/i, "https://www.ti.com/lit/ds/symlink/lm2596.pdf"],
    [/xl4015/i, "https://www.alldatasheet.com/datasheet-pdf/pdf/1131977/XLSEMI/XL4015.html"],
  ],
  documents: [],
  noDatasheet: /cable|cord|wire|lead|jumper|header|socket|terminal|supply|adapter|psu|power brick|distribution|bracket|kit|holder/i,
  accept: "The tip follows my drag smoothly in 3D, joint limits are never crossed, the shoulder stays under 50% of stall, and the emergency stop works with no board connected (simulated port).",
  budget: 60,
  budgetMax: 80,
  currency: "SGD",
  ownedBoard: /esp32/i,
  maxSteps: 140,
  maxSpendUsd: 10,
};
