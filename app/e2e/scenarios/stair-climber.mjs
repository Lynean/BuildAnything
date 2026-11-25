// The owner's RB4107 stair climber: an existing, built robot set up and imported by Claude Code (code, fabrication pack
// docs, redacted invoices, the open Fusion copy). From here Claude Code plays a non-technical owner: understands concepts
// and logic, can't change anything themselves, and doesn't approve complex requests (those stop the run for a judgement).
export default {
  id: "stair-climber",
  honest: true, // never record a test as passed on someone's word
  cautious: true, // change requests and Fusion changes stop for Claude Code to judge as a non-technical owner
  freshResume: false, // the concept is done now; resuming must not resend it
  project: { name: "RB4107 Stair Climber", aim: "", scope: "" },
  concept: [
    "What I want from this: my evacuation robot carries the seated dummy up the rescue stair (24 to 40 degrees, it's in the scope) and keeps the seat level the whole way. I'd like to see a simulation of it climbing the stair in the app. The buying list should match what we actually bought (the invoices are already read in), the software should start from my TwoMotorSync code and become a proper finished program, and the wiring in Electrical should match the pins my code uses. The robot is already built, so please keep what exists.",
    "I'm not very technical, so please decide the details sensibly and explain them simply. That's everything, please go ahead.",
  ],
  fallback: "I'm not very technical, sorry. The robot is already built as in the fabrication pack, and the wiring is exactly what my code's app_config.h says (it's in the scope). Please use the numbers in the CAD, the BOM in mechanics/docs or my code; if one isn't there, make a careful estimate and tell me it's an estimate. Please don't redesign what's already built.",
  firstMessages: {
    Mechanical: "Please build the AES stair for the simulation at 1:5 scale (it's in the scope: 24 to 40 degrees, steps 6 cm apart along the slope and level at every angle, 40 cm wide; about 8 steps is enough), then simulate my robot climbing it as a tracked climber with the hinge and the levelling seat. The sizes are in the Fusion copy that's open and in the fabrication pack docs in mechanics/docs: the track pulleys are HTD5M 20-tooth and the belts have cleats. Please don't change or save my Fusion design, and nothing new needs making.",
    Electrical: "Everything is already wired the way my code's app_config.h says (the pins are in the scope). Please record the parts we bought (Procurement has the orders, for example the two 150:1 37D gearmotors with encoders and the 5 V DC-DC converter) and draw the connections exactly as the code uses them. Please don't redesign the hardware.",
    Software: "Please start from my TwoMotorSync code in firmware/stair-climber. It's already split into drivers (src/driver), middleware (src/mid) and the app (src/app). Turn it into a production-ready program: keep the same pins and behaviour, but make it robust, with safety stops, timeouts, clear states and no placeholder values left unexplained. Please don't rewrite it from scratch.",
    Procurement: "We already bought nearly everything; the invoices are read in as orders. Please make the buying list match what we actually bought, and only add something if it's really missing.",
  },
  datasheets: [
    [/esp32/i, "https://www.espressif.com/sites/default/files/documentation/esp32-c6_datasheet_en.pdf"],
    [/37d|gearmotor|gear motor/i, "https://www.pololu.com/file/0J1736/pololu-37d-metal-gearmotors-rev-1-2.pdf"],
    [/bno08|bno085|bno080/i, "https://www.ceva-ip.com/wp-content/uploads/BNO080_085-Datasheet.pdf"],
    [/l12|linear actuator/i, "https://www.actuonix.com/assets/images/datasheets/ActuonixL12Datasheet.pdf"],
    [/ssd1306|oled/i, "https://cdn-shop.adafruit.com/datasheets/SSD1306.pdf"],
    [/drv5055/i, "https://www.ti.com/lit/ds/symlink/drv5055.pdf"],
  ],
  documents: [],
  noDatasheet: /cable|cord|wire|lead|jumper|header|socket|terminal|bracket|screw|nut|bolt|belt|pulley|bearing|shaft|profile|knob|ky-040|encoder knob|switch|holder|kit/i,
  accept: "In the simulation the robot climbs the 1:5 AES stair across 24 to 40 degrees with the seat staying level; the buying list matches what we bought; the code is my TwoMotorSync made robust; the wiring matches app_config.h.",
  budget: 1500,
  budgetMax: 1800,
  currency: "SGD",
  ownedBoard: /esp32/i,
  ownAll: true, // the robot is built: every confirmed component is already owned
  maxSteps: 160,
  maxSpendUsd: 9.4,
};
