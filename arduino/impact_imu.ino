// ImpactIQ — Person 3's impact sensor sketch (Arduino + MPU-6500).
// Reads the MPU-6500 FIFO at 1 kHz (+/-16 g) and detects impacts on-board:
// a change of >= 0.5 g in total acceleration vs ~10 ms earlier, then a
// ~1 s cooldown. Prints one machine-readable line per hit:
//   READY                                              (once, at boot)
//   IMPACT,<FRONT|BACK|LEFT|RIGHT>,ax,ay,az,totalG     (g, 115200 baud)
// On a wiring/sensor fault it prints the reason (e.g. "MPU-6500 not found.")
// and halts. backend/impact_detector.py and the dashboard's "Connect sensor"
// read this, map board axes to car axes, convert to force and run the model.

#include <Wire.h>
#include <math.h>

const byte MPU = 0x68;
const float impactThreshold = 0.5;

float recentG[10];
byte sampleIndex = 0;
byte samplesStored = 0;
unsigned int cooldown = 0;

bool impactWaiting = false;
float impactX, impactY, impactZ, impactG;

void stopWithError(const __FlashStringHelper *message) {
  Serial.println(message);
  while (true) {
    delay(100);
  }
}

void writeRegister(byte address, byte value) {
  Wire.beginTransmission(MPU);
  Wire.write(address);
  Wire.write(value);

  if (Wire.endTransmission() != 0) {
    stopWithError(F("Sensor write failed. Check wiring."));
  }
}

void readRegisters(byte address, byte *buffer, byte length) {
  Wire.beginTransmission(MPU);
  Wire.write(address);

  if (Wire.endTransmission(false) != 0) {
    stopWithError(F("Sensor not responding."));
  }

  if (Wire.requestFrom(MPU, length) != length) {
    stopWithError(F("Could not read sensor data."));
  }

  for (byte i = 0; i < length; i++) {
    buffer[i] = Wire.read();
  }
}

byte readRegister(byte address) {
  byte value;
  readRegisters(address, &value, 1);
  return value;
}

int16_t readAxis(const byte *data) {
  return (int16_t)(((uint16_t)data[0] << 8) | data[1]);
}

void clearBuffer() {
  writeRegister(0x23, 0x00);
  writeRegister(0x6A, 0x00);
  writeRegister(0x6A, 0x04);
  delay(1);

  writeRegister(0x6A, 0x40);
  writeRegister(0x23, 0x08);

  sampleIndex = 0;
  samplesStored = 0;
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(400000);

#if defined(WIRE_HAS_TIMEOUT)
  Wire.setWireTimeout(3000, true);
#endif

  delay(100);

  if (readRegister(0x75) != 0x70) {
    stopWithError(F("MPU-6500 not found."));
  }

  writeRegister(0x6B, 0x80);  // Reset
  delay(100);
  writeRegister(0x6B, 0x01);
  writeRegister(0x6C, 0x00);
  delay(100);

  // 1 kHz sampling, +/-16g range
  writeRegister(0x1A, 0x01);
  writeRegister(0x1B, 0x00);
  writeRegister(0x19, 0x00);
  writeRegister(0x1C, 0x18);
  writeRegister(0x1D, 0x01);
  writeRegister(0x38, 0x10);

  if (readRegister(0x1A) != 0x01 ||
      readRegister(0x19) != 0x00 ||
      readRegister(0x1C) != 0x18 ||
      readRegister(0x1D) != 0x01) {
    stopWithError(F("Sensor setup failed."));
  }

  Serial.println(F("READY"));
  Serial.flush();
  clearBuffer();
}

void loop() {
  byte status = readRegister(0x3A);
  byte bufferSize[2];
  readRegisters(0x72, bufferSize, 2);

  uint16_t bytesAvailable =
      ((uint16_t)bufferSize[0] << 8) | bufferSize[1];

  if ((status & 0x10) || bytesAvailable >= 510) {
    clearBuffer();
    return;
  }

  while (bytesAvailable >= 6) {
    byte data[6];
    readRegisters(0x74, data, 6);
    bytesAvailable -= 6;

    float ax = readAxis(data) / 2048.0f;
    float ay = readAxis(data + 2) / 2048.0f;
    float az = readAxis(data + 4) / 2048.0f;
    float totalG = sqrtf(ax * ax + ay * ay + az * az);

    if (cooldown > 0) {
      cooldown--;
    }

    // Compare with the reading from roughly 10 ms ago.
    if (samplesStored == 10 && cooldown == 0 && !impactWaiting) {
      float change = fabsf(totalG - recentG[sampleIndex]);

      if (change >= impactThreshold) {
        impactX = ax;
        impactY = ay;
        impactZ = az;
        impactG = totalG;
        impactWaiting = true;
        cooldown = 1000;
      }
    }

    recentG[sampleIndex] = totalG;
    sampleIndex = (sampleIndex + 1) % 10;

    if (samplesStored < 10) {
      samplesStored++;
    }
  }

  if (impactWaiting && Serial.availableForWrite() >= 60) {
    const char *direction;

    // Mounted orientation: +X left, +Y back
    if (fabsf(impactX) >= fabsf(impactY)) {
      direction = impactX >= 0 ? "LEFT" : "RIGHT";
    } else {
      direction = impactY >= 0 ? "BACK" : "FRONT";
    }

    Serial.print(F("IMPACT,"));
    Serial.print(direction);
    Serial.print(',');
    Serial.print(impactX, 2);
    Serial.print(',');
    Serial.print(impactY, 2);
    Serial.print(',');
    Serial.print(impactZ, 2);
    Serial.print(',');
    Serial.println(impactG, 2);

    impactWaiting = false;
  }
}
