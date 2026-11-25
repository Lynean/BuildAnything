// A first-year student with a board and a screen already wired up. The wiring goes in the scope, so every department is told.
export default {
  id: "tft-clock",
  project: {
    name: "TFT Clock Game",
    aim: "I want my STM32F401RE Nucleo board to show a clock on a 1.8 inch TFT screen, and also play a simple game on the screen using the blue B1 button on the board.",
    scope: "I'm a first year uni student. I already have everything and it's already wired, so don't change the wiring: PA5 to SCK, PA7 to SDA, PB6 to CS, PC7 to RESET, PA9 to A0, PB10 to the backlight LED, plus GND and 3V3 to VCC. The screen is a 1.8 inch TFT SPI 128x160 V1.1 board. No case or mechanical parts. The laptop should set the clock's time over the USB cable. The board shows up as COM12.",
  },
  concept: [
    "I want the time big on the screen and the game on the same screen. The game is a little dinosaur that jumps over things when I press B1 and it gets faster. Please make up your own dinosaur and obstacles, don't copy the chrome offline game. I don't really know how screens work.",
    "just keep it simple please. the laptop can send the time once when I plug it in, I don't need a battery. ok I think that's everything, is it ok to go ahead?",
  ],
  fallback: "I don't really know sorry, please pick whatever is simplest for a beginner.",
  firstMessages: { Electrical: "the screen already works fine with exactly the wiring in my project description, someone tested it. please just use those pins, I can't take photos or find a datasheet for it." },
  datasheets: [
    [/st7735|tft/i, "https://www.displayfuture.com/Display/datasheet/controller/ST7735.pdf"],
  ],
  documents: [
    [/nucleo/i, "user_manual", "https://www.st.com/resource/en/user_manual/um1724-stm32-nucleo64-boards-mb1136-stmicroelectronics.pdf"],
  ],
  noDatasheet: /cable|cord|wire|lead|jumper|header/i,
  accept: "um if I can see the time and the game works when I press the button that's fine",
  afterBlankScreen: [
    ["Software", "the LCD isn't even on, the backlight stays dark. My wiring already works and PB10 high turns the backlight on, nobody else needs to approve it. Please remove anything that switches the display off and make the real clock and dinosaur jump game run on the screen with the agreed pins, using B1. Print a line on serial when the screen has started."],
  ],
  ownAll: true,
  noMechanical: "It's only a board and a screen on my desk, there's no case or moving parts.",
  ownedBoard: /.*/,
  score: "score_tft.py",
  rehearse: false,
  maxSteps: 70,
  maxSpendUsd: 10,
};
