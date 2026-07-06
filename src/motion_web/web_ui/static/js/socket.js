export class StatusSocket {
  constructor({ onOpen, onMessage, onClose, onError }) {
    this.onOpen = onOpen;
    this.onMessage = onMessage;
    this.onClose = onClose;
    this.onError = onError;
    this.socket = null;
    this.reconnectTimer = null;
  }

  connect() {
    if (this.socket) this.socket.close();
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    this.socket = new WebSocket(`${protocol}://${location.host}/ws/status`);

    this.socket.onopen = () => {
      this.onOpen?.();
    };

    this.socket.onmessage = (event) => {
      this.onMessage?.(JSON.parse(event.data));
    };

    this.socket.onclose = () => {
      this.onClose?.();
      if (!this.reconnectTimer) {
        this.reconnectTimer = setTimeout(() => {
          this.reconnectTimer = null;
          this.connect();
        }, 1200);
      }
    };

    this.socket.onerror = () => {
      this.onError?.();
    };
  }
}
