// A first-year student with a car kit and no board plugged in. Tests the laptop bench and the wheeled-machine check, with no hardware.
export default {
  id: "rover",
  project: {
    name: "Desk Rover",
    aim: "I want a small robot car that drives around by itself and stops or turns before it hits things, and shows the distance in front of it on a little screen.",
    scope: "I'm a first year uni student. I bought a cheap 2WD robot car chassis kit: acrylic plate, two yellow TT gear motors with 65 mm wheels, a caster wheel and a 4xAA battery box, so no case or 3D printing needed. I have an Arduino Uno but it isn't plugged in right now, so nothing gets flashed yet. I can spend about S$40 on the other parts.",
  },
  concept: [
    "I want it to drive on my desk and the floor, and turn away when something is about 20 cm in front. Small OLED screen is fine for the distance. Maybe it can climb over a door threshold too, like 1 cm high? I don't really know how motors work.",
    "just keep it simple please, battery is the AA box from the kit. ok I think that's everything, is it ok to go ahead?",
  ],
  fallback: "I don't really know sorry, please pick whatever is simplest for a beginner.",
  firstMessages: {
    Mechanical: "it's the standard 2WD acrylic car kit, the plate is about 21 x 15 cm, wheels are 65 mm across, the whole car with batteries is maybe 400 g. I can't do CAD, please just check it can drive and get over a 1 cm bump.",
    Electrical: "the board is an Arduino Uno R3. please pick a motor driver, distance sensor and small screen that are cheap and tell me what goes where.",
    Software: "the board is an Arduino Uno R3, it's not plugged in right now. please write the firmware so it can be tested on the laptop first.",
  },
  datasheets: [
    [/ssd1306/i, "https://cdn-shop.adafruit.com/datasheets/SSD1306.pdf"],
    [/hc-?sr04/i, "https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf"],
    [/tb6612/i, "https://cdn.sparkfun.com/datasheets/Robotics/TB6612FNG.pdf"],
    [/l298/i, "https://www.st.com/resource/en/datasheet/l298.pdf"],
    [/atmega328|uno/i, "https://ww1.microchip.com/downloads/en/DeviceDoc/Atmel-7810-Automotive-Microcontrollers-ATmega328P_Datasheet.pdf"],
  ],
  documents: [],
  noDatasheet: /cable|cord|wire|lead|jumper|header|battery|holder|switch|chassis|caster|wheel/i,
  accept: "um if it stops before hitting my hand and shows the distance that's fine, and I can't spend more than S$50",
  budget: 40,
  budgetMax: 50,
  currency: "SGD",
  ownedBoard: /uno|atmega328/i,
  maxSteps: 80,
  maxSpendUsd: 8,
};
