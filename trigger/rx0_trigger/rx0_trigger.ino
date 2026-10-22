// PC-controlled "button press" for the RX0 II shared shutter-release cable.
//
// Each output drives an optocoupler (e.g. PC817) whose transistor side is wired
// in parallel with the matching contact of the existing analogue release:
//   FOCUS_PIN   -> half-press (focus) contact
//   SHUTTER_PIN -> full-press (shutter) contact
// Arduino pin -> 330R -> opto LED anode, LED cathode -> GND.
// Opto collector/emitter -> across the switch contact (mind polarity: collector
// to the side that measures positive relative to the other with a multimeter).
//
// Serial protocol (115200 baud, newline-terminated):
//   "S"  focus, then shutter, then release. Replies "OK <millis>".
//   "P"  ping. Replies "RX0TRIG".

const int FOCUS_PIN = 2;
const int SHUTTER_PIN = 3;
const unsigned long FOCUS_MS = 300;   // half-press before firing (keep even in MF; it wakes the cameras)
const unsigned long SHUTTER_MS = 200; // how long to hold the full press

void setup() {
  pinMode(FOCUS_PIN, OUTPUT);
  pinMode(SHUTTER_PIN, OUTPUT);
  digitalWrite(FOCUS_PIN, LOW);
  digitalWrite(SHUTTER_PIN, LOW);
  Serial.begin(115200);
}

void fire() {
  digitalWrite(FOCUS_PIN, HIGH);
  delay(FOCUS_MS);
  digitalWrite(SHUTTER_PIN, HIGH);
  unsigned long t = millis();
  delay(SHUTTER_MS);
  digitalWrite(SHUTTER_PIN, LOW);
  digitalWrite(FOCUS_PIN, LOW);
  Serial.print("OK ");
  Serial.println(t);
}

void loop() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd == "S") fire();
  else if (cmd == "P") Serial.println("RX0TRIG");
}
