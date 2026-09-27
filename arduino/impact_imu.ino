// ImpactIQ — Person 3's impact sensor sketch (Arduino + MPU-6500).
// Detects impacts on-board and prints one machine-readable line per hit:
//   IMPACT,<FRONT|BACK|LEFT|RIGHT>,ax,ay,az,totalG     (g, 9600 baud)
// backend/impact_detector.py reads this, maps board axes to car axes,
// converts to force and posts it to the backend.

#include <Wire.h>

const byte MPU_ADDR = 0x68;
const float IMPACT_CHANGE = 0.50;

float previousG = 1.0;
unsigned long lastImpact = 0;

void setup() {
  Wire.begin();
  Serial.begin(9600);

  // Wake up MPU-6500
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x00);
  Wire.endTransmission();

  delay(100);

  // Accelerometer range = +/-16g
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1C);
  Wire.write(0x18);
  Wire.endTransmission();

  delay(100);

  Serial.println("READY");
}

void loop() {

  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);

  Wire.requestFrom(MPU_ADDR, (byte)6);

  if (Wire.available() >= 6) {

    int16_t rawAx = (Wire.read() << 8) | Wire.read();
    int16_t rawAy = (Wire.read() << 8) | Wire.read();
    int16_t rawAz = (Wire.read() << 8) | Wire.read();

    float ax = rawAx / 2048.0;
    float ay = rawAy / 2048.0;
    float az = rawAz / 2048.0;

    float totalG = sqrt(ax * ax + ay * ay + az * az);
    float suddenChange = abs(totalG - previousG);

    if (suddenChange >= IMPACT_CHANGE &&
        millis() - lastImpact > 1000) {

      String direction;

      // Our calibrated demo orientation:
      //
      // Y- = FRONT
      // Y+ = BACK
      // X+ = LEFT
      // X- = RIGHT

      if (abs(ax) >= abs(ay)) {

        if (ax >= 0) {
          direction = "LEFT";
        } else {
          direction = "RIGHT";
        }

      } else {

        if (ay >= 0) {
          direction = "BACK";
        } else {
          direction = "FRONT";
        }
      }

      // Machine-readable output:
      // IMPACT,DIRECTION,Ax,Ay,Az,TotalG

      Serial.print("IMPACT,");
      Serial.print(direction);
      Serial.print(",");
      Serial.print(ax, 2);
      Serial.print(",");
      Serial.print(ay, 2);
      Serial.print(",");
      Serial.print(az, 2);
      Serial.print(",");
      Serial.println(totalG, 2);

      lastImpact = millis();
    }

    previousG = totalG;
  }

  delay(10);
}
