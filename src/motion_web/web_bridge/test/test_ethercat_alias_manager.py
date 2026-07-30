from types import SimpleNamespace

import pytest

from motion_web_bridge.ethercat_alias_manager import (
    EthercatAliasError,
    EthercatAliasManager,
)


SLAVES = '''=== Master 0, Slave 0 ===
Device: Main
State: OP
Identity:
  Vendor Id:       0x0000066f
  Product code:    0x60380004
  Revision number: 0x00000001
  Serial number:   0x18050508
  Order number: MADLN05BE
  Device name: MADLN05BE
=== Master 0, Slave 1 ===
State: OP
Identity:
  Vendor Id:       0x0000066f
  Product code:    0x60380004
  Revision number: 0x00000001
  Serial number:   0x18050509
'''


def completed(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def sii_identity(alias, serial_number):
    data = bytearray(32)
    data[8:10] = int(alias).to_bytes(2, 'little')
    data[16:20] = (0x0000066F).to_bytes(4, 'little')
    data[20:24] = (0x60380004).to_bytes(4, 'little')
    data[24:28] = (1).to_bytes(4, 'little')
    data[28:32] = int(serial_number).to_bytes(4, 'little')
    return bytes(data)


def physical_runner(calls=None):
    def runner(command, **kwargs):
        if calls is not None:
            calls.append(command)
        if command[1] == 'slaves':
            return completed(stdout=SLAVES)
        if command[1] == 'sii_read':
            position = int(command[-1])
            aliases = (101, 403)
            serials = (0x18050508, 0x18050509)
            return completed(stdout=sii_identity(aliases[position], serials[position]))
        return completed()
    return runner


def test_parse_slaves_does_not_invent_alias_missing_from_master_output():
    slaves = EthercatAliasManager.parse_slaves(SLAVES)
    assert slaves[0]['slave_position'] == 0
    assert slaves[0]['ethercat_alias'] is None
    assert slaves[0]['vendor_id'] == 0x0000066F
    assert slaves[0]['product_code'] == 0x60380004
    assert slaves[0]['serial_number'] == 0x18050508
    assert slaves[1]['ethercat_alias'] is None


def test_read_slaves_replaces_master_identity_with_direct_sii_values():
    slaves = EthercatAliasManager(runner=physical_runner()).read_slaves()
    assert [slave['ethercat_alias'] for slave in slaves] == [101, 403]
    assert [slave['serial_number'] for slave in slaves] == [0x18050508, 0x18050509]
    assert all(slave['identity_source'] == 'physical_sii' for slave in slaves)
    assert slaves[0]['sii_order_number'] == 'MADLN05BE'
    assert slaves[0]['sii_device_name'] == 'MADLN05BE'


def test_write_alias_rechecks_identity_before_single_slave_write():
    calls = []

    manager = EthercatAliasManager(runner=physical_runner(calls))
    result = manager.write_alias(0, 202, {
        'ethercat_alias': 101,
        'vendor_id': 0x0000066F,
        'product_code': 0x60380004,
        'serial_number': 0x18050508,
    })
    assert calls[-1] == ['ethercat', 'alias', '-p', '0', '202']
    assert result['previous_alias'] == 101
    assert result['new_alias'] == 202


def test_write_alias_blocks_when_selected_device_changed():
    manager = EthercatAliasManager(runner=physical_runner())
    with pytest.raises(EthercatAliasError, match='Serial Number'):
        manager.write_alias(0, 202, {
            'ethercat_alias': 101,
            'vendor_id': 0x0000066F,
            'product_code': 0x60380004,
            'serial_number': 123,
        })


def test_write_alias_blocks_duplicate_nonzero_alias():
    manager = EthercatAliasManager(runner=physical_runner())
    with pytest.raises(EthercatAliasError, match='이미 사용 중'):
        manager.write_alias(0, 403, {
            'ethercat_alias': 101,
            'vendor_id': 0x0000066F,
            'product_code': 0x60380004,
            'serial_number': 0x18050508,
        })
