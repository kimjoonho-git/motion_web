import os, termios, time, select

def dynamixel_crc(data):
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x8005) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF

def ping_broadcast(port, baudrate):
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except Exception as e:
        print(f"Failed to open {port}: {e}")
        return False
    try:
        speed = getattr(termios, f'B{baudrate}')
        attrs = termios.tcgetattr(fd)
        attrs[0] = termios.IGNPAR
        attrs[1] = 0
        attrs[2] = speed | termios.CS8 | termios.CLOCAL | termios.CREAD
        attrs[3] = 0
        attrs[4] = speed
        attrs[5] = speed
        attrs[6][termios.VTIME] = 0
        attrs[6][termios.VMIN] = 0
        termios.tcflush(fd, termios.TCIOFLUSH)
        termios.tcsetattr(fd, termios.TCSANOW, attrs)

        packet = bytearray([0xFF, 0xFF, 0xFD, 0x00, 0xFE, 0x03, 0x00, 0x01])
        crc = dynamixel_crc(packet)
        packet.extend([crc & 0xFF, (crc >> 8) & 0xFF])

        os.write(fd, bytes(packet))
        
        timeout = 0.2
        deadline = time.time() + timeout
        data = bytearray()
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                chunk = os.read(fd, 1024)
                if chunk:
                    data.extend(chunk)
        
        if data:
            print(f"[{baudrate} bps] Received {len(data)} bytes: {data.hex()}")
            return True
        else:
            print(f"[{baudrate} bps] No response.")
            return False
    finally:
        os.close(fd)

print("Pinging Dynamixel on /dev/ttyUSB0...")
for baud in [1000000, 57600]:
    ping_broadcast('/dev/ttyUSB0', baud)
