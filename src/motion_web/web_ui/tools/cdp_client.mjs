/**
 * 의존성 없는 Chrome DevTools Protocol 클라이언트.
 *
 * `ws` 패키지를 쓰지 않는다 · 이 저장소는 프런트엔드 빌드가 없고 node_modules 를
 * 두지 않는다. 검사 도구 하나 때문에 그 규칙을 깨면 도구가 안 돌아가는 날이 온다.
 *
 * 필요한 것만 구현했다 · 클라이언트 마스킹 · 텍스트 프레임 · 이어붙인 프레임.
 * 스크린샷은 수 MB 라 이어붙이기가 실제로 필요하다.
 */
import { createConnection } from 'node:net';
import { createHash, randomBytes } from 'node:crypto';
import { get as httpGet } from 'node:http';

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

export function fetchJson(port, path) {
  return new Promise((resolve, reject) => {
    httpGet({ host: '127.0.0.1', port, path }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        try { resolve(JSON.parse(body)); } catch (error) { reject(error); }
      });
    }).on('error', reject);
  });
}

function encodeFrame(text) {
  const payload = Buffer.from(text, 'utf8');
  const mask = randomBytes(4);
  let header;
  if (payload.length < 126) {
    header = Buffer.from([0x81, 0x80 | payload.length]);
  } else if (payload.length < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x81; header[1] = 0x80 | 126;
    header.writeUInt16BE(payload.length, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81; header[1] = 0x80 | 127;
    header.writeBigUInt64BE(BigInt(payload.length), 2);
  }
  const masked = Buffer.allocUnsafe(payload.length);
  for (let i = 0; i < payload.length; i += 1) masked[i] = payload[i] ^ mask[i % 4];
  return Buffer.concat([header, mask, masked]);
}

/** 버퍼에서 완성된 프레임을 꺼낸다 · 모자라면 null. */
function readFrame(buffer) {
  if (buffer.length < 2) return null;
  const fin = (buffer[0] & 0x80) !== 0;
  const opcode = buffer[0] & 0x0f;
  let length = buffer[1] & 0x7f;
  let offset = 2;
  if (length === 126) {
    if (buffer.length < 4) return null;
    length = buffer.readUInt16BE(2); offset = 4;
  } else if (length === 127) {
    if (buffer.length < 10) return null;
    length = Number(buffer.readBigUInt64BE(2)); offset = 10;
  }
  if (buffer.length < offset + length) return null;
  return {
    fin,
    opcode,
    payload: buffer.subarray(offset, offset + length),
    rest: buffer.subarray(offset + length),
  };
}

export async function connect(webSocketDebuggerUrl) {
  const url = new URL(webSocketDebuggerUrl);
  const key = randomBytes(16).toString('base64');
  const socket = createConnection({
    host: url.hostname,
    port: Number(url.port),
  });
  socket.setNoDelay(true);

  await new Promise((resolve, reject) => {
    socket.once('error', reject);
    socket.once('connect', () => {
      socket.write(
        `GET ${url.pathname} HTTP/1.1\r\n`
        + `Host: ${url.host}\r\n`
        + 'Upgrade: websocket\r\nConnection: Upgrade\r\n'
        + `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`,
      );
    });
    const onData = (chunk) => {
      const text = chunk.toString('latin1');
      const end = text.indexOf('\r\n\r\n');
      if (end < 0) return;
      const expected = createHash('sha1').update(key + GUID).digest('base64');
      if (!text.includes(expected)) {
        reject(new Error('WebSocket 업그레이드 실패'));
        return;
      }
      socket.off('data', onData);
      socket.off('error', reject);
      // 헤더 뒤에 프레임이 붙어 왔을 수 있다
      const leftover = chunk.subarray(Buffer.byteLength(text.slice(0, end + 4), 'latin1'));
      resolve(leftover);
    };
    socket.on('data', onData);
  }).then((leftover) => { if (leftover?.length) socket.unshift(leftover); });

  let buffer = Buffer.alloc(0);
  let assembling = [];
  const pending = new Map();
  const events = [];
  let nextId = 0;

  socket.on('data', (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    for (;;) {
      const frame = readFrame(buffer);
      if (!frame) break;
      buffer = frame.rest;
      if (frame.opcode === 0x8) { socket.end(); return; }
      if (frame.opcode === 0x9 || frame.opcode === 0xa) continue;
      assembling.push(frame.payload);
      if (!frame.fin) continue;
      const text = Buffer.concat(assembling).toString('utf8');
      assembling = [];
      let message;
      try { message = JSON.parse(text); } catch { continue; }
      if (message.id !== undefined && pending.has(message.id)) {
        const { resolve, reject } = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) reject(new Error(message.error.message));
        else resolve(message.result);
      } else if (message.method) {
        events.push(message);
      }
    }
  });

  return {
    send(method, params = {}) {
      const id = (nextId += 1);
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        socket.write(encodeFrame(JSON.stringify({ id, method, params })));
      });
    },
    /** 쌓인 이벤트를 꺼내 비운다. */
    drainEvents(method) {
      const taken = method ? events.filter((e) => e.method === method) : [...events];
      events.length = 0;
      return taken;
    },
    close() { socket.destroy(); },
  };
}
