import assert from 'node:assert/strict';
import test from 'node:test';

import {
  dynamixelMotorIdFromDevice,
  dynamixelScanDeviceForValues,
  dynamixelScanDeviceKey,
} from '../static/js/motor_type_dynamixel.js';

test('Dynamixel identity includes serial port, baudrate, and device ID', () => {
  const first = { port: '/dev/ttyUSB0', baudrate: 1000000, id: 3 };
  const second = { port: '/dev/ttyUSB1', baudrate: 1000000, id: 3 };

  assert.notEqual(dynamixelScanDeviceKey(first), dynamixelScanDeviceKey(second));
  assert.notEqual(dynamixelMotorIdFromDevice(first), dynamixelMotorIdFromDevice(second));
});

test('Dynamixel scan matching never guesses the same ID across two ports', () => {
  const devices = [
    { port: '/dev/ttyUSB0', baudrate: 1000000, id: 3 },
    { port: '/dev/ttyUSB1', baudrate: 1000000, id: 3 },
  ];

  assert.equal(dynamixelScanDeviceForValues({ nodeId: 3 }, devices), null);
  assert.equal(
    dynamixelScanDeviceForValues({
      nodeId: 3,
      serialPort: '/dev/ttyUSB1',
    }, devices),
    devices[1],
  );
});
