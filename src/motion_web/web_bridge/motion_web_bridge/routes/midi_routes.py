import asyncio

from fastapi import FastAPI, HTTPException, Request


def register_midi_routes(app: FastAPI, bridge) -> None:
    @app.get('/api/midi-monitor')
    async def midi_monitor_status():
        return await asyncio.to_thread(bridge.midi_monitor_status)

    @app.put('/api/midi-monitor/mapping')
    async def save_midi_monitor_mapping(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.save_midi_monitor_mapping, body)

    @app.post('/api/midi-monitor/banks')
    async def create_midi_bank(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.create_midi_bank, body)

    @app.post('/api/midi-monitor/banks/{bank_id}/select')
    async def select_midi_bank(bank_id: str):
        return await asyncio.to_thread(bridge.select_midi_bank, bank_id)

    @app.put('/api/midi-monitor/banks/{bank_id}')
    async def update_midi_bank(bank_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await asyncio.to_thread(bridge.update_midi_bank, bank_id, body)

    @app.delete('/api/midi-monitor/banks/{bank_id}')
    async def delete_midi_bank(bank_id: str):
        return await asyncio.to_thread(bridge.delete_midi_bank, bank_id)

    @app.post('/api/midi-monitor/banks/file/save')
    async def save_midi_banks_to_file():
        return await asyncio.to_thread(bridge.save_midi_banks_to_file)

    @app.post('/api/midi-monitor/banks/file/load')
    async def load_midi_banks_from_file():
        return await asyncio.to_thread(bridge.load_midi_banks_from_file)

    @app.post('/api/midi-monitor/runtime/reset')
    async def reset_midi_runtime_values():
        return await asyncio.to_thread(bridge.reset_midi_runtime_values)

    @app.post('/api/midi-monitor/device/connect')
    async def connect_midi_device():
        return await asyncio.to_thread(bridge.connect_midi_device)

    @app.post('/api/midi-monitor/device/disconnect')
    async def disconnect_midi_device():
        return await asyncio.to_thread(bridge.disconnect_midi_device)
