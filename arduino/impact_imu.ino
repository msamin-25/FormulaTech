/*
 * ImpactIQ — IMU reader for impact detection.
 * Person 3: this is a STARTER template, not a finished sketch — adapt it to
 * whatever IMU you actually have wired up (MPU6050 is assumed below since
 * it's the most common cheap IMU in hackathon kits; swap the library/calls
 * if you're using something else like an LSM6DS3 or an ADXL345).
 *
 * WHAT THIS MUST DO (the contract with the Python side):
 *   Print one line per sample over Serial, comma-separated, in units of g:
 *       ax,ay,az
 *   e.g.:
 *       0.01,-0.02,0.99
 *
 *   Send at roughly 100-200 Hz. That's it — all the impact-detection logic
 *   (thresholding, peak capture, force conversion) lives in
 *   backend/impact_detector.py, not here. Keep this sketch dumb and fast.
 *
 * AXIS CONVENTION — mount the IMU so its axes line up with these, or swap
 * the order/signs below in code so the OUTPUT matches this regardless of
 * physical mounting orientation:
 *   ax -> longitudinal (car's forward/back)
 *   ay -> lateral (car's left/right)
 *   az -> vertical (car's up/down) — will read ~1.0g at rest if flat
 *
 * WIRING (typical MPU6050 on I2C):
 *   MPU6050 VCC -> Arduino 5V (or 3.3V, check your board)
 *   MPU6050 GND -> Arduino GND
 *   MPU6050 SCL -> Arduino SCL (A5 on Uno, 21 on Mega)
 *   MPU6050 SDA -> Arduino SDA (A4 on Uno, 20 on Mega)
 *
 * LIBRARY: Install "Adafruit MPU6050" + "Adafruit Unified Sensor" via the
 * Arduino Library Manager (Sketch -> Include Library -> Manage Libraries).
 */

#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Wire.h>

Adafruit_MPU6050 mpu;

const unsigned long SAMPLE_INTERVAL_MS = 8;  // ~125 Hz
unsigned long lastSampleTime = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial) { delay(10); }

  if (!mpu.begin()) {
    Serial.println("ERROR: MPU6050 not found. Check wiring.");
    while (1) { delay(1000); }
  }

  // +-8g range gives headroom for real impact events without clipping.
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);

  Serial.println("ImpactIQ IMU reader ready.");
  delay(200);
}

void loop() {
  unsigned long now = millis();
  if (now - lastSampleTime < SAMPLE_INTERVAL_MS) {
    return;
  }
  lastSampleTime = now;

  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  // Library returns m/s^2 — convert to g to match the Python side's
  // expected units (see backend/impact_detector.py header comment).
  float ax_g = a.acceleration.x / 9.81;
  float ay_g = a.acceleration.y / 9.81;
  float az_g = a.acceleration.z / 9.81;

  // If your mounting orientation doesn't match the axis convention above,
  // remap here, e.g.: Serial.print(az_g); Serial.print(","); ...
  Serial.print(ax_g, 4);
  Serial.print(",");
  Serial.print(ay_g, 4);
  Serial.print(",");
  Serial.println(az_g, 4);
}
