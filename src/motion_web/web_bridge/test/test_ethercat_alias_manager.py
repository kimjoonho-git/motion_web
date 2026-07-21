from types import SimpleNamespace

import pytest

from motion_web_bridge.ethercat_alias_manager import (
    EthercatAliasError,
    EthercatAliasManager,
)


SLAVES = '''=== Master 0, Slave 0 ===
Alias: 101
Device: Main
State: OP
Identity:
  Vendor Id:       0x0000066f
  Product code:    0x60380004
  Serial number:   0x18050508
  Order number: MADLN05BE
  Device name: MADLN05BE
=== Master 0, Slave 1 ===
Alias: 403
State: OP
Identity:
  Vendor Id:       0x0000066f
  Product code:    0x60380004
  Serial number:   0x18050509
'''


def completed(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_parse_slaves_reads_alias_and_identity():
    slaves = EthercatAliasManager.parse_slaves(SLAVES)
    assert slaves[0]['slave_position'] == 0
    assert slaves[0]['ethercat_alias'] == 101
    assert slaves[0]['vendor_id'] == 0x0000066F
    assert slaves[0]['product_code'] == 0x60380004
    assert slaves[0]['serial_number'] == 0x18050508
    assert slaves[1]['ethercat_alias'] == 403


def test_write_alias_rechecks_identity_before_single_slave_write():
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[1] == 'slaves':
            return completed(stdout=SLAVES)
        return completed()

    manager = EthercatAliasManager(runner=runner)
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
    manager = EthercatAliasManager(runner=lambda *args, **kwargs: completed(stdout=SLAVES))
    with pytest.raises(EthercatAliasError, match='Serial Number'):
        manager.write_alias(0, 202, {
            'ethercat_alias': 101,
            'vendor_id': 0x0000066F,
            'product_code': 0x60380004,
            'serial_number': 123,
        })


def test_write_alias_blocks_duplicate_nonzero_alias():
    manager = EthercatAliasManager(runner=lambda *args, **kwargs: completed(stdout=SLAVES))
    with pytest.raises(EthercatAliasError, match='이미 사용 중'):
        manager.write_alias(0, 403, {
            'ethercat_alias': 101,
            'vendor_id': 0x0000066F,
            'product_code': 0x60380004,
            'serial_number': 0x18050508,
        })
