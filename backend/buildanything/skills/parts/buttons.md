---
match: button|switch|\bkey\b|\bB1\b
---
# Buttons

- Act the moment a button is pressed, not when it's released. Take the first edge at once and ignore bounce for about 30 ms after it, instead of waiting for the level to settle.
